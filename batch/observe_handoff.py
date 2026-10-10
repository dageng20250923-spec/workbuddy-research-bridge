"""Read-only library/Research Bridge observation; only project reports change."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from library_handoff import local_request, record_existing_note
from paper_batch import ROOT, Problem, atomic_json

import os

# Obsidian 库与 Zotero 数据目录，通过环境变量覆盖（默认值为占位示例）。
VAULT = Path(os.environ.get('PAPERFLOW_VAULT', 'D:/obsidian/<你的库>/<子目录>'))
LIBRARY = Path(os.environ.get('PAPERFLOW_LIBRARY', 'D:/<Zotero数据目录>'))


def observe(task_id):
    job_path = ROOT / 'jobs' / (str(task_id) + '.json')
    job = json.loads(job_path.read_text(encoding='utf-8'))
    result = job.get('library', {})
    key = result.get('itemKey', '')
    if result.get('status') not in ('imported', 'existing_doi', 'duplicate'):
        raise Problem('library_result_required')
    row = local_request('/api/users/0/items/' + key + '?format=json')['data']
    if row.get('DOI', '').lower().strip() != job['doi'] or row.get('itemType') != 'journalArticle':
        raise Problem('library_identity_mismatch')
    children = local_request('/api/users/0/items/' + key + '/children?format=json')
    attachments = [child['data'] for child in children if child['data'].get('contentType') == 'application/pdf']
    if len(attachments) != 1 or attachments[0].get('parentItem') != key:
        raise Problem('attachment_ambiguous')
    attachment = attachments[0]
    stored = (LIBRARY / 'storage' / attachment['key'] / attachment['filename']).resolve()
    if not stored.is_relative_to((LIBRARY / 'storage').resolve()):
        raise Problem('attachment_path_invalid')
    if hashlib.sha256(stored.read_bytes()).hexdigest() != job['sha256']:
        raise Problem('attachment_hash_mismatch')
    collections = []
    for collection_key in row.get('collections', []):
        data = local_request('/api/users/0/collections/' + collection_key + '?format=json')['data']
        collections.append({'key': collection_key, 'name': data['name'], 'parent': data.get('parentCollection')})
    state_path = VAULT / '.obsidian/plugins/research-bridge/state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    record = state['state']['seen'].get('users/0:' + key, {})
    card = record.get('card') or {}
    note_path = None
    if card.get('path'):
        candidate = (VAULT / card['path']).resolve()
        if not candidate.is_relative_to((VAULT / '文献笔记').resolve()):
            raise Problem('note_path_invalid')
        job = record_existing_note(task_id, candidate)
        job['notes']['producer'] = 'existing_research_bridge'
        job['notes']['observed_after_import'] = True
        atomic_json(job_path, job)
        note_path = str(candidate)
    ai_jobs = []
    for ai_job in state['state']['jobs'].values():
        if ai_job.get('paperId') == 'users/0:' + key:
            safe = {field: ai_job.get(field) for field in ('status', 'provider', 'notePath', 'error', 'diagnostic', 'usage') if field in ai_job}
            safe['inline_report_found'] = False
            if note_path:
                text = Path(note_path).read_text(encoding='utf-8-sig')
                safe['inline_report_found'] = ai_job['id'] in text
            ai_jobs.append(safe)
    report = {'observed_at': int(time.time()), 'task_id': task_id, 'doi': job['doi'],
              'itemKey': key, 'attachmentKey': attachment['key'], 'attachmentFilename': attachment['filename'],
              'attachment_sha256_matches': True, 'collections': collections,
              'note_verified': bool(note_path), 'note_path': note_path,
              'research_bridge_stable': record.get('stable'), 'research_bridge_issue': record.get('issue'),
              'research_bridge_auto_attempted': record.get('autoAttempted'), 'ai_jobs': ai_jobs,
              'library_or_vault_written_by_observer': False}
    atomic_json(ROOT / 'reports' / ('handoff-' + str(task_id) + '.json'), report)
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', type=int, required=True)
    arguments = parser.parse_args()
    try:
        observe(arguments.task_id)
    except Exception as error:
        print('Observation stopped (' + type(error).__name__ + '); no library writes.')
        raise SystemExit(2)
