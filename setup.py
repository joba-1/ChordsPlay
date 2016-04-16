try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

version='1.2'

config = {
    'description': 'Play your music matching filters like artists or chords while showing lyrics and guitar chords',
    'author': 'joba1',
    'url': 'https://example.org/hg/chordsplay/file/{}'.format(version),
    'download_url': 'https://example.org/hg/chordsplay/archive/{}.tar.gz'.format(version),
    'author_email': 'joba-1@users.noreply.github.com',
    'version': version,
    'install_requires': ['mutagen', 'python-vlc', 'requests'],
    'packages': ['ChordsPlay'],
    'scripts': ['chordsplay.py'],
    'license': 'GPL V2 or higher',
    'name': 'chordsplay'
}

setup(**config)