"""One-job ScanSci adapter; old installation and source PDFs remain untouched.

Default OA batch behavior is unchanged. This is an explicit operator entrypoint
for reviewed public publisher downloads, not a license/redistribution assertion.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from paper_batch import ROOT, DOI_RE, LIMIT_PDF, Problem, atomic_json, pdf_identity, validate_url


def official_pdf(doi):
    if not isinstance(doi, str) or not re.fullmatch(DOI_RE, doi):
        raise Problem('unsupported_doi')
    return 'https://www.nature.com/articles/' + doi.split('/', 1)[1] + '.pdf'


def load_job(task_id):
    if type(task_id) is not int or task_id < 1:
        raise Problem('invalid_task_id')
    job = json.loads((ROOT / 'jobs' / (str(task_id) + '.json')).read_text(encoding='utf-8'))
    if (job.get('task_id') != task_id or job.get('status') not in ('resolved', 'downloaded')
            or job.get('profile') != 'optical_imaging'
            or not isinstance(job.get('title'), str) or not job['title'].strip()
            or not isinstance(job.get('source'), str)
            or hashlib.sha256(job['source'].encode()).hexdigest() != job.get('source_hash')):
        raise Problem('job_not_ready')
    url = official_pdf(job.get('doi'))
    if job.get('primary_url', url[:-4]) != url[:-4]:
        raise Problem('job_source_mismatch')
    return job


def checked_path(path, roots):
    path = Path(path).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in roots) or not path.is_file():
        raise Problem('download_path_invalid')
    if path.suffix.lower() != '.pdf' or path.stat().st_size > LIMIT_PDF:
        raise Problem('unsupported_pdf')
    return path


def checked_bytes(path, job, expected_digest):
    if not isinstance(expected_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_digest):
        raise Problem('download_digest_required')
    data = path.read_bytes()
    if len(data) > LIMIT_PDF or hashlib.sha256(data).hexdigest() != expected_digest:
        raise Problem('artifact_hash_changed')
    pages = pdf_identity(data, job['doi'], job['title'])
    if pages < 2:
        raise Problem('pdf_preview_rejected')
    return data, pages


def reuse_job(job):
    path = checked_path(job['pdf_path'], [ROOT / 'downloads'])
    checked_bytes(path, job, job['sha256'])
    return job


def publish(job, path, expected_digest, url, method):
    validate_url(url)
    if url != official_pdf(job['doi']):
        raise Problem('publisher_source_mismatch')
    data, pages = checked_bytes(path, job, expected_digest)
    folder = ROOT / 'downloads'
    folder.mkdir(exist_ok=True)
    target = folder / (expected_digest + '.pdf')
    if not target.resolve().is_relative_to(ROOT.resolve()):
        raise Problem('download_path_invalid')
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_digest:
            raise Problem('existing_file_hash_mismatch')
    else:
        # Exclusive creation never overwrites the source or a prior artifact.
        # An interrupted partial artifact is detected and stopped on retry.
        with target.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    job.pop('error', None)
    job.update(status='downloaded', sha256=expected_digest, pdf_path=str(target),
               pdf_url=url, bytes=len(data), pages=pages, download_method=method,
               updated_at=int(time.time()))
    job['access'] = {'kind': 'reviewed_public_publisher_endpoint',
                     'scope': 'personal_library', 'redistribution': False,
                     'publisher_url': url, 'checked_at': int(time.time())}
    atomic_json(ROOT / 'jobs' / (str(job['task_id']) + '.json'), job)
    return job


def adopt_verified_report(task_id, report_path):
    """Reuse local operator-reviewed evidence; never trust its verified boolean."""
    report_path = Path(report_path).resolve()
    probe_root = ROOT.parent / 'scansci-probe'
    if not report_path.is_relative_to(probe_root.resolve()) or report_path.stat().st_size > 100_000:
        raise Problem('review_report_path_invalid')
    job = load_job(task_id)
    if job['status'] == 'downloaded':
        return reuse_job(job)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    proof_path = report_path.parent / 'direct-result.json'
    if not proof_path.resolve().is_relative_to(probe_root.resolve()) or proof_path.stat().st_size > 100_000:
        raise Problem('review_report_path_invalid')
    proof = json.loads(proof_path.read_text(encoding='utf-8'))
    source = proof.get('result') or {}
    if (report.get('doi') != job['doi'] or report.get('title') != job['title']
            or proof.get('doi') != job['doi'] or source.get('doi') != job['doi']
            or source.get('success') is not True or source.get('source') != 'NatureDirect'):
        raise Problem('review_evidence_mismatch')
    path = checked_path(report['file'], [probe_root])
    if Path(source.get('file', '')).resolve() != path:
        raise Problem('review_evidence_mismatch')
    return publish(job, path, report.get('sha256'), proof.get('requested_pdf_url'),
                   'scansci_reviewed_publisher_pdf')


def download_job(task_id, installed_python):
    """Optional bounded fresh download; only the exact official Nature source."""
    job = load_job(task_id)
    if job['status'] == 'downloaded':
        return reuse_job(job)
    python = Path(installed_python).resolve()
    if not python.is_file() or (os.name == 'nt' and python.drive.upper() != 'D:'):
        raise Problem('installed_D_python_required')
    ca = python.parent.parent / 'Lib/site-packages/certifi/cacert.pem'
    if not ca.is_file():
        raise Problem('installed_ca_bundle_required')
    sandbox = ROOT / 'scansci'
    sandbox.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=sandbox) as directory:
        work = Path(directory)
        (work / 'temp').mkdir()
        env = os.environ.copy()
        for name in list(env):
            if re.search(r'(?:API_KEY|TOKEN|PASSWORD|SECRET)$', name, re.I):
                env.pop(name)
        env.update({'SCANSCI_PDF_DATA_DIR': str(work / 'data'),
                    'TEMP': str(work / 'temp'), 'TMP': str(work / 'temp'),
                    'TMPDIR': str(work / 'temp'), 'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
                    'XDG_CACHE_HOME': str(work / 'cache'), 'PIP_CACHE_DIR': str(work / 'cache/pip'),
                    'PLAYWRIGHT_BROWSERS_PATH': str(work / 'cache/browsers'),
                    'SSL_CERT_FILE': str(ca), 'REQUESTS_CA_BUNDLE': str(ca),
                    'CURL_CA_BUNDLE': str(ca), 'TOR_PROXY': ''})
        try:
            result = subprocess.run([str(python), '-B', str(ROOT / 'scansci_nature_source.py'),
                                     '--doi', job['doi'], '--work', str(work)],
                                    cwd=work, env=env, capture_output=True, timeout=90,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise Problem('scansci_source_timeout') from None
        if result.returncode != 0 or len(result.stdout) > 100_000:
            raise Problem('scansci_source_unavailable')
        proof = json.loads(result.stdout.decode('utf-8'))
        target = checked_path(work / 'source.pdf', [work])
        if (proof.get('success') is not True or proof.get('source') != 'NatureDirect'
                or proof.get('doi') != job['doi'] or proof.get('requested_pdf_url') != official_pdf(job['doi'])):
            raise Problem('scansci_result_unverified')
        return publish(job, target, proof.get('sha256'), proof['requested_pdf_url'],
                       'isolated_scansci_nature_source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', type=int, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--verified-report', type=Path)
    choice.add_argument('--installed-python', type=Path)
    args = parser.parse_args()
    if os.name == 'nt' and ROOT.drive.upper() != 'D:':
        raise Problem('D_drive_required')
    lock = ROOT / 'batch.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Problem('worker_lock_exists')
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(str(os.getpid()))
        job = (adopt_verified_report(args.task_id, args.verified_report) if args.verified_report
               else download_job(args.task_id, args.installed_python))
        print(json.dumps({'task_id': job['task_id'], 'status': job['status'],
                          'pages': job['pages'], 'bytes': job['bytes'], 'method': job['download_method']},
                         ensure_ascii=False))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Problem as error:
        print('Adapter stopped: ' + str(error))
        raise SystemExit(2)
    except Exception:
        print('Adapter stopped: local_verification_failed')
        raise SystemExit(2)
