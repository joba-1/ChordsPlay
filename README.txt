= Chords Play

== Todo
* gui.resize: use font.measure() of longest lines
* gui: select current song in playlist
* playlist: shuffle including gui titles list
* filter dialog pre-filled with current settings

== Features
* Scan music library (list of paths or files)
 * Background task (possibly updating current playlist?)
 * Try ID3 tags or guess from filename
 * For every new song or if metadata is old try to get best chords over internet sites
  * Ultimate-guitar, Chordie, ...
  * Allow override (lock metadata, exclude songs)  
  * Save lyrics with chords, list of used chords, capo flag, audio file uri, songname, artist, album
 * Generate or update songlist for all songs, each path and one for all single files
 * TODO (V2)
  * Update current playlist?
  * Chordie or others
  * List of used chords
  * Capo
  
* Play music and display chords according to songlists and filters
 * Play: start, stop, pause, restart, next, prev, update (and try to go to current song), shuffle (but still respect filters)
  * Next, prev: show playlist around current position for some time
  * Use different play techniques depending on availability and audio file uri (an external player or a library)
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
  * Excluded chords (list)
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
* UI playlist around current position
* UI taglist for selection, add, delete, rename
* UI filterlist for selection
* UI filter definition, delete, change (with live preview)
* UI chord patterns of song with alternatives
* UI chord pattern definition, change
* UI edit lyrics and chords
* UI add or delete song_lists (directories and files)
* playlist
* Tag
* Filter
