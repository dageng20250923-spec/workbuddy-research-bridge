import json
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from receiver import (Config, InvalidInput, Receiver, ReceiverServer, Store, VerificationHandler,
                      build_passive_reply, extract_candidates, parse_message, protocol_signature,
                      verify_query)

TOKEN = "synthetic-test-token-not-a-real-secret"
ACCOUNT = "gh_synthetic"
SENDER = "synthetic-openid"
ARTICLE = "https://mp.weixin.qq.com/s/A62cLEpzYeAJ8yrW-uI9cw"
DOI = "10.1038/s41592-026-03196-6"


def query(**extra):
    params = {"timestamp": str(int(time.time())), "nonce": "synthetic123"}
    params["signature"] = protocol_signature(TOKEN, params["timestamp"], params["nonce"])
    params.update(extra)
    return urllib.parse.urlencode(params)


def message(content=DOI, sender=SENDER, account=ACCOUNT, msgid="18446744073709551615", kind="text"):
    return (f"<xml><ToUserName><![CDATA[{account}]]></ToUserName>"
            f"<FromUserName><![CDATA[{sender}]]></FromUserName>"
            f"<CreateTime>{int(time.time())}</CreateTime><MsgType>{kind}</MsgType>"
            f"<Content><![CDATA[{content}]]></Content><MsgId>{msgid}</MsgId></xml>").encode()


class SignatureTests(unittest.TestCase):
    def test_optional_openid_does_not_change_protocol_signature(self):
        self.assertEqual(verify_query(query(openid='synthetic-openid'), TOKEN)['openid'], 'synthetic-openid')

    def test_invalid_openid_rejected(self):
        for value in ('', 'bad user', 'x' * 129, 'unsafe/path'):
            with self.subTest(value=value):
                with self.assertRaises(InvalidInput):
                    verify_query(query(openid=value), TOKEN)

    def test_duplicate_openid_rejected(self):
        with self.assertRaises(InvalidInput):
            verify_query(query(openid='one') + '&openid=two', TOKEN)

    def test_openid_cannot_bypass_bad_signature(self):
        with self.assertRaises(InvalidInput):
            verify_query(query(openid='synthetic-openid', signature='0' * 40), TOKEN)

    def test_openid_with_encryption_mode_still_rejected(self):
        with self.assertRaises(InvalidInput):
            verify_query(query(openid='synthetic-openid', encrypt_type='aes'), TOKEN)

    def test_protocol_sorting_fixture(self):
        self.assertEqual(protocol_signature("token", "123", "abc"), hashlib.sha1(b"123abctoken").hexdigest())

    def test_valid(self):
        self.assertIn("nonce", verify_query(query(), TOKEN))

    def test_wrong_signature(self):
        with self.assertRaises(InvalidInput):
            verify_query(query(signature="0" * 40), TOKEN)

    def test_duplicate_query(self):
        with self.assertRaises(InvalidInput):
            verify_query(query() + "&nonce=other", TOKEN)

    def test_stale_signature(self):
        stamp = str(int(time.time()) - 601)
        with self.assertRaises(InvalidInput):
            verify_query(query(timestamp=stamp, signature=protocol_signature(TOKEN, stamp, "synthetic123")), TOKEN)

    def test_non_numeric_timestamp(self):
        with self.assertRaises(InvalidInput):
            verify_query(query(timestamp="invalid"), TOKEN)


class ExtractionTests(unittest.TestCase):
    def test_bare_doi(self):
        self.assertEqual(extract_candidates(DOI), [("doi", DOI, DOI)])

    def test_doi_resolver(self):
        self.assertEqual(extract_candidates("https://doi.org/" + DOI)[0][:2], ("doi", DOI))

    def test_doi_case_dedup(self):
        self.assertEqual(len(extract_candidates(DOI + "\n" + DOI.upper())), 1)

    def test_article(self):
        self.assertEqual(extract_candidates(ARTICLE), [("article_url", ARTICLE, ARTICLE)])

    def test_article_parameters(self):
        value = "https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=1&sn=ABC"
        self.assertEqual(extract_candidates(value)[0][1], value)

    def test_reject_other_hosts(self):
        self.assertEqual(extract_candidates("https://mp.weixin.qq.com.evil/s/test"), [])

    def test_reject_nonarticle_page(self):
        self.assertEqual(extract_candidates("https://mp.weixin.qq.com/"), [])

    def test_reject_private_url(self):
        self.assertEqual(extract_candidates("http://127.0.0.1/private"), [])

    def test_ignore_prose(self):
        self.assertEqual(extract_candidates("你好，请稍等"), [])

    def test_preserve_doi_parentheses(self):
        value = "10.1000/abc(2000)01"
        self.assertEqual(extract_candidates(value)[0][1], value)

    def test_trim_citation_punctuation(self):
        self.assertEqual(extract_candidates("DOI: " + DOI + "。" )[0][1], DOI)

    def test_no_doi_from_foreign_query(self):
        self.assertEqual(extract_candidates("https://evil.example/?doi=" + DOI), [])


class XmlTests(unittest.TestCase):
    def test_cdata(self):
        self.assertEqual(parse_message(message(ARTICLE))["Content"], ARTICLE)

    def test_escaped_text(self):
        body = message("one & two").replace(b"<![CDATA[one & two]]>", b"one &amp; two")
        self.assertEqual(parse_message(body)["Content"], "one & two")

    def test_msgid_kept_as_string(self):
        self.assertEqual(parse_message(message())["MsgId"], "18446744073709551615")

    def test_duplicate_field(self):
        with self.assertRaises(InvalidInput):
            parse_message(message().replace(b"</xml>", b"<MsgId>2</MsgId></xml>"))

    def test_xxe(self):
        with self.assertRaises(InvalidInput):
            parse_message(b'<!DOCTYPE xml [<!ENTITY x SYSTEM "file:///secret">]><xml>&x;</xml>')

    def test_nested_fields(self):
        with self.assertRaises(InvalidInput):
            parse_message(message().replace(b"<MsgType>text</MsgType>", b"<MsgType><x>text</x></MsgType>"))

    def test_encrypted_rejected(self):
        with self.assertRaises(InvalidInput):
            parse_message(b"<xml><Encrypt>opaque</Encrypt></xml>")

    def test_oversize(self):
        with self.assertRaises(InvalidInput):
            parse_message(b"x" * 65537)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="receiver-test-")
        self.store = Store(Path(self.temp.name) / "queue.sqlite3")
        self.config = Config(TOKEN, ACCOUNT, frozenset([SENDER]))
        self.receiver = Receiver(self.config, self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_persist_known_sender(self):
        self.receiver.accept(message(), query())
        self.assertEqual(self.store.pending()[0]["value"], DOI)

    def test_dedup_retry(self):
        self.receiver.accept(message(), query())
        self.receiver.accept(message(), query())
        self.assertEqual(len(self.store.pending()), 1)

    def test_dedup_new_message_same_paper(self):
        self.receiver.accept(message(), query())
        self.receiver.accept(message(msgid="2"), query())
        self.assertEqual(len(self.store.pending()), 1)

    def test_account_isolation(self):
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(account="gh_other"), query())
        self.assertEqual(self.store.pending(), [])

    def test_sender_isolation(self):
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(sender="other-user"), query())
        self.assertEqual(self.store.pending(), [])

    def test_openid_parameter_cannot_authorize_wrong_body_sender(self):
        with self.assertRaises(InvalidInput):
            self.receiver.accept(message(sender='other-user'), query(openid=SENDER))
        self.assertEqual(self.store.pending(), [])

    def test_multiple_values(self):
        self.receiver.accept(message(DOI + "\n" + ARTICLE), query())
        self.assertEqual(len(self.store.pending()), 2)

    def test_nontext_not_stored(self):
        self.receiver.accept(message(kind="image"), query())
        self.assertEqual(self.store.pending(), [])

    def test_prose_not_stored(self):
        self.receiver.accept(message("private unrelated prose"), query())
        self.assertEqual(self.store.pending(), [])

    def test_store_error_not_acknowledged(self):
        class BrokenStore:
            def enqueue(self, *args):
                raise sqlite3.OperationalError("synthetic disk failure")
        receiver = Receiver(self.config, BrokenStore())
        with self.assertRaises(sqlite3.OperationalError):
            receiver.accept(message(), query())

    def test_restart_persistence(self):
        self.receiver.accept(message(), query())
        reopened = Store(Path(self.temp.name) / "queue.sqlite3")
        self.assertEqual(len(reopened.pending()), 1)

    def test_transaction_rolls_back_receipt_and_all_tasks(self):
        connection = self.store.connect()
        try:
            connection.executescript("CREATE TRIGGER fail_candidate BEFORE INSERT ON tasks WHEN NEW.value='synthetic-fail' BEGIN SELECT RAISE(ABORT, 'synthetic abort'); END;")
        finally:
            connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.enqueue("account", "senderhash", "123", [("doi", DOI, DOI), ("doi", "synthetic-fail", "synthetic-fail")])
        self.assertEqual(self.store.pending(), [])
        connection = self.store.connect()
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)
        finally:
            connection.close()

    def test_no_raw_content_or_sender_in_export(self):
        self.receiver.accept(message("private prefix " + DOI), query())
        exported = json.dumps(self.store.pending())
        self.assertNotIn("private prefix", exported)
        self.assertNotIn(SENDER, exported)
        self.assertNotIn(TOKEN, exported)

    def test_config_fail_closed(self):
        with self.assertRaises(InvalidInput):
            Config(TOKEN, ACCOUNT, frozenset())


class HttpTests(unittest.TestCase):
    setUp = ServiceTests.setUp
    tearDown = ServiceTests.tearDown
    def test_verification_only_never_accepts_post(self):
        service = Receiver(self.config, None)
        server = ReceiverServer(("127.0.0.1", 0), service, VerificationHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/wechat/callback?"
            with urllib.request.urlopen(url + query(echostr="handshake"), timeout=3) as response:
                self.assertEqual(response.read(), b"handshake")
            request = urllib.request.Request(url + query(), data=message(), headers={"Content-Type": "text/xml"})
            with self.assertRaises(urllib.error.HTTPError) as failed:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(failed.exception.code, 503)
            self.assertEqual(self.store.pending(), [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_http_verify_and_callback(self):
        server = ReceiverServer(("127.0.0.1", 0), self.receiver)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/wechat/callback?"
            with urllib.request.urlopen(url + query(echostr="synthetic_echo"), timeout=3) as response:
                self.assertEqual(response.read(), b"synthetic_echo")
            request = urllib.request.Request(url + query(), data=message(), headers={"Content-Type": "text/xml"})
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertTrue(response.read().startswith(b"<xml>"))
                self.assertEqual(response.headers.get_content_type(), "text/xml")
            self.assertEqual(len(self.store.pending()), 1)
            with self.assertRaises(urllib.error.HTTPError) as failed:
                urllib.request.urlopen(url + query(signature="0" * 40), timeout=3)
            self.assertEqual(failed.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(url.split("/wechat/")[0] + "/queue", timeout=3)
            self.assertEqual(denied.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_http_does_not_ack_storage_failure(self):
        class BrokenStore:
            def enqueue(self, *args):
                raise sqlite3.OperationalError("synthetic failure")
        service = Receiver(self.config, BrokenStore())
        server = ReceiverServer(("127.0.0.1", 0), service)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/wechat/callback?" + query()
            request = urllib.request.Request(url, data=message(), headers={"Content-Type": "text/xml"})
            with self.assertRaises(urllib.error.HTTPError) as failed:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(failed.exception.code, 503)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_refuse_nonloopback(self):
        with self.assertRaises(InvalidInput):
            ReceiverServer(("0.0.0.0", 0), self.receiver)


class ReplyTests(unittest.TestCase):
    def test_accepted_reply_is_text_xml(self):
        reply = build_passive_reply(parse_message(message()), "accepted")
        self.assertTrue(reply.startswith(b"<xml>"))
        self.assertIn(b"<MsgType><![CDATA[text]]></MsgType>", reply)
        self.assertIn(b"<Content><![CDATA[", reply)

    def test_reply_echoes_to_from_accounts_only(self):
        reply = build_passive_reply(parse_message(message()), "accepted").decode()
        # Passive reply flips direction: back to the sender, from the account.
        self.assertIn(f"<ToUserName><![CDATA[{SENDER}]]></ToUserName>", reply)
        self.assertIn(f"<FromUserName><![CDATA[{ACCOUNT}]]></FromUserName>", reply)

    def test_reply_never_leaks_doi_or_content(self):
        reply = build_passive_reply(parse_message(message(DOI)), "accepted").decode()
        # The reply Content is fixed copy only; the DOI never appears anywhere.
        self.assertNotIn("10.1038", reply)
        self.assertNotIn(DOI, reply)

    def test_reply_fixed_copy_for_ignored(self):
        self.assertNotEqual(build_passive_reply(parse_message(message()), "ignored_no_candidate"), b"success")

    def test_reply_unknown_outcome_falls_back_to_success(self):
        self.assertEqual(build_passive_reply(parse_message(message()), "unknown"), b"success")

    def test_reply_nontext_falls_back_to_success(self):
        self.assertEqual(build_passive_reply(parse_message(message(kind="image")), "accepted"), b"success")

    def test_reply_escapes_special_chars(self):
        # Fixed copy has no angle brackets; CDATA keeps it safe regardless.
        reply = build_passive_reply(parse_message(message()), "duplicate").decode()
        self.assertIn("<Content><![CDATA[", reply)
        self.assertNotIn("&lt;", reply)


if __name__ == "__main__":
    unittest.main()
