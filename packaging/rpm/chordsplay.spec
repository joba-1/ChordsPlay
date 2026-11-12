#
# spec file for package chordsplay
#
# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
#

%define appdir %{_datadir}/%{name}

Name:           chordsplay
Version:        1.2.1
Release:        0
Summary:        Play your music in the browser with lyrics and guitar chords
License:        AGPL-3.0-or-later AND Apache-2.0
Group:          Productivity/Multimedia/Sound/Players
URL:            https://github.com/joba-1/ChordsPlay
Source0:        %{name}-%{version}.tar.xz
BuildArch:      noarch
BuildRequires:  python3 >= 3.11
BuildRequires:  python3-Flask
BuildRequires:  python3-mutagen
BuildRequires:  python3-qrcode
BuildRequires:  python3-waitress
BuildRequires:  systemd-rpm-macros
BuildRequires:  sysuser-tools
Requires:       python3 >= 3.11
Requires:       python3-Flask
Requires:       python3-mutagen
Requires:       python3-qrcode
Requires:       python3-waitress
%sysusers_requires
%{?systemd_requires}

%description
ChordsPlay is a web server for your music collection: any browser plays the
songs while the page shows each song's lyrics and guitar chords, fetched from
Ultimate Guitar and drawn as large as the screen allows. One server holds the
songs, chords, tags, filters and play history. A TV or tablet can show the
playing song, and PDF tabs next to the song files are shown instead of the text.

The package installs the server as the system service chordsplay.service with
its own user. Settings are in /etc/chordsplay/chordsplay.env, the data is in
/var/lib/chordsplay. The server has no login of its own: publish it through a
reverse proxy (an Apache example is in the documentation).

The bundled PDF viewer PDF.js is under the Apache License 2.0.

%package mmp
Summary:        ChordsPlay amp control for the Fender Mustang Micro Plus
Requires:       maneline-mmp
# opusenc, for recording takes; Leap 16.0 has no opus-tools
Recommends:     opus-tools
Requires:       pipewire-tools
Requires:       pulseaudio-utils
Requires:       python3 >= 3.11
Requires:       python3-numpy

%description mmp
The optional Mustang Micro Plus parts of ChordsPlay, for the computer the amp
is paired with (it may be the server or another one): the amp bridge, which
follows the ChordsPlay page and switches the amp to the preset stored for the
playing song, and the script that measures all presets so they are equally
loud (chordsplay-match-levels).

Both run as systemd user units of the user whose session has the amp's
Bluetooth audio: chordsplay-amp-bridge.service, which starts
chordsplay-amp-agent.service (mmp-web from the package maneline-mmp) while
the amp is connected. See install.md in the documentation of chordsplay.

%prep
%autosetup -n %{name}-%{version}

%build
%sysusers_generate_pre packaging/chordsplay.sysusers %{name} %{name}.conf

%install
install -d %{buildroot}%{appdir}
cp -a ChordsPlay web %{buildroot}%{appdir}/
find %{buildroot}%{appdir} -name __pycache__ -prune -exec rm -rf {} +
# modules run with "python3 -m", not as scripts
find %{buildroot}%{appdir} -name '*.py' -exec chmod 0644 {} +
rm -f %{buildroot}%{appdir}/web/requirements.txt
install -D -m 0755 packaging/chordsplay %{buildroot}%{_bindir}/chordsplay
install -D -m 0644 packaging/chordsplay.service %{buildroot}%{_unitdir}/chordsplay.service
install -D -m 0644 packaging/chordsplay.sysusers %{buildroot}%{_sysusersdir}/chordsplay.conf
install -D -m 0644 packaging/chordsplay.env %{buildroot}%{_sysconfdir}/chordsplay/chordsplay.env
packaging/mmp/install.sh %{buildroot}

%check
python3 -m unittest discover -s tests

%pre -f %{name}.pre
%service_add_pre chordsplay.service

%post
%service_add_post chordsplay.service

%preun
%service_del_preun chordsplay.service

%postun
%service_del_postun chordsplay.service

%files
%license LICENSE
%doc README.md docs/install.md docs/web.md
%doc contrib/chordsplay.env.example contrib/apache-chordsplay-web.conf
%{_bindir}/chordsplay
%{appdir}
%{_unitdir}/chordsplay.service
%{_sysusersdir}/chordsplay.conf
%dir %{_sysconfdir}/chordsplay
%config(noreplace) %{_sysconfdir}/chordsplay/chordsplay.env

%files mmp
%license LICENSE
%doc docs/spec-amp.md
%{_bindir}/chordsplay-match-levels
%{_datadir}/chordsplay-mmp
%{_userunitdir}/chordsplay-amp-agent.service
%{_userunitdir}/chordsplay-amp-bridge.service

%changelog
