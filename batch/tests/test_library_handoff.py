import sys
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import library_handoff as handoff
from paper_batch import Problem


class LibraryTests(unittest.TestCase):
    def test_existing_doi_exact(self):
        rows = [{'key':'ABCDEFGH','data':{'DOI':'10.1038/s41592-026-03196-6'}},{'key':'12345678','data':{'DOI':'10.1038/other'}}]
        with patch.object(handoff,'local_request',return_value=rows):
            self.assertEqual(handoff.find_existing('10.1038/s41592-026-03196-6')['key'],'ABCDEFGH')

    def test_ambiguous_duplicates_stop(self):
        rows = [{'key':'ABCDEFGH','data':{'DOI':'test'}},{'key':'12345678','data':{'DOI':'test'}}]
        with patch.object(handoff,'local_request',return_value=rows), self.assertRaises(Problem): handoff.find_existing('test')

    def test_exact_hash_selects_existing_without_merge(self):
        rows = [{'key':'ABCDEFGH','data':{'DOI':'test','tags':[{'tag':'wechat-zotero-sha256:abc'}]}},{'key':'12345678','data':{'DOI':'test','tags':[]}}]
        with patch.object(handoff,'local_request',return_value=rows):
            self.assertEqual(handoff.find_existing('test','abc')['key'],'ABCDEFGH')

    def test_inflight_no_retry(self):
        with tempfile.TemporaryDirectory(dir=handoff.ROOT.parent/'receiver/.tmp') as directory, patch.object(handoff,'ROOT',Path(directory)), patch.object(handoff,'local_request') as request:
            jobs=Path(directory)/'jobs';jobs.mkdir()
            (jobs/'2.json').write_text(json.dumps({'library':{'status':'import_in_flight'}}))
            with self.assertRaises(Problem): handoff.import_job(2)
            request.assert_not_called()

    def test_download_required(self):
        with tempfile.TemporaryDirectory(dir=handoff.ROOT.parent/'receiver/.tmp') as directory, patch.object(handoff,'ROOT',Path(directory)), patch.object(handoff,'local_request') as request:
            jobs=Path(directory)/'jobs';jobs.mkdir()
            (jobs/'2.json').write_text(json.dumps({'status':'resolved'}))
            with self.assertRaises(Problem): handoff.import_job(2)
            request.assert_not_called()

    def test_success_rerun_no_import(self):
        with tempfile.TemporaryDirectory(dir=handoff.ROOT.parent/'receiver/.tmp') as directory, patch.object(handoff,'ROOT',Path(directory)), patch.object(handoff,'local_request') as request:
            jobs=Path(directory)/'jobs';jobs.mkdir()
            (jobs/'2.json').write_text(json.dumps({'library':{'status':'imported','itemKey':'ABCDEFGH'}}))
            self.assertEqual(handoff.import_job(2)['library']['itemKey'],'ABCDEFGH')
            request.assert_not_called()

    def test_redirect_rejected(self):
        with self.assertRaises(Problem): handoff.NoRedirect().redirect_request(None,None,302,'',None,'https://evil.test/')


if __name__ == '__main__': unittest.main()
