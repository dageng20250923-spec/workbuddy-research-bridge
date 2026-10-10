"""Loopback-only P0 receiver. No downloads, desktop access or library writes."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit, unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
MAX_BODY = 65536
MAX_TEXT = 8192


class InvalidInput(ValueError):
    def __init__(self, reason: str, status: int = 400):
        super().__init__(reason)
        self.status = status


@dataclass(frozen=True)
class Config:
    token: str
    account_id: str
    allowed_senders: frozenset[str]

    def __post_init__(self):
        if not isinstance(self.token, str) or not 16 <= len(self.token) <= 128:
            raise InvalidInput("token_not_configured")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.account_id or ""):
            raise InvalidInput("account_not_configured")
        if not isinstance(self.allowed_senders, frozenset) or not self.allowed_senders or any(
            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
            for value in self.allowed_senders
        ):
            raise InvalidInput("sender_allowlist_not_configured")
        if any("CHANGE_ME" in value for value in [self.token, self.account_id, *self.allowed_senders]):
            raise InvalidInput("placeholder_not_configured")


def protocol_signature(token: str, timestamp: str, nonce: str) -> str:
    # SHA1 is mandated by the official plaintext callback protocol, not a new
    # general-purpose password/hash design. POST body is not covered by this MAC.
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()


def verify_query(query: str, token: str, now: float | None = None) -> dict[str, str]:
    if len(query) > 4096:
        raise InvalidInput("query_too_large")
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
    except ValueError:
        raise InvalidInput("bad_query") from None
    if any(len(items) != 1 for items in values.values()):
        raise InvalidInput("duplicate_query")
    if set(values) - {"signature", "timestamp", "nonce", "echostr", "openid"}:
        raise InvalidInput("unsupported_query_or_encryption_mode")
    params = {key: items[0] for key, items in values.items()}
    # WeChat message callbacks can include this extra routing hint. It is not
    # part of the signature and is NEVER used to identify/authorize a sender.
    if "openid" in params and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", params["openid"]):
        raise InvalidInput("bad_optional_openid")
    stamp, nonce, signature = (params.get(key, "") for key in ("timestamp", "nonce", "signature"))
    if not re.fullmatch(r"[0-9]{1,13}", stamp) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", nonce):
        raise InvalidInput("bad_timestamp_or_nonce", 403)
    if abs((time.time() if now is None else now) - int(stamp)) > 600:
        raise InvalidInput("stale_callback", 403)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", signature):
        raise InvalidInput("bad_signature", 403)
    if not hmac.compare_digest(signature.lower(), protocol_signature(token, stamp, nonce)):
        raise InvalidInput("bad_signature", 403)
    if len(params.get("echostr", "")) > 512:
        raise InvalidInput("echo_too_large")
    return params


def parse_message(body: bytes) -> dict[str, str]:
    if not body or len(body) > MAX_BODY:
        raise InvalidInput("body_size")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise InvalidInput("utf8_required") from None
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise InvalidInput("xml_dtd_forbidden")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise InvalidInput("bad_xml") from None
    if root.tag != "xml" or root.attrib or len(root) > 24:
        raise InvalidInput("bad_xml_root")
    if root.find("Encrypt") is not None:
        raise InvalidInput("encrypted_mode_not_implemented")
    fields: dict[str, str] = {}
    for child in root:
        if child.tag in fields or len(child) or child.attrib or not re.fullmatch(r"[A-Za-z]+", child.tag):
            raise InvalidInput("bad_xml_field")
        value = child.text or ""
        if len(value) > MAX_TEXT:
            raise InvalidInput("field_too_large")
        fields[child.tag] = value
    for required in ("ToUserName", "FromUserName", "MsgType", "CreateTime"):
        if not fields.get(required):
            raise InvalidInput("missing_field")
    if not re.fullmatch(r"[0-9]{1,13}", fields["CreateTime"]):
        raise InvalidInput("bad_creation_time")
    if fields["MsgType"] == "text":
        if "Content" not in fields or not re.fullmatch(r"[0-9]{1,20}", fields.get("MsgId", "")):
            raise InvalidInput("bad_text_fields")
    return fields


# Fixed safe reply copy only. Never echo queue contents, sender identity or
# article text back to the channel; the reply is a deterministic acknowledgement.
REPLY_TEXT = {
    "accepted": "已收到，正在排队整理。当前为本地验证版，不会下载或写入你的文库。",
    "duplicate": "这条文献已在队列中，无需重复发送。",
    "ignored_nontext": "目前只支持文字消息中的 DOI 或公众号文章链接。",
    "ignored_no_candidate": "没有识别到 DOI 或公众号文章链接。",
}


def build_passive_reply(fields: dict[str, str], outcome: str) -> bytes:
    """WeChat passive text reply (application/xml). Deterministic fixed copy.

    The reply is sent back on the same HTTP response as the callback, so it must
    be produced and returned within the platform's 5-second window. It carries
    no queue content, sender identity or article text.
    """
    if outcome not in REPLY_TEXT or fields.get("MsgType") != "text":
        return b"success"
    content = REPLY_TEXT[outcome]
    # Passive reply flips the callback direction: the reply goes to the sender.
    to_user = fields["FromUserName"]
    from_user = fields["ToUserName"]
    xml = ("<xml><ToUserName><![CDATA[{to}]]></ToUserName>"
           "<FromUserName><![CDATA[{from_}]]></FromUserName>"
           "<CreateTime>{time}</CreateTime><MsgType><![CDATA[text]]></MsgType>"
           "<Content><![CDATA[{content}]]></Content></xml>").format(
        to=to_user, from_=from_user, time=str(int(time.time())), content=content)
    return xml.encode("utf-8")


def extract_candidates(text: str) -> list[tuple[str, str, str]]:
    if len(text) > MAX_TEXT:
        raise InvalidInput("content_too_large")
    candidates: list[tuple[str, str, str]] = []
    keys: set[tuple[str, str]] = set()

    def add(kind: str, value: str, original: str):
        key = (kind, value)
        if key not in keys:
            if len(candidates) >= 20:
                raise InvalidInput("too_many_candidates")
            keys.add(key)
            candidates.append((kind, value, original))

    def doi_candidate(raw: str):
        raw = raw.rstrip(".,;，。；！？、")
        while raw.endswith(")") and raw.count(")") > raw.count("("):
            raw = raw[:-1]
        if re.fullmatch(r"10\.[0-9]{4,9}/[^\s<>\"'?#]+", raw, re.ASCII) and len(raw) <= 512 and raw.isascii():
            add("doi", raw.lower(), raw)

    # Strip URLs from the text before searching bare DOIs, so a DOI hidden in
    # an untrusted host's query cannot silently become a download instruction.
    spans = []
    for match in re.finditer(r"https?://[^\s<>\"'，。；！？、]+", text):
        raw = match.group().rstrip(".,;")
        spans.append(match.span())
        try:
            url = urlsplit(raw)
            if url.scheme != "https" or url.username or url.password or url.port not in (None, 443) or url.fragment:
                continue
            if url.hostname in ("doi.org", "dx.doi.org") and not url.query:
                doi_candidate(unquote(url.path.lstrip("/")))
            elif url.hostname == "mp.weixin.qq.com" and len(raw) <= 4096:
                if ((url.path.startswith("/s/") and len(url.path) > 3)
                        or (url.path == "/s" and url.query)):
                    add("article_url", raw, raw)
        except ValueError:
            continue
    plain = list(text)
    for start, end in spans:
        plain[start:end] = " " * (end - start)
    for match in re.finditer(r"(?<![A-Za-z0-9])10\.[0-9]{4,9}/[^\s<>\"'，。；！？、]+", "".join(plain)):
        doi_candidate(match.group())
    return candidates


class Store:
    def __init__(self, path: Path):
        self.path = path
        with closing(self.connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS receipts (
                    account_key TEXT NOT NULL,
                    msgid TEXT NOT NULL,
                    sender_hash TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    PRIMARY KEY(account_key, msgid)
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY,
                    account_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    original_value TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    added_at INTEGER NOT NULL,
                    UNIQUE(account_key, kind, value)
                );
            """)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=0.5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def enqueue(self, account_key: str, sender_hash: str, msgid: str,
                candidates: list[tuple[str, str, str]]) -> str:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO receipts VALUES (?, ?, ?, ?)",
                (account_key, msgid, sender_hash, int(time.time())),
            ).rowcount
            if inserted:
                for kind, value, original in candidates:
                    connection.execute(
                        "INSERT OR IGNORE INTO tasks(account_key, kind, value, original_value, added_at) VALUES (?, ?, ?, ?, ?)",
                        (account_key, kind, value, original, int(time.time())),
                    )
            connection.commit()  # No success acknowledgement before this completes.
            return "accepted" if inserted else "duplicate"
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def pending(self) -> list[dict]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT id, kind, value, original_value, status, added_at FROM tasks WHERE status='pending' ORDER BY id"
            ).fetchall()
            return [dict(row, paper_verified=False, pdf_url=None) for row in rows]
        finally:
            connection.close()


class Receiver:
    def __init__(self, config: Config, store: Store):
        self.config, self.store = config, store

    def accept(self, body: bytes, query: str) -> str:
        verify_query(query, self.config.token)
        fields = parse_message(body)
        if fields["ToUserName"] != self.config.account_id:
            raise InvalidInput("wrong_account", 403)
        if fields["FromUserName"] not in self.config.allowed_senders:
            raise InvalidInput("sender_not_authorized", 403)
        if fields["MsgType"] != "text":
            return "ignored_nontext"
        candidates = extract_candidates(fields["Content"])
        if not candidates:
            return "ignored_no_candidate"
        account_key = hashlib.sha256(self.config.account_id.encode()).hexdigest()
        sender_hash = hmac.new(self.config.token.encode(), fields["FromUserName"].encode(), hashlib.sha256).hexdigest()
        return self.store.enqueue(account_key, sender_hash, fields["MsgId"], candidates)


class PairingStore:
    """Private singleton challenge/candidate; never a document queue."""

    def __init__(self, path: Path):
        if not path.is_file():
            raise FileNotFoundError()
        self.path = path

    @classmethod
    def issue(cls, path: Path, digest: str, created_at: int, expires_at: int):
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not 60 <= expires_at - created_at <= 900:
            raise InvalidInput("invalid_pairing_challenge")
        with path.open("xb"):
            pass  # Reserve exclusively. Never replace a previous pairing.
        store = cls(path)
        with store.connection() as connection:
            connection.execute("""CREATE TABLE pairing (
                id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                account_id TEXT, sender_id TEXT, msgid TEXT, claimed_at INTEGER
            )""")
            connection.execute("INSERT INTO pairing(id,digest,created_at,expires_at) VALUES(1,?,?,?)",
                               (digest, created_at, expires_at))

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=0.5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def row(self, connection):
        row = connection.execute("SELECT * FROM pairing WHERE id=1").fetchone()
        if row is None:
            raise InvalidInput("pairing_not_initialized", 503)
        return row

    def candidate(self):
        with self.connection() as connection:
            row = self.row(connection)
            if not row["sender_id"]:
                return None
            return {key: row[key] for key in ("account_id", "sender_id", "msgid", "claimed_at")}

    def claim(self, content: str, fields: dict[str, str]) -> str:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self.row(connection)
            now = int(time.time())
            if not row["created_at"] <= now <= row["expires_at"]:
                raise InvalidInput("pairing_expired", 410)
            digest = hashlib.sha256(content.strip().encode()).hexdigest()
            if not hmac.compare_digest(digest, row["digest"]):
                return "ignored_not_pairing_code"
            account, sender = fields["ToUserName"], fields["FromUserName"]
            Config("pairing-validation-placeholder", account, frozenset([sender]))
            if not row["created_at"] - 60 <= int(fields["CreateTime"]) <= min(now + 60, row["expires_at"]):
                raise InvalidInput("pairing_message_time_invalid", 403)
            if row["sender_id"]:
                if (account, sender, fields["MsgId"]) == (row["account_id"], row["sender_id"], row["msgid"]):
                    return "pairing_duplicate"
                raise InvalidInput("pairing_already_claimed", 403)
            connection.execute("UPDATE pairing SET account_id=?,sender_id=?,msgid=?,claimed_at=? WHERE id=1",
                               (account, sender, fields["MsgId"], now))
        return "pairing_candidate_saved"  # Only after commit succeeds.


class PairingReceiver:
    def __init__(self, config: Config, store: PairingStore):
        self.config, self.store = config, store

    def accept(self, body: bytes, query: str) -> str:
        verify_query(query, self.config.token)
        fields = parse_message(body)
        if fields["MsgType"] != "text":
            return "ignored_nontext"
        return self.store.claim(fields["Content"], fields)


def archive_expired_pairing(path: Path, code_path: Path, archive: Path):
    old = PairingStore(path)
    with old.connection() as connection:
        row = old.row(connection)
        if row["sender_id"] or int(time.time()) <= row["expires_at"]:
            raise InvalidInput("cannot_renew_claimed_or_active_challenge")
    archive.mkdir(parents=True, exist_ok=False)
    path.rename(archive / "pairing.sqlite3")
    if code_path.exists():
        code_path.rename(archive / "pairing-code.txt")


def confirm_pairing(config_path: Path, pairing: PairingStore):
    candidate = pairing.candidate()
    if candidate is None:
        raise InvalidInput("pairing_candidate_missing")
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if raw.get("account_id") or raw.get("allowed_senders"):
        if raw.get("account_id") == candidate["account_id"] and raw.get("allowed_senders") == [candidate["sender_id"]]:
            return  # Explicit local re-confirmation is idempotent.
        raise InvalidInput("existing_binding_must_not_be_overwritten")
    Config(raw["callback_token"], candidate["account_id"], frozenset([candidate["sender_id"]]))
    raw["account_id"] = candidate["account_id"]
    raw["allowed_senders"] = [candidate["sender_id"]]
    temporary = config_path.with_suffix(".confirm.tmp")
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(raw, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(config_path)


class CallbackStats:
    REASONS = frozenset({
        "http_response", "request_failed", "temporarily_unavailable", "query_too_large", "bad_query",
        "duplicate_query", "unsupported_query_or_encryption_mode", "bad_timestamp_or_nonce", "stale_callback",
        "bad_signature", "bad_optional_openid", "echo_too_large", "body_size", "utf8_required", "xml_dtd_forbidden", "bad_xml",
        "bad_xml_root", "encrypted_mode_not_implemented", "bad_xml_field", "field_too_large", "missing_field",
        "bad_creation_time", "bad_text_fields", "content_too_large", "too_many_candidates", "wrong_account",
        "sender_not_authorized", "path_too_large", "not_found", "echo_required", "length_required",
        "chunked_not_supported", "body_too_large", "xml_required", "incomplete_body", "pairing_expired",
        "pairing_message_time_invalid", "pairing_already_claimed", "pairing_not_initialized", "accepted",
        "duplicate", "ignored_nontext", "ignored_no_candidate", "ignored_not_pairing_code", "pairing_duplicate",
        "pairing_candidate_saved", "account_not_configured", "sender_allowlist_not_configured", "placeholder_not_configured"
    })

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"started_at": int(time.time()), "counts": {}, "last_post": None}

    def record(self, method: str, status: int, reason: str, openid_present: bool):
        # Code-defined categories only. Never persist request/exception content.
        reason = reason if reason in self.REASONS else "request_failed"
        method = method if method in ("GET", "POST") else "OTHER"
        if status not in (200, 400, 403, 404, 410, 411, 413, 414, 415, 500, 503):
            status = 500
        with self.lock:
            key = f"{method}:{status}:{reason}:openid={bool(openid_present)}"
            self.data["counts"][key] = self.data["counts"].get(key, 0) + 1
            if method == "POST":
                self.data["last_post"] = {"at": int(time.time()), "status": status,
                                          "reason": reason, "openid_present": bool(openid_present)}
            try:
                temporary = self.path.with_suffix(".tmp")
                with temporary.open("w", encoding="utf-8") as output:
                    json.dump(self.data, output)
                temporary.replace(self.path)
            except OSError:
                pass  # Diagnostic failure must not change durable queue acknowledgements.


class ReceiverServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, receiver: Receiver, handler=None, stats=None):
        if address[0] != "127.0.0.1":
            raise InvalidInput("loopback_only")
        self.receiver = receiver
        self.stats = stats
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, handler or CallbackHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # No request path/query/XML/exception stack is printed to console.
        pass


class CallbackHandler(BaseHTTPRequestHandler):
    server_version = "ReceiverProbe"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(4)

    def log_message(self, format, *args):
        pass  # Base logger would leak callback signature and echo in request URL.

    def reply(self, status: int, body: bytes, reason: str = "http_response",
              content_type: str = "text/plain; charset=utf-8"):
        if self.server.stats:
            self.server.stats.record(self.command, status, reason,
                                     bool(re.search(r"[?&]openid=", self.path)))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def callback_query(self) -> str:
        if len(self.path) > 8192:
            raise InvalidInput("path_too_large", 414)
        url = urlsplit(self.path)
        if url.netloc or url.path != "/wechat/callback":
            raise InvalidInput("not_found", 404)
        return url.query

    def do_GET(self):
        try:
            params = verify_query(self.callback_query(), self.server.receiver.config.token)
            if "echostr" not in params:
                raise InvalidInput("echo_required")
            self.reply(200, params["echostr"].encode())
        except InvalidInput as error:
            self.reply(error.status, b"request rejected", str(error))

    def do_POST(self):
        try:
            query = self.callback_query()
            verify_query(query, self.server.receiver.config.token)  # Authenticate before reading body.
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,8}", lengths[0]):
                raise InvalidInput("length_required", 411)
            if self.headers.get("Transfer-Encoding"):
                raise InvalidInput("chunked_not_supported")
            length = int(lengths[0])
            if not 1 <= length <= MAX_BODY:
                raise InvalidInput("body_too_large", 413)
            if self.headers.get_content_type() not in ("text/xml", "application/xml"):
                raise InvalidInput("xml_required", 415)
            body = self.rfile.read(length)
            if len(body) != length:
                raise InvalidInput("incomplete_body")
            outcome = self.server.receiver.accept(body, query)
            reply = build_passive_reply(parse_message(body), outcome)
            if reply == b"success":
                self.reply(200, reply, outcome)
            else:
                self.reply(200, reply, outcome, content_type="text/xml; charset=utf-8")
        except InvalidInput as error:
            self.reply(error.status, b"request rejected", str(error))
        except (sqlite3.Error, OSError):
            self.reply(503, b"temporarily unavailable", "temporarily_unavailable")
        except Exception:
            self.reply(500, b"request failed", "request_failed")


class VerificationHandler(CallbackHandler):
    """URL handshake only. Never read or acknowledge message bodies."""

    def do_POST(self):
        self.reply(503, b"receiver not paired")


def local_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or (os.name == "nt" and path.drive.upper() != "D:"):
        raise InvalidInput("D_project_path_required")
    return path


def main():
    parser = argparse.ArgumentParser(description="Reception-only prototype; no PDF downloads.")
    parser.add_argument("action", choices=("init", "serve", "serve-verification", "export",
                                          "pair-init", "pair-renew", "serve-pairing", "pair-confirm"))
    parser.add_argument("--diagnostics", action="store_true", help="Local fixed-category counts; no request content.")
    parser.add_argument("--test-seconds", type=int, default=0,
                        help="Optional auto-stop, 60..7200 seconds; temporary tests only.")
    args = parser.parse_args()
    if args.test_seconds and not 60 <= args.test_seconds <= 7200:
        raise InvalidInput("invalid_test_duration")
    config_path = local_path("runtime/local-config.json")
    if args.action in ("pair-init", "pair-renew"):
        # Valid existing callback Token required; do not change it after URL setup.
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        Config(raw["callback_token"], "pairing-only", frozenset(["pairing-only"]))
        if raw.get("account_id") or raw.get("allowed_senders"):
            raise InvalidInput("already_bound")
        code_path = local_path("runtime/pairing-code.txt")
        if args.action == "pair-renew":
            archive = local_path("runtime/expired/" + str(int(time.time())) + "-" + secrets.token_hex(4))
            archive_expired_pairing(local_path("runtime/pairing.sqlite3"), code_path, archive)
        if code_path.exists():
            raise FileExistsError()
        code = "pair-" + secrets.token_hex(16)
        now = int(time.time())
        PairingStore.issue(local_path("runtime/pairing.sqlite3"), hashlib.sha256(code.encode()).hexdigest(), now, now + 900)
        with code_path.open("x", encoding="utf-8") as output:
            output.write(code + "\n")
            output.flush()
            os.fsync(output.fileno())
        print("Pairing code saved privately, valid 15 minutes. No automatic authorization.")
        return
    if args.action == "pair-confirm":
        confirm_pairing(config_path, PairingStore(local_path("runtime/pairing.sqlite3")))
        print("Local identity confirmation saved. No service switch or library connection performed.")
        return
    if args.action == "init":
        config_path.parent.mkdir(exist_ok=True)
        # Never overwrite secrets or print them. Configure account and allowed
        # sender OpenID locally, not by pasting credentials in the Codex chat.
        with config_path.open("x", encoding="utf-8") as output:
            json.dump({"callback_token": secrets.token_hex(16), "account_id": "",
                       "allowed_senders": [], "port": 18791}, output, indent=2)
        print("Private configuration created; fill account/sender locally. No listener started.")
        return
    if args.action == "export":
        database = local_path("runtime/queue.sqlite3")
        if not database.exists():
            raise InvalidInput("queue_not_created")
        store = Store(database)
        destination = local_path("runtime/pending-candidates.json")
        temporary = local_path("runtime/pending-candidates.json.tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump({"schema_version": 1, "exported_at": int(time.time()), "tasks": store.pending()}, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
        print("Pending candidates exported locally; nothing marked downloaded or completed.")
        return
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    port = raw.get("port", 18791)
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise InvalidInput("bad_port")
    if args.action == "serve-pairing":
        if raw.get("account_id") or raw.get("allowed_senders"):
            raise InvalidInput("already_bound")
        config = Config(raw["callback_token"], "pairing-only", frozenset(["pairing-only"]))
        service = PairingReceiver(config, PairingStore(local_path("runtime/pairing.sqlite3")))
        server = ReceiverServer(("127.0.0.1", port), service)
    elif args.action == "serve-verification":
        # No real identities, DB or message processing in this mode. A valid
        # random Token is still required to protect the GET challenge endpoint.
        config = Config(raw["callback_token"], "verification-only", frozenset(["verification-only"]))
        service = Receiver(config, None)
        server = ReceiverServer(("127.0.0.1", port), service, VerificationHandler)
    else:
        if not isinstance(raw["allowed_senders"], list):
            raise InvalidInput("allowlist_must_be_array")
        config = Config(raw["callback_token"], raw["account_id"], frozenset(raw["allowed_senders"]))
        store = Store(local_path("runtime/queue.sqlite3"))
        server = ReceiverServer(("127.0.0.1", port), Receiver(config, store))
    print("Loopback service active in " + args.action + " mode; no downloads or library connection.")
    if args.diagnostics:
        server.stats = CallbackStats(local_path("runtime/callback-status.json"))
    timer = None
    if args.test_seconds:
        timer = threading.Timer(args.test_seconds, server.shutdown)
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    finally:
        if timer:
            timer.cancel()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (InvalidInput, OSError, sqlite3.Error, KeyError, TypeError, ValueError) as error:
        # No configuration content, identifiers or secrets in errors.
        print("Setup incomplete or invalid (" + type(error).__name__ + "). No service started.")
        raise SystemExit(2)
