import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scansci_adapter as adapter
from paper_batch import Problem

DOI = '10.1038/s41566-026-02019-6'
URL = 'https://www.nature.com/articles/s41566-026-02019-6.pdf'
DATA = b'%PDF-1.7\n' + b'x' * 300 + b'\n%%EOF'


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / 'receiver/.tmp')
        self.project = Path(self.temp.name)
        self.root = self.project / 'batch'
        (self.root / 'jobs').mkdir(parents=True)
        self.probe = self.project / 'scansci-probe'
        self.probe.mkdir()
        self.pdf = self.probe / 'paper.pdf'
        self.pdf.write_bytes(DATA)
        self.digest = hashlib.sha256(DATA).hexdigest()
        self.source = 'https://mp.weixin.qq.com/s/gSh9FTL3moTyxnhTmuH3Lw'
        self.job = {'task_id': 1, 'status': 'resolved', 'doi': DOI, 'title': 'LA-SIM', 'year': 2026,
                    'profile': 'optical_imaging', 'source': self.source,
                    'source_hash': hashlib.sha256(self.source.encode()).hexdigest(), 'error': 'open_fulltext_not_confirmed'}
        self.write_job()
        self.report = {'file': str(self.pdf), 'doi': DOI, 'title': 'LA-SIM', 'sha256': self.digest}
        self.proof = {'doi': DOI, 'requested_pdf_url': URL,
                      'result': {'success': True, 'file': str(self.pdf), 'source': 'NatureDirect', 'doi': DOI}}
        self.write_reports()
        self.root_patch = patch.object(adapter, 'ROOT', self.root)
        self.root_patch.start()
        self.identity_patch = patch.object(adapter, 'pdf_identity', return_value=30)
        self.identity = self.identity_patch.start()

    def tearDown(self):
        self.identity_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def write_job(self):
        (self.root / 'jobs/1.json').write_text(json.dumps(self.job), encoding='utf-8')

    def write_reports(self):
        (self.probe / 'verification.json').write_text(json.dumps(self.report), encoding='utf-8')
        (self.probe / 'direct-result.json').write_text(json.dumps(self.proof), encoding='utf-8')

    def adopt(self):
        return adapter.adopt_verified_report(1, self.probe / 'verification.json')

    def test_adopts_without_inventing_license_and_preserves_original(self):
        result = self.adopt()
        self.assertEqual(result['status'], 'downloaded')
        self.assertNotIn('license', result)
        self.assertEqual(result['access']['scope'], 'personal_library')
        self.assertFalse(result['access']['redistribution'])
        self.assertEqual(result['pages'], 30)
        self.assertEqual(self.pdf.read_bytes(), DATA)
        self.assertEqual(Path(result['pdf_path']).read_bytes(), DATA)
        self.assertNotIn('error', result)

    def test_does_not_trust_verified_boolean(self):
        self.report['identity_verified'] = True
        self.write_reports()
        self.identity.side_effect = Problem('pdf_identity_unconfirmed')
        with self.assertRaises(Problem): self.adopt()
        self.assertFalse((self.root / 'downloads').exists())

    def test_hash_change_rejected(self):
        self.pdf.write_bytes(DATA + b'changed')
        with self.assertRaises(Problem): self.adopt()

    def test_other_doi_rejected(self):
        self.report['doi'] = '10.1038/s41592-026-03196-6'
        self.write_reports()
        with self.assertRaises(Problem): self.adopt()

    def test_other_title_rejected(self):
        self.report['title'] = 'Unrelated paper'
        self.write_reports()
        with self.assertRaises(Problem): self.adopt()

    def test_false_source_success_rejected(self):
        self.proof['result']['success'] = False
        self.write_reports()
        with self.assertRaises(Problem): self.adopt()

    def test_arbitrary_source_rejected(self):
        self.proof['result']['source'] = 'Unknown'
        self.write_reports()
        with self.assertRaises(Problem): self.adopt()

    def test_other_official_pdf_rejected(self):
        self.proof['requested_pdf_url'] = 'https://www.nature.com/articles/s41592-026-03196-6.pdf'
        self.write_reports()
        with self.assertRaises(Problem): self.adopt()

    def test_private_or_credential_url_rejected(self):
        for url in ('https://127.0.0.1/private', 'https://user:pass@www.nature.com/articles/s41566-026-02019-6.pdf'):
            self.proof['requested_pdf_url'] = url
            self.write_reports()
            with self.subTest(url=url), self.assertRaises(Problem): self.adopt()

    def test_report_outside_project_rejected(self):
        with self.assertRaises(Problem): adapter.adopt_verified_report(1, self.project / 'verification.json')

    def test_pdf_outside_allowed_download_roots_rejected(self):
        outside = self.project / 'outside.pdf'
        outside.write_bytes(DATA)
        self.report['file'] = self.proof['result']['file'] = str(outside)
        self.write_reports()
        with self.assertRaises(Problem): self.adopt()

    def test_unresolved_job_rejected(self):
        self.job['status'] = 'pending'
        self.write_job()
        with self.assertRaises(Problem): self.adopt()

    def test_changed_source_hash_rejected(self):
        self.job['source_hash'] = '0' * 64
        self.write_job()
        with self.assertRaises(Problem): self.adopt()

    def test_non_optical_profile_rejected(self):
        self.job['profile'] = 'medical'
        self.write_job()
        with self.assertRaises(Problem): self.adopt()

    def test_one_page_preview_rejected(self):
        self.identity.return_value = 1
        with self.assertRaises(Problem): self.adopt()

    def test_existing_hash_file_conflict_rejected(self):
        folder = self.root / 'downloads'
        folder.mkdir()
        (folder / (self.digest + '.pdf')).write_bytes(b'not the PDF')
        with self.assertRaises(Problem): self.adopt()
        self.assertEqual(json.loads((self.root / 'jobs/1.json').read_text())['status'], 'resolved')

    def test_rerun_preserves_library_and_note_results(self):
        result = self.adopt()
        result['library'] = {'status': 'imported', 'itemKey': 'ABCDEFGH'}
        result['notes'] = {'status': 'existing_note', 'path': 'original.md'}
        (self.root / 'jobs/1.json').write_text(json.dumps(result), encoding='utf-8')
        self.assertEqual(self.adopt()['library']['itemKey'], 'ABCDEFGH')
        self.assertEqual(self.adopt()['notes']['path'], 'original.md')

    def installed_python(self):
        venv = self.project / 'old-suite/venv'
        (venv / 'Scripts').mkdir(parents=True)
        (venv / 'Lib/site-packages/certifi').mkdir(parents=True)
        python = venv / 'Scripts/python.exe'
        python.write_bytes(b'existing mock interpreter')
        (venv / 'Lib/site-packages/certifi/cacert.pem').write_text('existing bundle')
        return python

    def source_result(self, arguments, **kwargs):
        work = Path(kwargs['cwd'])
        (work / 'source.pdf').write_bytes(DATA)
        self.assertEqual(kwargs['timeout'], 90)
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
        self.assertTrue(Path(kwargs['env']['TEMP']).is_relative_to(self.root / 'scansci'))
        self.assertNotIn('scansci_pdf.main', arguments)
        return subprocess.CompletedProcess(arguments, 0, json.dumps({
            'success': True, 'doi': DOI, 'source': 'NatureDirect',
            'requested_pdf_url': URL, 'sha256': self.digest}).encode(), b'')

    def test_fresh_child_uses_isolation_and_validates_output(self):
        python = self.installed_python()
        with patch.dict(adapter.os.environ, {'OPENAI_API_KEY': 'synthetic-not-a-real-key'}), \
                patch.object(adapter.subprocess, 'run', side_effect=self.source_result) as run:
            result = adapter.download_job(1, python)
        run.assert_called_once()
        self.assertEqual(result['download_method'], 'isolated_scansci_nature_source')
        self.assertEqual(python.read_bytes(), b'existing mock interpreter')
        self.assertEqual(list((self.root / 'scansci').iterdir()), [])

    def test_child_timeout_leaves_resolved_job(self):
        with patch.object(adapter.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['mock'], 90)), self.assertRaises(Problem):
            adapter.download_job(1, self.installed_python())
        self.assertEqual(json.loads((self.root / 'jobs/1.json').read_text())['status'], 'resolved')

    def test_child_failure_not_adopted(self):
        with patch.object(adapter.subprocess, 'run', return_value=subprocess.CompletedProcess(['mock'], 2, b'{}', b'')), self.assertRaises(Problem):
            adapter.download_job(1, self.installed_python())
        self.assertFalse((self.root / 'downloads').exists())

    def test_child_false_success_cannot_skip_parser(self):
        self.identity.side_effect = Problem('not_complete_pdf')
        with patch.object(adapter.subprocess, 'run', side_effect=self.source_result), self.assertRaises(Problem):
            adapter.download_job(1, self.installed_python())
        self.assertFalse((self.root / 'downloads').exists())

    def test_downloaded_job_never_starts_another_child(self):
        self.adopt()
        with patch.object(adapter.subprocess, 'run') as run:
            self.assertEqual(adapter.download_job(1, self.project / 'unused-python')['status'], 'downloaded')
        run.assert_not_called()


if __name__ == '__main__': unittest.main()
