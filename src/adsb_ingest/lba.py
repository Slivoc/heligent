"""Bounded, preview-only LBA directory adapter. Never writes catalogue records."""
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import re
from threading import Lock
from time import monotonic

import requests

SOURCE = 'https://iauskunft.lba.de/tb/'
_lock = Lock()
_last_fetch = 0.0


class DirectoryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.captures = []
        self.events = []

    def handle_starttag(self, tag, attrs):
        if tag != 'div' and tag != 'span':
            return
        self.depth += 1
        classes = dict(attrs).get('class', '').split()
        for kind in ('inhaber', 'gnr', 'strasse', 'plz_ort', 'rating', 'muster', 'ui-paginator-current'):
            if kind in classes:
                self.captures.append([self.depth, kind, []])

    def handle_data(self, data):
        for capture in self.captures:
            capture[2].append(data)

    def handle_endtag(self, tag):
        if tag not in ('div', 'span'):
            return
        for capture in self.captures[:]:
            if capture[0] == self.depth:
                self.events.append((capture[1], ' '.join(''.join(capture[2]).split())))
                self.captures.remove(capture)
        self.depth -= 1


def parse_directory(raw):
    fragment = re.search(r'<update id="tbliste"><!\[CDATA\[(.*?)\]\]></update>', raw, re.S)
    if not fragment:
        raise ValueError('LBA response format changed; preview stopped without importing anything')
    parser = DirectoryParser()
    parser.feed(fragment.group(1))
    organisations = []
    organisation = site = rating = None
    page = None
    for kind, value in parser.events:
        if kind == 'ui-paginator-current':
            page = value
        elif kind == 'inhaber':
            organisation = {'name':value,'approval':'','sites':[]}
            organisations.append(organisation)
            site = rating = None
        elif kind == 'gnr' and organisation is not None:
            organisation['approval'] = value
        elif kind == 'strasse' and organisation is not None:
            site = {'street':value,'locality':'','ratings':[]}
            organisation['sites'].append(site)
            rating = None
        elif kind == 'plz_ort' and site is not None:
            site['locality'] = value.lstrip(' •')
        elif kind == 'rating':
            if site is None:
                raise ValueError('LBA rating has no site; refusing to flatten approval scope')
            rating = {'wording':value,'models':[]}
            site['ratings'].append(rating)
        elif kind == 'muster':
            if rating is None:
                raise ValueError('LBA model has no rating; preview stopped')
            rating['models'].append(value)
    if not organisations:
        raise ValueError('No organisations returned, or LBA layout changed. Try a more specific name.')
    pagination = re.fullmatch(r'(\d+)-(\d+)\s*/\s*(\d+)', page or '')
    if not pagination:
        raise ValueError('Cannot verify LBA result coverage; preview stopped')
    return {'organisations':organisations,'page':page,
            'truncated':int(pagination[2]) < int(pagination[3])}


def _read(response):
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('Unexpected LBA response; preview stopped')
    chunks = []
    size = 0
    started = monotonic()
    for chunk in response.iter_content(65536):
        if monotonic() - started > 12:
            raise ValueError('LBA response took too long; try again later')
        size += len(chunk)
        if size > 4 * 1024 * 1024:
            raise ValueError('LBA response exceeds preview limit; use a narrower organisation name')
        chunks.append(chunk)
    return b''.join(chunks).decode('utf-8')


def fetch_preview(query):
    global _last_fetch
    if not isinstance(query, str) or not 3 <= len(query.strip()) <= 100:
        raise ValueError('Enter an organisation name of 3–100 characters')
    query = query.strip()
    with _lock:
        if monotonic() - _last_fetch < 30:
            raise ValueError('Please wait 30 seconds between LBA previews')
        _last_fetch = monotonic()
    try:
        with requests.Session() as session:
            session.headers['User-Agent'] = 'Heligent-LBA-preview/1.0'
            with session.get(SOURCE, timeout=(5, 8), stream=True, allow_redirects=False) as response:
                page = _read(response)
            match = re.search(r'name="javax.faces.ViewState"[^>]*value="([^"]+)"', page)
            if not match:
                raise ValueError('LBA search form changed; no preview was imported')
            body = {'javax.faces.partial.ajax':'true','javax.faces.source':'filterForm:filterInhaber',
                    'javax.faces.partial.execute':'filterForm:filterInhaber','javax.faces.partial.render':'tbliste',
                    'javax.faces.behavior.event':'change','javax.faces.partial.event':'change',
                    'filterForm':'filterForm','filterForm:filterInhaber':query,'javax.faces.ViewState':unescape(match[1])}
            with session.post(SOURCE+'index.xhtml', data=body, timeout=(5, 8), stream=True, allow_redirects=False) as response:
                raw = _read(response)
    except requests.RequestException as exc:
        raise ValueError('LBA could not be reached; no records were imported. Try again later.') from exc
    return {**parse_directory(raw),'query':query,'source_url':SOURCE,
            'fetched_at':datetime.now(timezone.utc).isoformat(),
            'source_sha256':sha256(raw.encode('utf-8')).hexdigest(),'mode':'PREVIEW_ONLY'}
