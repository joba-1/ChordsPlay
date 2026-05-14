try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

version='3.21' # update also in chordsplay.py

config = {
    'description': 'Play your music matching filters like artists or chords while showing lyrics and guitar chords',
    'author': 'joba1',
    # 'url': 'https://example.org/hg/chordsplay/file/{}'.format(version),
    # 'download_url': 'https://example.org/hg/chordsplay/archive/{}.tar.gz'.format(version),
    'author_email': 'joba-1@users.noreply.github.com',
    'version': version,
    'install_requires': ['mutagen', 'python-vlc', 'curl_cffi'],
    'packages': ['ChordsPlay'],
    'scripts': ['chordsplay.py'],
    'license': 'GPL V2 or higher',
    'name': 'chordsplay'
}

# Install this package by calling pip install {download_url|path_to_setup.py}
# In the download_url you can replace the version with "tip" to get the bleeding edge
# Or create a source distribution archive with python setup.py sdist
# Then copy and unpack the resulting tar file on the target machine,
# change to the unpacked directory and call python setup.py install
 
setup(**config)
