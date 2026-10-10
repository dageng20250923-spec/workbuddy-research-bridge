"""Finite, download-only OA batch. Does not modify callbacks or libraries."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
from html.parser import HTMLParser
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import time
from urllib.parse import urlsplit, urljoin, quote

ROOT = Path(__file__).resolve().parent
DOI_RE = r'10\.1038/s\d{5}-\d{3}-\d{5}-\d'
UUID_RE = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
ARTICLE_RE = r'/articles/(s\d{5}-\d{3}-\d{5}-\d)(?:\.pdf)?'
LIMIT_HTML = 5 * 1024 * 1024
LIMIT_PDF = 60 * 1024 * 1024


class Problem(Exception):
    """Only fixed-category messages; never expose HTTP content or secrets."""


def validate_url(url):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\s\\\x00-\x1f]', url):
        raise Problem('invalid_url')
    p = urlsplit(url)
    try: port = p.port
    except ValueError: raise Problem('invalid_url')
    if p.scheme != 'https' or p.username or p.password or port not in (None, 443) or p.fragment:
        raise Problem('unsafe_url')
    paths = {
        'www.nature.com': ARTICLE_RE,
        'link.springer.com': r'/content/pdf/' + DOI_RE + r'\.pdf',
        'api.crossref.org': r'/works/10\.1038%2[Ff]s\d{5}-\d{3}-\d{5}-\d',
        'mp.weixin.qq.com': r'/s(?:/[A-Za-z0-9_-]{1,200})?',
        'infoscience.epfl.ch': r'(?:/items/' + UUID_RE + r'|/bitstreams/' + UUID_RE + r'/download|/server/api/core/bitstreams/' + UUID_RE + r'/content)',
    }
    if p.hostname not in paths or not re.fullmatch(paths[p.hostname], p.path):
        raise Problem('unsupported_url')
    if p.query and p.hostname != 'mp.weixin.qq.com':
        raise Problem('unexpected_query')
    return p


def public_ip(address):
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not getattr(ip, 'ipv4_mapped', None)


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        answers = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(row[4][0] for row in answers))
        if not ips or any(not public_ip(ip) for ip in ips):
            raise Problem('non_public_address')
        last = None
        for ip in ips[:4]:
            raw = None
            try:
                raw = socket.create_connection((ip, 443), timeout=min(5, self.timeout))
                raw.settimeout(self.timeout)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as error:
                if raw: raw.close()
                last = error
        raise Problem('tls_or_connection_failed') from last


def fetch(url, limit):
    """DNS-pinned TLS, no proxies/cookies/credentials, validated redirects."""
    for _ in range(5):
        p = validate_url(url)
        connection = PinnedHTTPS(p.hostname, timeout=15, context=ssl.create_default_context())
        try:
            target = p.path + ('?' + p.query if p.query else '')
            connection.request('GET', target, headers={'User-Agent':'WeChatPaperBatch/0.1', 'Accept-Encoding':'identity'})
            response = connection.getresponse()
            if response.status in (301,302,303,307,308):
                location = response.getheader('Location')
                if not location: raise Problem('redirect_missing')
                url = urljoin(url, location)
                validate_url(url)
                continue
            if response.status != 200: raise Problem('publisher_http_' + str(response.status))
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise Problem('unexpected_encoding')
            length = response.getheader('Content-Length')
            if length and (not length.isdigit() or int(length) > limit): raise Problem('body_too_large')
            started = time.monotonic()
            data = bytearray()
            while True:
                if time.monotonic() - started > 90: raise Problem('download_time_limit')
                chunk = response.read(min(65536, limit + 1 - len(data)))
                if not chunk: break
                data.extend(chunk)
                if len(data) > limit: raise Problem('body_too_large')
            if length and int(length) != len(data): raise Problem('truncated_response')
            return bytes(data), response.getheader('Content-Type', '').split(';')[0].lower(), url
        finally: connection.close()
    raise Problem('redirect_limit')


class BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if not self.depth:
            if tag == 'div' and dict(attrs).get('id') == 'js_content': self.depth = 1
            return
        if tag == 'div': self.depth += 1
        if tag in ('script','style'): self.skip += 1
        if not self.skip:
            self.parts.append(' ')
            if tag == 'a': self.parts.append(' ' + dict(attrs).get('href', '') + ' ')

    def handle_endtag(self, tag):
        if not self.depth: return
        if tag in ('script','style') and self.skip: self.skip -= 1
        if tag == 'div': self.depth -= 1
        self.parts.append(' ')

    def handle_data(self, data):
        if self.depth and not self.skip: self.parts.append(data)


def article_doi(page):
    parser = BodyParser()
    parser.feed(page)
    text = ' '.join(parser.parts)
    # Deliberately narrow MVP: an explicit primary link, not the first DOI on page.
    marker = re.search(r'(?:文章链接|论文链接|原文链接|原文地址|论文地址)\s*[：:]', text)
    if not marker: raise Problem('main_paper_needs_review')
    section = re.split(r'相关推荐|相关阅读|推荐阅读', text[marker.end():], maxsplit=1)[0]
    if re.search(r'参考文献|references', section, re.I): raise Problem('ambiguous_main_paper')
    identities = set(re.findall(r'https://www\.nature\.com/articles/(s\d{5}-\d{3}-\d{5}-\d)(?![A-Za-z0-9_-])', section))
    identities.update(d.split('/',1)[1] for d in re.findall(DOI_RE, section))
    if len(identities) != 1: raise Problem('ambiguous_main_paper')
    return '10.1038/' + identities.pop()


def verify_metadata(message, doi):
    if message.get('DOI','').lower() != doi or message.get('type') != 'journal-article':
        raise Problem('metadata_identity_mismatch')
    titles = message.get('title', [])
    if not titles or not isinstance(titles[0], str) or not titles[0].strip(): raise Problem('metadata_no_title')
    year = message.get('published', {}).get('date-parts', [[None]])[0][0]
    if not isinstance(year, int) or not 1800 <= year <= 2100: raise Problem('metadata_no_year')
    return {'doi':doi, 'title':titles[0], 'year':year}


def open_license(message):
    # TDM permission alone is not a public full-text license.
    now = time.time()
    for entry in message.get('license', []):
        p = urlsplit(entry.get('URL',''))
        start = entry.get('start',{}).get('timestamp')
        if (entry.get('content-version') == 'vor' and p.scheme == 'https'
                and p.hostname == 'creativecommons.org' and p.path.startswith('/licenses/')
                and isinstance(start, (int,float)) and start / 1000 <= now):
            return entry['URL']
    raise Problem('open_fulltext_not_confirmed')


def validate_pdf_bytes(data):
    if len(data) < 200 or not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:
        raise Problem('not_complete_pdf')


def pdf_identity(data, doi, title):
    validate_pdf_bytes(data)
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 500: raise Problem('unsupported_pdf')
        text = reader.pages[0].extract_text() or ''
    except (PyPdfError, ValueError, KeyError, IndexError): raise Problem('pdf_parse_failed')
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    if norm(doi) not in norm(text) or norm(title) not in norm(text): raise Problem('pdf_identity_unconfirmed')
    return len(reader.pages)


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}
    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            attr = dict(attrs)
            name = attr.get('name', '').lower()
            if name in ('citation_doi', 'citation_title', 'citation_pdf_url'):
                value = attr.get('content','')
                if name in self.values and self.values[name] != value: raise Problem('ambiguous_repository_metadata')
                self.values[name] = value


def repository_pdf(page, doi, title):
    parser = MetaParser()
    parser.feed(page)
    values = parser.values
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    if values.get('citation_doi','').lower() != doi or norm(values.get('citation_title','')) != norm(title):
        raise Problem('repository_identity_mismatch')
    url = values.get('citation_pdf_url', '')
    parsed = validate_url(url)
    if parsed.hostname != 'infoscience.epfl.ch' or not re.fullmatch('/bitstreams/'+UUID_RE+'/download', parsed.path):
        raise Problem('repository_pdf_url_invalid')
    return url


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def adopt_download(task_id, path, pdf_url):
    """Codex-assisted ordinary Windows download; validate before publication."""
    job_path = ROOT / 'jobs' / (str(task_id) + '.json')
    job = json.loads(job_path.read_text(encoding='utf-8'))
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / 'downloads') or not path.is_file(): raise Problem('download_path_invalid')
    validate_url(pdf_url)
    if job.get('status') != 'resolved' or not job.get('license','').startswith('https://creativecommons.org/licenses/'):
        raise Problem('job_not_ready')
    if path.stat().st_size > LIMIT_PDF: raise Problem('body_too_large')
    data = path.read_bytes()
    pages = pdf_identity(data, job['doi'], job['title'])
    digest = hashlib.sha256(data).hexdigest()
    target = ROOT / 'downloads' / (digest + '.pdf')
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest: raise Problem('existing_file_hash_mismatch')
    else: path.replace(target)
    job.pop('error', None)
    job.update(status='downloaded', download_method='codex_reviewed_public_api', pdf_url=pdf_url,
               sha256=digest, pdf_path=str(target), bytes=len(data), pages=pages, updated_at=int(time.time()))
    atomic_json(job_path, job)
    return job


def process(task, network=fetch, reviewed=None):
    job_path = ROOT / 'jobs' / (str(task['id']) + '.json')
    source = task['value']
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    if job_path.exists():
        old = json.loads(job_path.read_text(encoding='utf-8'))
        if old.get('source_hash') == source_hash and old.get('status') == 'downloaded':
            path = Path(old['pdf_path']).resolve()
            if path.is_relative_to(ROOT / 'downloads') and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == old['sha256']:
                return old
    job = {'task_id':task['id'], 'source':source, 'source_hash':source_hash, 'status':'pending', 'updated_at':int(time.time()), 'profile':'optical_imaging'}
    try:
        evidence = (reviewed or {}).get(source_hash, {})
        if task['kind'] == 'doi':
            doi = source.lower()
        elif evidence.get('source') == source and evidence.get('method') == 'codex_public_html_review':
            # Trusted local review evidence, never a web page instruction.
            doi = evidence['doi']
            expected = 'https://www.nature.com/articles/' + doi.split('/',1)[1]
            if evidence.get('primary_url') != expected: raise Problem('invalid_review_evidence')
            job['resolution_method'] = 'codex_public_html_review'
            job['primary_url'] = expected
        else:
            doi = article_doi(network(source, LIMIT_HTML)[0].decode('utf-8'))
        if not re.fullmatch(DOI_RE, doi): raise Problem('unsupported_doi')
        raw, mime, _ = network('https://api.crossref.org/works/' + quote(doi, safe=''), LIMIT_HTML)
        if mime != 'application/json': raise Problem('not_metadata_json')
        message = json.loads(raw)['message']
        job.update(verify_metadata(message, doi))
        job['status'] = 'resolved'
        job['license'] = open_license(message)
        slug = doi.split('/',1)[1]
        # Both URLs are official publisher endpoints for this exact DOI.
        urls = ['https://www.nature.com/articles/' + slug + '.pdf', 'https://link.springer.com/content/pdf/' + doi + '.pdf']
        repository = evidence.get('repository_record')
        if repository:
            parsed = validate_url(repository)
            if parsed.hostname != 'infoscience.epfl.ch' or not re.fullmatch('/items/'+UUID_RE, parsed.path): raise Problem('invalid_repository_record')
            raw, mime, _ = network(repository, LIMIT_HTML)
            if mime != 'text/html': raise Problem('repository_not_html')
            urls.append(repository_pdf(raw.decode('utf-8'), doi, job['title']))
            job['repository_record'] = repository
        failures = []
        for url in urls:
            try:
                data, mime, final_url = network(url, LIMIT_PDF)
                if mime != 'application/pdf': raise Problem('not_pdf_mime')
                job['pages'] = pdf_identity(data, doi, job['title'])
                job['pdf_url'] = final_url
                break
            except Problem as error: failures.append(str(error))
            except (OSError, ValueError): failures.append('connection_failed')
        job['route_errors'] = failures
        if 'pdf_url' not in job: raise Problem('publisher_download_unavailable')
        digest = hashlib.sha256(data).hexdigest()
        folder = ROOT / 'downloads'
        folder.mkdir(exist_ok=True)
        target = folder / (digest + '.pdf')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest: raise Problem('existing_file_hash_mismatch')
        else:
            temp = target.with_suffix('.part')
            with temp.open('xb') as output:
                output.write(data); output.flush(); os.fsync(output.fileno())
            temp.replace(target)
        job.update(status='downloaded', sha256=digest, pdf_path=str(target), bytes=len(data))
    except Problem as error:
        job['error'] = str(error)
    except (OSError, ValueError, KeyError, TypeError, IndexError, ImportError, http.client.HTTPException):
        job['error'] = 'processing_failed'
    job['updated_at'] = int(time.time())
    atomic_json(job_path, job)
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', type=int, action='append')
    parser.add_argument('--reviewed-sources', action='store_true', help='Use local Codex-reviewed public-source evidence.')
    args = parser.parse_args()
    if os.name == 'nt' and ROOT.drive.upper() != 'D:': raise Problem('D_drive_required')
    queue = ROOT.parent / 'receiver/runtime/queue.sqlite3'
    if not queue.is_file(): raise Problem('queue_missing')
    lock = ROOT / 'batch.lock'
    try: descriptor = os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise Problem('worker_lock_exists')
    try:
        with os.fdopen(descriptor, 'w') as out: out.write(str(os.getpid()))
        reviewed = json.loads((ROOT / 'reviewed-sources.json').read_text(encoding='utf-8')) if args.reviewed_sources else {}
        with closing(sqlite3.connect(queue.as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            tasks = [dict(row) for row in db.execute('SELECT id,kind,value FROM tasks WHERE status=? ORDER BY id LIMIT 20', ('pending',))]
        for task in tasks:
            if args.task_id and task['id'] not in args.task_id: continue
            result = process(task, reviewed=reviewed)
            print(json.dumps({key:result[key] for key in ('task_id','status','doi','title','bytes','error') if key in result}, ensure_ascii=False))
    finally: lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try: main()
    except (Problem, OSError, sqlite3.Error):
        print('Batch not started: configuration, queue or worker lock needs review.')
        raise SystemExit(2)
