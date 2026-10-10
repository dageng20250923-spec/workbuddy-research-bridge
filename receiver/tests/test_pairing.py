import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from receiver import (Config, InvalidInput, PairingReceiver, PairingStore, ReceiverServer,
                      confirm_pairing, archive_expired_pairing, CallbackStats)
from test_receiver import TOKEN, ACCOUNT, SENDER, DOI, message, query

CODE = 'pair-' + 'a' * 32


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pairing-test-')
        self.path = Path(self.temp.name) / 'pairing.sqlite3'
        now = int(time.time())
        PairingStore.issue(self.path, hashlib.sha256(CODE.encode()).hexdigest(), now, now + 900)
        self.store = PairingStore(self.path)
        self.config = Config(TOKEN, 'verification-only', frozenset(['verification-only']))
        self.receiver = PairingReceiver(self.config, self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_pairing_not_automatic_authorization(self):
        self.assertEqual(self.receiver.accept(message(CODE), query()), 'pairing_candidate_saved')
        self.assertEqual(self.store.candidate()['account_id'], ACCOUNT)
        self.assertEqual(self.store.candidate()['sender_id'], SENDER)
        self.assertNotIn(SENDER, self.config.allowed_senders)

    def test_pairing_with_openid_uses_xml_identity_not_query(self):
        self.receiver.accept(message(CODE), query(openid='query-only-not-trusted'))
        self.assertEqual(self.store.candidate()['sender_id'], SENDER)

    def test_wrong_code_and_doi_not_stored(self):
        self.receiver.accept(message('private unrelated text'), query())
        self.receiver.accept(message(DOI), query())
        self.assertIsNone(self.store.candidate())

    def test_bad_signature(self):
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(CODE), query(signature='0' * 40))
        self.assertIsNone(self.store.candidate())

    def test_expired(self):
        with self.store.connection() as connection:
            connection.execute('UPDATE pairing SET expires_at=?', (int(time.time()) - 1,))
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(CODE), query())
        self.assertIsNone(self.store.candidate())

    def test_retry_idempotent(self):
        self.receiver.accept(message(CODE), query())
        self.assertEqual(self.receiver.accept(message(CODE), query()), 'pairing_duplicate')

    def test_second_sender_rejected(self):
        self.receiver.accept(message(CODE), query())
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(CODE, sender='other-user', msgid='2'), query())
        self.assertEqual(self.store.candidate()['sender_id'], SENDER)

    def test_second_account_rejected(self):
        self.receiver.accept(message(CODE), query())
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(CODE, account='gh_other', msgid='2'), query())

    def test_second_message_cannot_reuse_code(self):
        self.receiver.accept(message(CODE), query())
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(CODE, msgid='2'), query())

    def test_restart(self):
        self.receiver.accept(message(CODE), query())
        self.assertEqual(PairingStore(self.path).candidate()['sender_id'], SENDER)

    def test_no_plain_code_or_content_in_database(self):
        self.receiver.accept(message(CODE), query())
        raw = self.path.read_bytes()
        self.assertNotIn(CODE.encode(), raw)
        self.assertNotIn(TOKEN.encode(), raw)
        self.assertNotIn(b'Content', raw)

    def test_no_overwrite_existing_challenge(self):
        with self.assertRaises(FileExistsError):
            PairingStore.issue(self.path, 'b' * 64, int(time.time()), int(time.time()) + 900)

    def test_old_message_rejected(self):
        body = message(CODE).replace(str(int(time.time())).encode(), b'1')
        with self.assertRaises(InvalidInput):
            self.receiver.accept(body, query())
        self.assertIsNone(self.store.candidate())

    def test_missing_candidate_cannot_confirm(self):
        config = Path(self.temp.name) / 'config.json'
        with self.assertRaises(InvalidInput):
            confirm_pairing(config, self.store)

    def test_confirm_only_explicit_local_action(self):
        config = Path(self.temp.name) / 'config.json'
        original = {'callback_token': TOKEN, 'account_id': '', 'allowed_senders': [], 'port': 18791}
        config.write_text(json.dumps(original), encoding='utf-8')
        self.receiver.accept(message(CODE), query())
        self.assertEqual(json.loads(config.read_text()), original)
        confirm_pairing(config, self.store)
        confirmed = json.loads(config.read_text())
        self.assertEqual(confirmed['account_id'], ACCOUNT)
        self.assertEqual(confirmed['allowed_senders'], [SENDER])
        self.assertEqual(confirmed['callback_token'], TOKEN)
        confirm_pairing(config, self.store)

    def test_confirm_cannot_overwrite_other_binding(self):
        config = Path(self.temp.name) / 'config.json'
        original = {'callback_token': TOKEN, 'account_id': 'gh_other', 'allowed_senders': ['other']}
        config.write_text(json.dumps(original), encoding='utf-8')
        self.receiver.accept(message(CODE), query())
        with self.assertRaises(InvalidInput):
            confirm_pairing(config, self.store)
        self.assertEqual(json.loads(config.read_text()), original)

    def test_failed_confirmation_temp_file_does_not_overwrite_config(self):
        config = Path(self.temp.name) / 'config.json'
        original = {'callback_token': TOKEN, 'account_id': '', 'allowed_senders': []}
        config.write_text(json.dumps(original), encoding='utf-8')
        config.with_suffix('.confirm.tmp').write_text('synthetic unfinished write', encoding='utf-8')
        self.receiver.accept(message(CODE), query())
        with self.assertRaises(FileExistsError):
            confirm_pairing(config, self.store)
        self.assertEqual(json.loads(config.read_text()), original)

    def test_nontext_ignored(self):
        self.receiver.accept(message(CODE, kind='image'), query())
        self.assertIsNone(self.store.candidate())

    def test_active_challenge_not_archived(self):
        with self.assertRaises(InvalidInput):
            archive_expired_pairing(self.path, Path(self.temp.name)/'code.txt', Path(self.temp.name)/'archive')
        self.assertTrue(self.path.exists())

    def test_claimed_challenge_not_archived_even_expired(self):
        self.receiver.accept(message(CODE), query())
        with self.store.connection() as connection:
            connection.execute('UPDATE pairing SET expires_at=?', (int(time.time()) - 1,))
        with self.assertRaises(InvalidInput):
            archive_expired_pairing(self.path, Path(self.temp.name)/'code.txt', Path(self.temp.name)/'archive')
        self.assertEqual(self.store.candidate()['sender_id'], SENDER)

    def test_expired_unclaimed_challenge_preserved_in_archive(self):
        with self.store.connection() as connection:
            connection.execute('UPDATE pairing SET expires_at=?', (int(time.time()) - 1,))
        code_path = Path(self.temp.name)/'code.txt'
        code_path.write_text(CODE, encoding='utf-8')
        archive = Path(self.temp.name)/'archive'
        archive_expired_pairing(self.path, code_path, archive)
        self.assertFalse(self.path.exists())
        self.assertFalse(code_path.exists())
        self.assertIsNone(PairingStore(archive/'pairing.sqlite3').candidate())
        self.assertEqual((archive/'pairing-code.txt').read_text(), CODE)

    def test_stats_never_persist_unknown_reason_content(self):
        path = Path(self.temp.name)/'stats.json'
        stats = CallbackStats(path)
        stats.record('POST', 400, 'private-secret:'+TOKEN, True)
        output = path.read_text()
        self.assertNotIn(TOKEN, output)
        self.assertNotIn('private-secret', output)
        data = json.loads(output)
        self.assertEqual(data['last_post']['reason'], 'request_failed')
        self.assertEqual(data['last_post']['openid_present'], True)

    def test_http_diagnostic_reason_without_query_or_body(self):
        path = Path(self.temp.name)/'stats.json'
        stats = CallbackStats(path)
        server = ReceiverServer(('127.0.0.1', 0), self.receiver, stats=stats)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}/wechat/callback?' + query(openid='synthetic-private-openid', encrypt_type='aes')
            req = urllib.request.Request(url, data=message(CODE), headers={'Content-Type':'text/xml'})
            with self.assertRaises(urllib.error.HTTPError) as failed:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(failed.exception.code, 400)
            output = path.read_text()
            data = json.loads(output)
            self.assertEqual(data['last_post']['reason'], 'unsupported_query_or_encryption_mode')
            self.assertTrue(data['last_post']['openid_present'])
            for private in (CODE, TOKEN, SENDER, ACCOUNT, 'synthetic-private-openid', 'signature='):
                self.assertNotIn(private, output)
            self.assertIsNone(self.store.candidate())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_http_pairing_with_openid_records_success_without_identity(self):
        path = Path(self.temp.name)/'stats.json'
        server = ReceiverServer(('127.0.0.1', 0), self.receiver, stats=CallbackStats(path))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}/wechat/callback?' + query(openid='synthetic-private-openid')
            req = urllib.request.Request(url, data=message(CODE), headers={'Content-Type':'text/xml'})
            with urllib.request.urlopen(req, timeout=3) as response:
                self.assertEqual(response.read(), b'success')
            output = path.read_text()
            data = json.loads(output)
            self.assertEqual(data['last_post']['reason'], 'pairing_candidate_saved')
            self.assertEqual(data['last_post']['status'], 200)
            for private in (CODE, TOKEN, SENDER, ACCOUNT, 'synthetic-private-openid', 'signature='):
                self.assertNotIn(private, output)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_invalid_identity(self):
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(CODE, sender='invalid user'), query())
        self.assertIsNone(self.store.candidate())

    def test_claim_failure_rolls_back(self):
        with self.store.connection() as connection:
            connection.executescript("CREATE TRIGGER block_claim BEFORE UPDATE ON pairing BEGIN SELECT RAISE(ABORT, 'synthetic error'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            self.receiver.accept(message(CODE), query())
        self.assertIsNone(self.store.candidate())

    def test_parallel_claim_only_one_winner(self):
        outcomes = []
        def claim(sender, msgid):
            try:
                self.receiver.accept(message(CODE, sender=sender, msgid=msgid), query())
                outcomes.append('accepted')
            except InvalidInput:
                outcomes.append('rejected')
        threads = [threading.Thread(target=claim, args=('sender-one', '1')),
                   threading.Thread(target=claim, args=('sender-two', '2'))]
        for worker in threads:
            worker.start()
        for worker in threads:
            worker.join(3)
        self.assertEqual(sorted(outcomes), ['accepted', 'rejected'])

    def test_http_pairing_accept_and_storage_failure(self):
        server = ReceiverServer(('127.0.0.1', 0), self.receiver)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}/wechat/callback?' + query()
            with self.store.connection() as connection:
                connection.executescript("CREATE TRIGGER block_claim BEFORE UPDATE ON pairing BEGIN SELECT RAISE(ABORT, 'synthetic error'); END;")
            req = urllib.request.Request(url, data=message(CODE), headers={'Content-Type':'text/xml'})
            with self.assertRaises(urllib.error.HTTPError) as failed:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(failed.exception.code, 503)
            self.assertIsNone(self.store.candidate())
            with self.store.connection() as connection:
                connection.execute('DROP TRIGGER block_claim')
            with urllib.request.urlopen(req, timeout=3) as response:
                self.assertEqual(response.read(), b'success')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)
