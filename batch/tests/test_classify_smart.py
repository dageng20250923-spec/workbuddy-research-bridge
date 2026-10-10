"""Offline checks; no personal collection, bridge token or live Zotero is read."""
import argparse
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import classify_smart as smart


class SmartClassificationTests(unittest.TestCase):
    def test_release_contains_only_collection_placeholder(self):
        self.assertEqual(smart.PENDING_COLLECTION, "YOURKEY1")

    def test_pending_filters_attachments(self):
        rows = [{"key": "ABCDEFG1", "data": {"itemType": "journalArticle"}},
                {"key": "ABCDEFG2", "data": {"itemType": "attachment"}}]
        with patch.object(smart, "get", return_value=rows) as get:
            self.assertEqual(smart.pending_items(), rows[:1])
        get.assert_called_once_with(
            "/api/users/0/collections/YOURKEY1/items?format=json&limit=100")

    def test_evidence_uses_title_and_abstract_without_writes(self):
        output = io.StringIO()
        with patch.object(smart, "pending_items", return_value=[{"key": "ABCDEFG1"}]), \
             patch.object(smart, "get_children_text", return_value=("Test title", "Test abstract")), \
             patch.object(smart, "token") as token, contextlib.redirect_stdout(output):
            smart.cmd_evidence(None)
        token.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report["pending_count"], 1)
        self.assertEqual(report["items"][0]["abstract"], "Test abstract")

    def test_invalid_key_rejected_before_io(self):
        with patch.object(smart, "token") as token, \
             patch.object(smart, "_opener") as opener, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                smart.cmd_apply(argparse.Namespace(item_key="../bad", dimension="Method", category="OCT"))
        self.assertEqual(error.exception.code, 2)
        token.assert_not_called()
        opener.assert_not_called()

    def test_empty_dimension_rejected_before_io(self):
        with patch.object(smart, "token") as token, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                smart.cmd_apply(argparse.Namespace(item_key="ABCDEFG1", dimension=" ", category="OCT"))
        token.assert_not_called()

    def test_apply_sends_explicit_decision_to_local_authenticated_endpoint(self):
        response = io.BytesIO(b'{"status":"classified"}')
        opener = Mock()
        opener.open.return_value = response
        output = io.StringIO()
        with patch.object(smart, "_opener", return_value=opener), \
             patch.object(smart, "token", return_value="test-token-not-a-real-credential"), \
             contextlib.redirect_stdout(output):
            smart.cmd_apply(argparse.Namespace(item_key="ABCDEFG1", dimension="Method", category="OCT"))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:23119/wechat-zotero/classify")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data),
                         {"itemKey": "ABCDEFG1", "dimension": "Method", "category": "OCT"})
        self.assertEqual(request.get_header("X-wechat-zotero-token"), "test-token-not-a-real-credential")
        self.assertEqual(json.loads(output.getvalue())["status"], "classified")


if __name__ == "__main__":
    unittest.main()
