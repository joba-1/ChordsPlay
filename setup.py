try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

config = {
    'description': 'Play your music matching filters like artists or chords while showing lyrics and guitar chords',
    'author': 'joba1',
    'url': 'https://example.org/hg/chordsplay/file/tip/',
    'download_url': 'https://example.org/hg/chordsplay/archive/tip.tar.gz',
    'author_email': 'joba-1@users.noreply.github.com',
    'version': 'tip',
    'install_requires': ['nose', 'tkinter', 'logging', 'sqlite3', 'mutagen', 'vlc', 'requests'],
    'packages': ['ChordsPlay'],
    'scripts': ['chordsplay.py'],
    'license': 'GPL V2 or higher',
    'name': 'chordsplay'
}

setup(**config)