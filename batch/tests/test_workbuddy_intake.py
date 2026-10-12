"""Offline release checks; all queues/files are synthetic and temporary."""
from contextlib import redirect_stdout
import gc
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enqueue
import intake_file


class QueueIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Path(self.temp.name) / 'queue.sqlite3'
        with sqlite3.connect(self.queue) as db:
            db.execute('CREATE TABLE tasks (id INTEGER PRIMARY KEY, account_key TEXT NOT NULL, '
                       'kind TEXT NOT NULL, value TEXT NOT NULL, original_value TEXT NOT NULL, '
                       'status TEXT NOT NULL, added_at INTEGER NOT NULL, '
                       'UNIQUE(account_key, kind, value))')
        self.queue_patch = patch.object(enqueue, 'QUEUE', self.queue)
        self.queue_patch.start()

    def tearDown(self):
        self.queue_patch.stop()
        # SQLite context managers commit/rollback but do not close connections.
        # Collect short-lived connection cycles before Windows deletes fixtures.
        gc.collect()
        self.temp.cleanup()

    def test_extract_doi_and_article_once(self):
        items = enqueue.candidates_from('https://doi.org/10.1038/abc 10.1038/abc '
                                        'https://mp.weixin.qq.com/s/EXAMPLE')
        self.assertEqual(items, [('doi', '10.1038/abc'),
                                 ('article_url', 'https://mp.weixin.qq.com/s/EXAMPLE')])

    def test_unrelated_text_has_no_candidates(self):
        self.assertEqual(enqueue.candidates_from('Only a reading note'), [])

    def test_first_account_requires_explicit_label(self):
        with self.assertRaises(enqueue.Problem):
            enqueue.pick_account_key(None)
        self.assertEqual(enqueue.pick_account_key('local-research'), 'local-research')

    def test_insert_dedup_preserves_existing_status(self):
        item = [('doi', '10.1038/abc')]
        self.assertEqual(len(enqueue.enqueue(item, 'synthetic-account')['added']), 1)
        with sqlite3.connect(self.queue) as db:
            db.execute("UPDATE tasks SET status='completed'")
        repeated = enqueue.enqueue(item, 'synthetic-account')
        self.assertEqual(repeated['added'], [])
        self.assertEqual(len(repeated['already_queued']), 1)
        self.assertEqual(enqueue.list_queue()[0]['status'], 'completed')

    def test_single_account_reused_multiple_accounts_require_override(self):
        enqueue.enqueue([('doi', '10.1038/abc')], 'account-a')
        self.assertEqual(enqueue.pick_account_key(None), 'account-a')
        enqueue.enqueue([('doi', '10.1038/abc')], 'account-b')
        with self.assertRaises(enqueue.Problem):
            enqueue.pick_account_key(None)
        self.assertEqual(len(enqueue.list_queue()), 2)

    def test_missing_queue_is_not_created(self):
        missing = Path(self.temp.name) / 'missing.sqlite3'
        with patch.object(enqueue, 'QUEUE', missing), self.assertRaises(enqueue.Problem):
            enqueue.enqueue([('doi', '10.1038/abc')], 'local-research')
        self.assertFalse(missing.exists())


class PdfIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inbox = self.root / 'inbox'
        self.inbox_patch = patch.object(intake_file, 'INBOX', self.inbox)
        self.ledger_patch = patch.object(intake_file, 'INTAKE_LEDGER',
                                         self.inbox / '.intake-ledger.json')
        self.inbox_patch.start()
        self.ledger_patch.start()

    def tearDown(self):
        self.ledger_patch.stop()
        self.inbox_patch.stop()
        self.temp.cleanup()

    def file(self, folder, name, data=b'%PDF-1.7\nsynthetic-test'):
        path = self.root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_copy_deduplicates_and_preserves_source(self):
        original = self.file('source', 'paper.pdf')
        before = original.read_bytes()
        self.assertEqual(len(intake_file.intake([original])['accepted']), 1)
        again = intake_file.intake([original])
        self.assertEqual(len(again['already_present']), 1)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual((self.inbox / 'paper.pdf').read_bytes(), before)

    def test_same_name_different_bytes_gets_distinct_copy(self):
        first = self.file('first', 'paper.pdf', b'%PDF-a')
        second = self.file('second', 'paper.pdf', b'%PDF-b')
        self.assertEqual(len(intake_file.intake([first, second])['accepted']), 2)
        self.assertEqual(len(list(self.inbox.glob('*.pdf'))), 2)

    def test_invalid_missing_and_oversized_files_rejected(self):
        text = self.file('source', 'notes.txt')
        large = self.file('source', 'large.pdf', b'x' * 10)
        with patch.object(intake_file, 'MAX_PDF', 5):
            result = intake_file.intake([text, large, self.root / 'missing.pdf'])
        self.assertEqual([r['reason'] for r in result['rejected']],
                         ['not_pdf', 'too_large', 'not_a_file'])

    def test_list_does_not_create_or_process_inbox(self):
        with patch.object(intake_file.scan_pdf_dir, 'run') as run, \
                patch.object(sys, 'argv', ['intake_file.py', '--list']), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(intake_file.main(), 0)
        self.assertFalse(json.loads(output.getvalue())['exists'])
        self.assertFalse(self.inbox.exists())
        run.assert_not_called()

    def test_file_cli_uses_explicit_state_path_and_existing_router(self):
        original = self.file('source', 'paper.pdf')
        state = self.root / 'standalone-state.json'
        archive = self.root / 'archive'
        with patch.object(intake_file.scan_pdf_dir, 'run', return_value={'papers': 1}) as run, \
                patch.object(sys, 'argv', ['intake_file.py', '--file', str(original),
                                         '--state-path', str(state), '--archive', str(archive)]), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(intake_file.main(), 0)
        run.assert_called_once_with(self.inbox, state, archive,
                                    intake_file.scan_pdf_dir.BATCH_LIMIT)
        self.assertTrue(original.exists())


if __name__ == '__main__':
    unittest.main()
