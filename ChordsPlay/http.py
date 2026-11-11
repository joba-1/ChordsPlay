'''
HTTP for the Ultimate Guitar fetch and the sync client, with the standard library.

Ultimate Guitar refuses Python's default User-Agent (403) but answers a browser's.
If it ever asks for more (a TLS fingerprint check), install curl_cffi: session()
then uses it, impersonating Chrome, as ChordsPlay did before 2026-11.
'''

import gzip
import http.cookiejar
import json as jsonlib
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/141.0.0.0 Safari/537.36')


class HTTPError(Exception):
    pass


class Response(object):
    def __init__(self, url, status, body):
        self.url, self.status_code, self.content = url, status, body
        self.text = body.decode('utf-8', 'replace')

    def json(self):
        return jsonlib.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError('{} for {}'.format(self.status_code, self.url))


class Session(object):
    '''The part of the requests API ChordsPlay uses: get and post, cookies kept.'''

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, request, timeout):
        request.add_header('User-Agent', USER_AGENT)
        request.add_header('Accept-Language', 'en-US,en;q=0.9')
        request.add_header('Accept-Encoding', 'gzip')
        if isinstance(timeout, tuple):      # (connect, read): urllib has one timeout
            timeout = max(timeout)
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as e:
            response = e                    # a reply with an error status, as in requests
        with response:
            body = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            return Response(response.geturl(), response.status, body)

    def get(self, url, params=None, headers=None, timeout=30):
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params, doseq=True)
        return self._open(urllib.request.Request(url, headers=headers or {}), timeout)

    def post(self, url, json=None, headers=None, timeout=30):
        data = None if json is None else jsonlib.dumps(json).encode()
        request = urllib.request.Request(url, data=data, headers=headers or {}, method='POST')
        if data is not None and not request.has_header('Content-type'):
            request.add_header('Content-Type', 'application/json')
        return self._open(request, timeout)


def session(browser=False):
    '''A session; with browser=True one that passes as a browser (curl_cffi if installed).'''
    if browser:
        try:
            from curl_cffi import requests as curl_requests
            return curl_requests.Session(impersonate='chrome')
        except ImportError:
            pass
    return Session()
