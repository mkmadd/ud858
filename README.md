Conference Jeeves
=================
Udacity Full Stack Nanodegree Project 4
---------------------------------------

Base project was forked from https://github.com/udacity/ud858.

Course code for Building Scalable Apps with Google App Engine in Python class

App ID:  conf-jeeves

## Task 1: Add Sessions to a Conference

I added sessions as children of conferences.  The rationale was that each  
belonged specifically to a single conference - while the same speaker might  
give the same lecture at multiple conferences, the dates and times would be  
different, so it wouldn't be reusable.  I considered making it a structured  
property, but while structured properties can be nested, only one level can  
be repeated.  Since I wanted to leave the possibility for multiple speakers  
in a session, I went with creating sessions with conferences as parent.

I made speakers an entity, reasoning that unlike sessions, one speaker might  
appear at several different conferences and be reused.  Therefore I made them  
a KeyProperty of Session.  Anyone logged in can make a Speaker or get a list  
of all speakers.

I created two additional endpoint methods:  
* createSpeaker() -- create new Speaker, with fields (name,  
name_first, name_last, title, degrees, biography, institute)
* getSpeakers() -- return list of all speakers, with websafeKeys

When creating a new session, if a speaker is added, it must already exist  
and its web safe key appended to the SessionForm.speaker list.


## Task 2: Add Sessions to User Wishlist

I added a sessionWishlist KeyProperty to the Profile entity.  The KeyProperty  
is repeated, as is the corresponding sessionWishlist StringField in ProfileForm.  

Any session can be added, whether the user has registered for the conference  
or not.  getSessionsInWishlist() returns all sessions in the wishlist across  
all conferences.


## Task 3: Work on indexes and queries

Indexes were automatically created by running all queries locally before  
deploying.

I came up with two additional queries:  
* getSessionsWithStartTimesWithin(websafeConferenceKey, date, startTime,  
window) -- get all sessions in a given conference on a given date and with  
a start time within window number of minutes before or after the given  
startTime.  Formats are:  date='YYYY-MM-DD', startTime='HHMM', window  
is an integer number of minutes.
* getSessionsByDateAndCity(date, city) -- get all sessions, across all  
conferences, occurring on the given date in the given city.  This query  
first gets the keys of all conferences matching the given city, then  
queries all sessions with corresponding ancestors and dates matching  
the given date.  The last query is done asynchronously to try to  
parallelize.

###Query Problem

The main problem with implementing the non-workshop, before 7pm session query  
is that Datastore does not allow non-equality queries on multiple properties at  
once.  So doing a query like Session.query(Session.typeOfSession!='workshop',  
Session.startTime>time(19,0)) is not possible.  (A minor issue is that the '!='  
will not exclude all workshops.  If 'workshop' is only one of an entity's  
multiple types, it will still be returned.)

There are several ways of handling this particular problem.

1) Query on one property, then filter on the second using Python.  Fairly  
straightforward.  One benefit of this method is that the second problem  
can be handled easily and ALL workshops removed if desired.  
	q = Session.query()
	q = q.filter(Session.startTime < time(19, 0))
	return SessionForms(
		items=[self._copySessionToForm(s) for s in q
			   if 'workshop' not in s.typeOfSession]
	)

2) Make two separate queries, fetch only the keys, do a set intersection,  
then get_multi() the result.  This leaves most of the work to the datastore  
and is still fairly straightforward and doesn't mess with the models.  
	q = Session.query(Session.typeOfSession!="lecture").fetch(keys_only=True)
	r = Session.query(Session.startTime >= time(19, 0)).fetch(keys_only=True)
	result_keys = set.intersection(set(q), set(r))
	q = ndb.get_multi(result_keys)
	return SessionForms(
		items=[self._copySessionToForm(s) for s in q]
	)

3) Change the query.  A separate class could be constructed with all  
session types in it.  Every time a session is created, its type is added  
to the list of known session types.  To do the query, get the known list,  
remove the unwanted type, then query using IN and one inequality.  
	q = Session.query()
	q = q.filter(Session.startTime < time(19, 0))
	q = q.filter(Session.typeOfSession.IN(TYPES_MINUS_WORKSHOP))
	return SessionForms(
		items=[self._copySessionToForm(s) for s in q]
	)

4) Change the model.  If the types are enumerable, simply exchange the  
typeOfSession repeated StringProperties for several BooleanProperties,  
one for each type, then query with one equality and one inequality.  
This method also fixes the minor issue of not being able to exclude  
all workshops.  
    q = Session.query()
    q = q.filter(Session.startTime < time(19, 0))
    q = q.filter(Session.workshop == False)
    return SessionForms(
        items=[self._copySessionToForm(s) for s in q]
    )

I went with the second method.  I was unsure if the first method would scale  
as well.  Though in a real system I might just go with the first, and only try  
something else if problems arose.  The feasibility of the third and fourth is  
going to depend on the specific query problem.  This particular query is  
doable, though the fourth restricts types to predefined ones.

I implemented the dual-query/set-intersection method as:  
* getSessionsBeforeStartTimeNoType(type, startTime) -- returns all sessions  
not of the given type and with a startTime occurring before the given  
startTime.

## Task 4: Add a Task

When a new session is created, a task is added to the default push queue.  If  
the speaker is speaking at more than one session, a memcache entry is made or  
updated for that conference and speaker, with a value of the speaker's name  
and the names of all the sessions he's speaking at for that conference.  That  
memcache entry key is then added to the set of speaker keys for that conference.  
(Conceivably there could be multiple speakers at a conference giving more than  
one talk.)

The getFeaturedSpeaker() endpoint then retrieves the list of speaker keys for  
a given conference from the memcache, then fetches all the speaker/session  
names for each, constructs a message, and returns it.

## How to Run

Navigate to [conf-jeeves](https://conf-jeeves.appspot.com/_ah/api/explorer) and try out all the functions.  
All the endpoint methods associated with conferences remain the same.  New  
methods added are:  
* createSpeaker(SpeakerForm) - create Speaker entities.  A name field  
is required, the rest are optional.  A websafeKey is returned that  
can be used in the speaker field of a new Session object.  
* getSpeakers() - returns a list of all Speakers in the datastore.  
* createSession(SessionForm, websafeConferenceKey) - create a session  
as a child of given conference key.  (typeOfSession defaults to  
'lecture' and duration defaults to 30 min.)  
* getConferenceSessions(websafeConferenceKey) - get all sessions in  
conference with given key.  
* getConferenceSessionsByType(websafeConferenceKey, typeOfSession) -  
get all sessions of given type in conference with given key.  
* getSessionsBySpeaker(websafeSpeakerKey) - returns all sessions  
presented by speaker with given key across all conferences.  
* addSessionToWishlist(websafeSessionKey) - adds session with given  
key to wishlist in user's profile, regardless of registration status.  
Returns new wishlist.
* getSessionsInWishlist() - returns all sessions in users wishlist.  
* getSessionsByDateAndCity(date, city) - returns all sessions across  
all conferences that occur in given city on given date
* getSessionsWithStartTimesWithin(websafeConferenceKey, date, startTime,  
window) - returns all sessions in conference with given key on given  
date that have a startTime within number of minutes given by window.  
* getSessionsBeforeStartTimeNoType(type, startTime) - return all  
sessions that occur before given startTime not (solely) of given type.  
* getFeaturedSpeaker(websafeConferenceKey) - returns announcement  
string with names of featured speaker(s) and the sessions they are  
presenting.
