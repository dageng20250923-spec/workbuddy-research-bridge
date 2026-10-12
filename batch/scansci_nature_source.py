"""Short-lived source helper; no old suite edits or unrestricted HTTP binding."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from urllib.parse import urlsplit

from paper_batch import DOI_RE, LIMIT_HTML, LIMIT_PDF, Problem, atomic_json
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--doi', required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    work = args.work.resolve()
    if (not work.is_relative_to((root / 'scansci').resolve()) or not work.is_dir()
            or not re.fullmatch(DOI_RE, args.doi)):
        raise Problem('source_input_invalid')
    os.environ['SCANSCI_PDF_DATA_DIR'] = str(work / 'data')
    config = {'output_dir': str(work), 'cache_dir': str(work / 'cache'),
              'scihub_enabled': False, 'libgen_enabled': False, 'use_tor_for_scihub': False,
              'instsci_enabled': False, 'carsi_enabled': False, 'ezproxy_enabled': False,
              'tor_proxy': '', 'network_proxy': '', 'cookie_path': '', 'chrome_profile_dir': '',
              'carsi_cookie_dir': '', 'browser_headless': True, 'auto_rename': False}
    atomic_json(work / 'data/config.json', config)
    import requests
    import scansci_pdf.pdf_utils as pdf_utils
    import scansci_pdf.sources.nature as nature
    pdf_url = 'https://www.nature.com/articles/' + args.doi.split('/', 1)[1] + '.pdf'

    # Nature's PDF endpoint issues a 303 to idp.nature.com/authorize (a cookie
    # auth hop) before serving the PDF. The project's own paper_batch.fetch uses
    # a hand-rolled redirect loop that rejects idp.nature.com and any query
    # string, so it cannot follow that hop. We therefore follow redirects with a
    # cookie-holding requests.Session instead, while preserving the same strict
    # route boundary: only the two exact Nature URLs are allowed, Tor is refused,
    # and the final resolved URL must still point back at www.nature.com.
    _session = requests.Session()
    _session.headers.update({'User-Agent': 'WeChatPaperBatch/0.1'})

    def checked_fetch(url, _config, **kwargs):
        if kwargs.get('use_tor') or url not in (pdf_url, pdf_url[:-4]):
            raise Problem('source_route_invalid')
        resp = _session.get(url, timeout=60, allow_redirects=True, stream=True)
        if resp.status_code != 200:
            raise Problem('publisher_http_' + str(resp.status_code))
        final = resp.url
        if urlsplit(final).hostname != 'www.nature.com':
            raise Problem('source_route_invalid')
        if len(resp.content) > (LIMIT_PDF if url.endswith('.pdf') else LIMIT_HTML):
            raise Problem('body_too_large')
        return resp

    # Only these child-local module bindings change; installed files do not.
    pdf_utils.fetch = checked_fetch
    nature.fetch = checked_fetch
    output = work / 'source.pdf'
    result = nature.try_nature_direct(args.doi, output, config)
    if not result or result.get('success') is not True:
        raise Problem('publisher_download_unavailable')
    print(json.dumps({'success': True, 'doi': args.doi, 'source': 'NatureDirect',
                      'requested_pdf_url': pdf_url, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'success': False, 'error': 'publisher_download_unavailable'}))
        raise SystemExit(2)
