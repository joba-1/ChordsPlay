= Chords Play

== Fix chord download
Often times ultimate guitar website changes causing my chord download fail.

To fix it, look at chords.py class Chords method request().
You can test with python3 chordsplay.py -t test (generates a file test.dat_ChordsPlay_List.txt), 
or call python ChordsPlay/chords.py (prints request results).

== Todo
* Allow lyrics update override (lock metadata, exclude songs)
* Update current playlist after scan
* Configure tags (assign keys to tag-strings)
* Dialog: Display chord-patterns used in current song
* Dialog: Chord pattern definition, change
* Dialog: Add or delete song_lists (directories and files)
* Chordie
* Synced DB
 * commandline: 2 files and 2 user names
 * print stats on users, songs, user_songs (to sync), song_tags (to sync) and user_filters (to sync)
 * if sync option, then transfer missing data   

== Features
* Scan music library (list of paths or files)
 * Background task (possibly updating current playlist?)
 * Try ID3 tags or guess from filename
 * For every new song or if metadata is old try to get best chords over internet sites
  * Ultimate-guitar, Chordie, ...
  * Allow override (lock metadata, exclude songs)  
  * Save lyrics with chords, list of used chords, capo flag, audio file uri, songname, artist, album
 * Generate or update songlist for all songs, each path and one for all single files
 * List of used chords
 * Capo
 * TODO (V2)
  * Update current playlist after scan?
  * Chordie or others
  
* Play music and display chords according to songlists and filters
 * Play: start, stop, pause, restart, next, prev, update (and try to go to current song), shuffle (but still respect filters)
  * no: Next, prev: show playlist around current position for some time
  * no: Use different play techniques depending on availability and audio file uri (an external player or a library)
  * Display lyrics with chords optimally sized for given window (dynamic)
 * Tag songs while played with predefined tags (key pressed -> tag with string, del: untag previous)
  * Configure tags (assign keys to tag-strings)
 * Configure filters (combined)
  * Songlists to use
  * Artists pattern or list
  * Titles pattern or list
  * Albums pattern or list
  * Longest time not played or recently played
  * Played most
  * Required chords (list)
  * Optional chords (list)
  * no: Excluded chords (list)
  * Required tags (list)
  * Excluded tags (list)
  * Needs capo
 * Save current set of filters
 * Load a set of filters
 * Display chords used in current song
 * Edit or unlock lyrics and chords text

== UI
* lyrics with chords
* playlist around current position
* taglist for selection, add, delete, rename
* filterlist for selection
* filter definition, delete, change (with live preview)
* chord patterns of song with alternatives
* chord pattern definition, change
* add or delete song_lists (directories and files)

* currently no playlist editor, always generated

== Parameters
* config: config file, optionally containing
 * db (or use previous or create default)
 * user (or use os user)

== Persistence
sqlite for now: see ChordsPlay/db.py

== Classes
* main (parameters, init, loop)
* Db (persistence: titles, chords, playlist definitions, ...)
* Song (title, artist, album, lyrics, chords, capo)
* Chord (retrieve, parse, alternatives, visual)
* Scan (get or update online data)
* Play (songlist, play, pause, next, prev)
* Gui (main window)
* UI lyrics with chords
* UI playlist, see current position
* UI taglist for selection, add, delete, rename
* UI filterlist for selection
* UI filter definition, delete, change (with live preview)
* UI chord patterns of song with alternatives
* UI chord pattern definition, change
* UI edit lyrics and chords
* UI add or delete song_lists (directories and files)
* Playlist
* Tag
* Filter
