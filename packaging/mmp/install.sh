#!/bin/sh
# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
# Put the files of the package chordsplay-mmp into DESTDIR; the RPM, Arch and Debian
# packages all call this, from the top of the source tree:
#   packaging/mmp/install.sh DESTDIR
set -eu
dest=${1:?usage: install.sh DESTDIR}
share=$dest/usr/share/chordsplay-mmp
install -d "$share/ChordsPlay" "$share/mmp" "$dest/usr/bin" "$dest/usr/lib/systemd/user"
# standard library only: the bridge and the amp module
install -m 644 ChordsPlay/__init__.py ChordsPlay/amp.py "$share/ChordsPlay/"
install -m 644 mmp/__init__.py mmp/bridge.py "$share/mmp/"
install -m 755 mmp/match-preset-levels.sh "$share/mmp/"
ln -s ../share/chordsplay-mmp/mmp/match-preset-levels.sh "$dest/usr/bin/chordsplay-match-levels"
install -m 644 packaging/mmp/chordsplay-amp-agent.service packaging/mmp/chordsplay-amp-bridge.service \
    "$dest/usr/lib/systemd/user/"
