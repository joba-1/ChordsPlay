#!/bin/bash

# The path of the unzipped SchemaCrawler directory
SchemaCrawlerPATH=schemacrawler-14.07.07-main

# The full path of the SQLite database
SQLiteDatabaseFILE=$HOME/.chordsplay.py.db

# The type of the database system.
RDBMS=sqlite

# Where to store the image
OutputPATH=$SQLiteDatabaseFILE.png

# Username and password need to be empty for SQLite
DBUSER=
PASSWORD=

cd $SchemaCrawlerPATH/_schemacrawler/lib/
c=""
sep=""
for f in *.jar; do
  c="$c$sep$f"
  sep=":"
done
java -classpath "$c" schemacrawler.Main -server=$RDBMS -database=$SQLiteDatabaseFILE -outputformat=png -outputfile=$OutputPATH -command=graph -infolevel=maximum -user=$DBUSER -password=$PASSWORD -loglevel=CONFIG
