"""Explicit one-job import gate. No callback changes, no metadata-only imports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote
import urllib.request

from paper_batch import ROOT, Problem, atomic_json, pdf_identity


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Problem('local_redirect_rejected')


def local_request(route, payload=None):
    # Only a fixed local origin; never follow redirects with the bridge token.
    if not route.startswith(('/api/users/0/', '/wechat-zotero/')): raise Problem('local_route_invalid')
    headers = {'Accept':'application/json', 'Zotero-Allowed-Request':'true','Zotero-API-Version':'3'}
    body = None
    if payload is not None:
        token_path = Path(os.environ['LOCALAPPDATA']) / 'WeChatZoteroImporter/bridge-token'
        token = token_path.read_text(encoding='utf-8-sig').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,256}',token): raise Problem('bridge_token_invalid')
        headers.update({'X-WeChat-Zotero-Token':token, 'Content-Type':'application/json'})
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request('http://127.0.0.1:23119'+route, data=body, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=180) as response:
        data = response.read(2*1024*1024+1)
        if len(data)>2*1024*1024: raise Problem('local_response_too_large')
        return json.loads(data)


def find_existing(doi, digest=None):
    rows = local_request('/api/users/0/items?format=json&q='+quote(doi,safe='')+'&qmode=everything&itemType=-attachment&limit=100')
    if not isinstance(rows,list): raise Problem('local_api_unexpected')
    matches = [row for row in rows if row.get('data',{}).get('DOI','').lower().strip()==doi]
    if len(matches)>1:
        same_hash = [row for row in matches if any(tag.get('tag') == 'wechat-zotero-sha256:'+str(digest) for tag in row.get('data',{}).get('tags',[]))] if digest else []
        if len(same_hash)==1: return same_hash[0]
        raise Problem('existing_doi_ambiguous')
    return matches[0] if matches else None


def import_job(task_id):
    job_path = ROOT / 'jobs' / (str(task_id)+'.json')
    job = json.loads(job_path.read_text(encoding='utf-8'))
    existing_result = job.get('library', {})
    if existing_result.get('status') in ('imported','duplicate','existing_doi'):
        return job
    if existing_result.get('status') == 'import_in_flight':
        # A crashed POST may have committed. Reconcile locally, do not retry blindly.
        raise Problem('previous_import_needs_reconciliation')
    if job.get('status') != 'downloaded': raise Problem('verified_pdf_required')
    path = Path(job['pdf_path']).resolve()
    if not path.is_relative_to(ROOT/'downloads'): raise Problem('download_path_invalid')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != job['sha256']: raise Problem('artifact_hash_changed')
    pdf_identity(data,job['doi'],job['title'])
    old = find_existing(job['doi'],job['sha256'])
    if old:
        job['library'] = {'status':'existing_doi','itemKey':old['key'],'classificationPreserved':True}
    else:
        job['library'] = {'status':'import_in_flight','started_at':int(time.time())}
        atomic_json(job_path,job)
        result = local_request('/wechat-zotero/import', {'path':str(path),'sha256':job['sha256'],
            'sourceName':'微信链接整理','extraTags':['来源/微信链接'],'classificationProfileId':'optical_imaging'})
        if result.get('status') not in ('imported','duplicate') or not re.fullmatch(r'[A-Z0-9]{8}',result.get('itemKey','')):
            raise Problem('import_response_needs_reconciliation')
        job['library'] = result
    job['updated_at'] = int(time.time())
    atomic_json(job_path,job)
    return job


def record_existing_note(task_id, note_path):
    """Read-only note verification; never overwrite managed or personal text."""
    job_path = ROOT/'jobs'/(str(task_id)+'.json')
    job = json.loads(job_path.read_text(encoding='utf-8'))
    key = job.get('library',{}).get('itemKey','')
    if not re.fullmatch(r'[A-Z0-9]{8}',key): raise Problem('library_key_required')
    path = Path(note_path).resolve()
    notes_root = Path(os.environ.get(
        'PAPERFLOW_NOTES_ROOT',
        'D:/obsidian/<你的库>/<子目录>/文献笔记')).resolve()
    if not path.is_relative_to(notes_root) or path.suffix.lower() != '.md' or path.stat().st_size > 2*1024*1024:
        raise Problem('note_path_invalid')
    text = path.read_text(encoding='utf-8-sig')
    frontmatter = re.match(r'\A---\r?\n(.*?)\r?\n---',text,re.S)
    if not frontmatter or not re.search(r'^zotero_key:\s*'+re.escape(key)+r'\s*$',frontmatter.group(1),re.M):
        raise Problem('note_key_mismatch')
    if 'zotero://select/library/items/'+key not in text or job['doi'] not in text:
        raise Problem('note_link_or_doi_missing')
    job['notes'] = {'status':'existing_note','path':str(path),'itemKey':key,'preserved':True}
    job['updated_at'] = int(time.time())
    atomic_json(job_path,job)
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id',type=int,required=True)
    args = parser.parse_args()
    lock = ROOT/'library.lock'
    try: descriptor = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise Problem('library_worker_exists')
    try:
        with os.fdopen(descriptor,'w') as out: out.write(str(os.getpid()))
        job = import_job(args.task_id)
        print(json.dumps(job['library'],ensure_ascii=False))
    finally: lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        # No token/HTTP body/path details from unexpected exceptions.
        print('Import stopped; check local job state ('+type(error).__name__+').')
        raise SystemExit(2)
