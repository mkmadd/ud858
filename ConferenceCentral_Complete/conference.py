#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

forked from udacity/ud858 by MKM on 2015 May 22

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime, timedelta, time

import endpoints
from protorpc import messages, message_types, remote

from google.appengine.api import memcache, taskqueue
from google.appengine.ext import ndb

from models import ConflictException, Profile, ProfileMiniForm, ProfileForm, \
                   StringMessage, BooleanMessage, Conference, ConferenceForm, \
                   ConferenceForms, ConferenceQueryForm, ConferenceQueryForms, \
                   TeeShirtSize, Session, SessionForm, SessionForms, Speaker, \
                   SpeakerForm, SpeakerForms

from settings import WEB_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID, \
                     ANDROID_AUDIENCE

from utils import getUserId

import logging

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKERS_KEY = "FEATURED_SPEAKERS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

SESS_DEFAULTS = {
    "duration": 30,
    "typeOfSession": ["lecture"]
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1)
)

SESS_TYPE_QUERY_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    type=messages.StringField(2)
)

SESS_SPEAKER_QUERY_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSpeakerKey=messages.StringField(1)
)

SESS_STARTTIME_QUERY_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    date=messages.StringField(2),
    startTime=messages.StringField(3),
    window=messages.IntegerField(4)
)

SESS_DATE_CITY_QUERY_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    date=messages.StringField(1),
    city=messages.StringField(2)
)

SESS_PUZZLE_QUERY_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    type=messages.StringField(1),
    startTime=messages.StringField(2)
)

FEATURED_SPEAKER_REQUEST = endpoints.ResourceContainer(
    websafeConferenceKey=messages.StringField(1),
    websafeSessionKey=messages.StringField(2)
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        request.websafeKey = c_key.urlsafe()    # return the new urlsafe key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )

# - - - Session objects - - - - - - - - - - - - - - - - -
# added by MKM

    def _copySessionToForm(self, sess):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sess, field.name):
                # convert Date to date string
                if field.name in ('date', 'startTime'):
                    setattr(sf, field.name, str(getattr(sess, field.name)))
                # make speaker keys urlsafe
                elif field.name == 'speaker':
                    setattr(sf, field.name, [s.urlsafe() for s in sess.speaker])
                # else just copy
                else:
                    setattr(sf, field.name, getattr(sess, field.name))
            # set urlsafe key
            elif field.name == 'websafeKey':
                setattr(sf, field.name, sess.key.urlsafe())
        sf.check_initialized()
        return sf


    def _createSessionObject(self, request):
        """Create or update Session object, returning SessionForm/request."""
        # get user
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # get conference that session will belong to
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException('No conference found with ' \
                        'key: {}'.format(request.websafeConferenceKey)

        # check that user is owner of conference
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # name is a required Session field
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) \
                for field in request.all_fields()}
        del data['websafeConferenceKey']    # conf key isn't part of session
        del data['websafeKey']              # websafe key not part of session

        # add default values if missing (both data model & outbound message)
        for df in SESS_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESS_DEFAULTS[df]
                setattr(request, df, SESS_DEFAULTS[df])

        # convert dates and times from strings to Date/Time objects
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], 
                                             "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'], 
                                                  "%H:%M").time()


        # get speaker keys from websafe keys
        if data['speaker']:
            key_list = []
            for ws_key in data['speaker']:
                s_key = ndb.Key(urlsafe=ws_key)
                if s_key:
                    key_list.append(s_key)
            data['speaker'] = key_list
        
        # allocate session id
        s_id = Session.allocate_ids(size=1, parent=conf.key)[0]
        s_key = ndb.Key(Session, s_id, parent=conf.key)
        data['key'] = s_key

        # create Session
        sess = Session(**data)
        sess.put()
        
        # if speaker is ubiquitous, make an announcement, but do it on own time
        taskqueue.add(
            params={
                'websafeConferenceKey': request.websafeConferenceKey,
                'websafeSessionKey': sess.key.urlsafe()
            },
            url='/tasks/handle_featured_speaker'
        )
        
        return self._copySessionToForm(sess)

    @endpoints.method(SESS_POST_REQUEST, SessionForm, 
            path='conference/{websafeConferenceKey}/sessions/new',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session in conference with key {websafeConferenceKey}."""
        return self._createSessionObject(request)

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/sessions',
            http_method='GET',
            name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return sessions in conference (by websafeConferenceKey)."""
        # get parent Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException('No conference found with ' \
                        'key: {}').format(request.websafeConferenceKey)
        
        # get all sessions with conf as parent
        sess = Session.query(ancestor=conf.key)

        return SessionForms(
            items=[self._copySessionToForm(s) for s in sess]
        )
    
    @endpoints.method(SESS_TYPE_QUERY_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions/query',
                      http_method='GET',
                      name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return sessions of given type in conference with given key."""
        # get parent Conference key from request; bail if not found
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        if not c_key:
            raise endpoints.NotFoundException('No conference found with ' \
                        'key: {}').format(request.websafeConferenceKey)

        # get all sessions with conf as parent and filter on type
        sess = Session.query(ancestor=c_key)
        sess = sess.filter(Session.typeOfSession == request.type)
        
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sess]
        )
    
    
    @endpoints.method(SESS_SPEAKER_QUERY_REQUEST, SessionForms,
                      path='session/query/speaker/{websafeSpeakerKey}',
                      http_method='GET',
                      name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return all sessions with given speaker across all conferences."""
        # get speaker key from request; bail if not found
        s_key = ndb.Key(urlsafe=request.websafeSpeakerKey)
        if not s_key:
            raise endpoints.NotFoundException('No speaker found with ' \
                        'key: {}').format(request.websafeSpeakerKey)
        
        # query for all sessions having that speaker key
        sess = Session.query(Session.speaker == s_key)
        
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sess]
        )

    @ndb.transactional()
    @endpoints.method(SESS_GET_REQUEST, SessionForms,
                      path='session/wish/new/{websafeSessionKey}',
                      http_method='POST',
                      name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add session to user's wish list and return updated list."""
        # get user info
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        prof = ndb.Key(Profile, user_id).get()
        
        # add session key to wishlist if not already there and commit
        s_key = ndb.Key(urlsafe=request.websafeSessionKey)
        if s_key not in prof.sessionWishlist:
            prof.sessionWishlist.append(s_key)
        prof.put()
        
        # get and return all session in updated wishlist
        sess = ndb.get_multi(prof.sessionWishlist)
        
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sess]
        )
        
    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='wishlist/session',
                      http_method='GET',
                      name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Return sessions in users wish list."""
        # get user info
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        prof = ndb.Key(Profile, user_id).get()
        
        # get and return all sessions in updated wishlist
        sess = ndb.get_multi(prof.sessionWishlist)
        
        return SessionForms(
            items = [self._copySessionToForm(s) for s in sess]
        )

# - - - Speaker objects - - - - - - - - - - - - - - - - -
# added by MKM

    def _copySpeakerToForm(self, speaker):
        """Copy relevant fields from Speaker object to SpeakerForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, speaker.key.urlsafe())
        sf.check_initialized()
        return sf


    def _createSpeakerObject(self, request):
        """Create or update Speaker object, returning SpeakerForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # name is a required Speaker field
        if not request.name:
            raise endpoints.BadRequestException("Speaker 'name' field required")

        # copy SpeakerForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) \
                for field in request.all_fields()}
        del data['websafeKey']      # websafeKey not a Speaker attribute

        # allocate Speaker id
        s_id = Speaker.allocate_ids(size=1)[0]
        s_key = ndb.Key(Speaker, s_id)
        data['key'] = s_key

        # create Session, send email to organizer confirming
        # creation of Session & return (modified) SessionForm
        speaker = Speaker(**data)
        speaker.put()

        return self._copySpeakerToForm(speaker)

    @endpoints.method(SpeakerForm, SpeakerForm, 
            path='speaker/new',
            http_method='POST', name='createSpeaker')
    def createSpeaker(self, request):
        """Create new speaker entity."""
        return self._createSpeakerObject(request)

    @endpoints.method(message_types.VoidMessage, SpeakerForms,
            path='speakers',
            http_method='GET',
            name='getSpeakers')
    def getSpeakers(self, request):
        """Return all speakers."""
        return SpeakerForms(
            items=[self._copySpeakerToForm(s) for s in Speaker.query()]
        )


# - - - Queries - - - - - - - - - - - - - - - - - - -
# added by MKM

    @endpoints.method(SESS_STARTTIME_QUERY_REQUEST, SessionForms,
                      path='session/query/start_times/{websafeConferenceKey}',
                      http_method='GET',
                      name='getSessionsWithStartTimesWithin')
    def getSessionsWithStartTimesWithin(self, request):
        """Return all sessions in a conference with start times in window."""
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        if not c_key:
            raise endpoints.NotFoundException('No conference found with ' \
                        'key: {}'.format(request.websafeConferenceKey))
        
        # Expect date in format YYYY-MM-DD
        try:
            date = datetime.strptime(request.date, '%Y-%m-%d').date()
        except ValueError:
            raise endpoints.BadRequestException('Invalid date - '
                        'must be in YYYY-MM-DD format')
        # Expect startTime in format HHMM
        try:
            start = datetime.strptime(request.startTime, '%H%M')
        except ValueError:
            raise endpoints.BadRequestException('Invalid startTime - '
                        'must be in HHMM format')
        # Expect window in integer number of minutes
        try:
            delta = timedelta(minutes=request.window)
        except:
            raise endpoints.BadRequestException('Invalid window value')
        
        # Look for startTimes within window before and after
        before = (start - delta).time()
        after = (start + delta).time()
        # Set bounds so don't wrap into tomorrow or yesterday
        if before > start.time():
            before = time(0, 0)
        if after < start.time():
            after = time(23, 59)
        
        # query for session with given conference as ancestor, then filter on
        # date and time within window
        sess = Session.query(ancestor=c_key)
        sess = sess.filter(Session.date==date)
        sess = sess.filter(Session.startTime >= before,
                           Session.startTime <= after)
        
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sess]
        )

    @endpoints.method(SESS_DATE_CITY_QUERY_REQUEST, SessionForms,
                      path='session/query/date_city',
                      http_method='GET',
                      name='getSessionsByDateAndCity')
    def getSessionsByDateAndCity(self, request):
        """Return all sessions in given city on given date."""
        # Expect date in format YYYY-MM-DD
        try:
            date = datetime.strptime(request.date, '%Y-%m-%d').date()
        except ValueError:
            raise endpoints.BadRequestException('Invalid date - '
                        'must be in YYYY-MM-DD format')

        # get just the keys for conferences in city
        conf_keys = Conference.query(Conference.city==request.city)\
                              .fetch(keys_only=True)

        # get all sessions for conferences in city, filter on date
        # (Guido's advice in http://stackoverflow.com/questions/12440333/
        # ndb-query-on-multiple-parents-given-a-cursor)
        # need to do multiple ancestor queries, do asynchronously
        futures = []
        for c_key in conf_keys:
            futures.append(Session.query(ancestor=c_key)\
                   .filter(Session.date==date)\
                   .fetch_async())
        
        sess = []
        for f in futures:
            sess.extend(f.get_result())
        
        return SessionForms(
            items=[self._copySessionToForm(s) for s in sess]
        )

    @endpoints.method(SESS_PUZZLE_QUERY_REQUEST, SessionForms,
                      path='session/query/puzzle',
                      http_method='GET',
                      name='getSessionsBeforeStartTimeNoType')
    def getSessionsBeforeStartTimeNoType(self, request):
        """Return all sessions not of given type occurring before startTime."""
        # Expect startTime in format HHMM
        try:
            start = datetime.strptime(request.startTime, '%H%M').time()
        except ValueError:
            raise endpoints.BadRequestException('Invalid startTime - '
                        'must be in HHMM format')

        # Can't query for inequalities on two properties, need to do two
        # separate queries and intersect the results
        q = Session.query(Session.typeOfSession!=request.type)\
                   .fetch(keys_only=True)
        r = Session.query(Session.startTime < start).fetch(keys_only=True)
        q = set.intersection(set(q), set(r))
        sess = ndb.get_multi(q)

        return SessionForms(
            items=[self._copySessionToForm(s) for s in sess]
        )

# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, 
                                                    getattr(prof, field.name)))
                # condition added by MKM
                elif field.name == 'sessionWishlist':
                    setattr(pf, field.name, 
                            [sk.urlsafe() for sk in prof.sessionWishlist])
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -
# added by MKM

    @staticmethod
    def _handleFeaturedSpeaker(websafeConferenceKey, websafeSessionKey):
        """Add given speaker to given conference's featured speaker memcache."""
        # get conference and session keys
        conf = ndb.Key(urlsafe=websafeConferenceKey).get()
        sess = ndb.Key(urlsafe=websafeSessionKey).get()
        
        # if this session's speaker has more than one session in this 
        # conference, add them and their sessions to memcache announcement
        for s in sess.speaker:
            # get other sessions in this conference with same speaker
            other_sess = Session.query(ancestor=conf.key)
            other_sess = other_sess.filter(Session.speaker==s)
            
            # if more than one, add announcement
            if other_sess.count() > 1:
                # make unique memcache key of conference and speaker keys
                sp_key = '_'.join(('sk', websafeConferenceKey, s.urlsafe()))
                speaker = s.get()
                # create value to be string of speaker's name and sessions
                val = speaker.name + ' - ' + '; '.join(ss.name for ss \
                            in Session.query(Session.speaker==s))

                memcache.set(sp_key, val)

                # add this speaker's key to this conference's list of speaker 
                # keys (there can be multiple speakers with many sessions)
                mem_key = '_'.join((MEMCACHE_FEATURED_SPEAKERS_KEY, 
                                    websafeConferenceKey))
                speaker_keys = memcache.get(mem_key)
                if speaker_keys:
                    speaker_keys.add(sp_key)
                else:
                    speaker_keys = set([sp_key])
                memcache.set(mem_key, speaker_keys)

    @endpoints.method(CONF_GET_REQUEST, StringMessage,
            path='conference/featuredspeaker/{websafeConferenceKey}',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return featured speaker(s) for given conference from memcache."""
        mem_key = '_'.join((MEMCACHE_FEATURED_SPEAKERS_KEY, 
                           request.websafeConferenceKey))
        speaker_keys = memcache.get(mem_key)    # get keys for speakers
        
        # if keys exist, get values for each speaker
        if speaker_keys:
            speakers = 'Featured Speaker'
            speakers += 's:' if len(speaker_keys) > 1 else ':'    # fix grammar
            for speaker_key in speaker_keys:
                speakers = '\n'.join((speakers, memcache.get(speaker_key)))
        else:
            speakers = ''
            
        return StringMessage(data=speakers)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Session.query()
        # q = Session.query(Session.typeOfSession!="lecture").fetch(keys_only=True)
        # r = Session.query(Session.startTime >= time(19, 0)).fetch(keys_only=True)
        # q = set(q).intersection(set(r))
        # q = ndb.get_multi(q)
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        # q = q.filter(Session.typeOfSession=="workshop")
        # q = q.filter(Session.startTime < time(19, 0))
        # q = q.filter(Session.typeOfSession.IN(['workshop', 'keynote']))
        q = q.filter(Session.highlights=="hope")

        return SessionForms(
            items=[self._copySessionToForm(s) for s in q]# \
                   #if 'workshop' not in s.typeOfSession]
        )


api = endpoints.api_server([ConferenceApi]) # register API
