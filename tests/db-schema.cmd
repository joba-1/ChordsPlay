rem @echo off
rem The path of the unzipped SchemaCrawler directory
set SchemaCrawlerPATH=schemacrawler-14.07.07-main
rem The path of the SQLite database
set SQLiteDatabaseFILE=%HOMEPATH%\.chordsplay.py.db
rem The type of the database system.
set RDBMS=sqlite
rem Where to store the image
set OutputPATH=%SQLiteDatabaseFILE%.png
rem Username and password need to be empty for SQLite
set DBUSER=
set PASSWORD=

java -classpath %SchemaCrawlerPATH%\_schemacrawler\lib\* schemacrawler.Main -server=%RDBMS% -database=%SQLiteDatabaseFILE% -outputformat=png -outputfile=%OutputPATH% -command=graph -infolevel=maximum -user=%DBUSER% -password=%PASSWORD% -loglevel=CONFIG
