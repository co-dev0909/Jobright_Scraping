"""Authenticated loopback receiver used by the Kula APP desktop UI.

The callback runs on an HTTP worker thread. It must return only after the UI has
replaced the complete list, or raise if the replacement could not be applied.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import re
import socket
import threading
from typing import Callable
from urllib.parse import urlsplit


DEFAULT_PORT = 43127
MAX_LINKS = 10_000
MAX_BODY_BYTES = 1_048_576
_KEY_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_ORIGIN_PATTERN = re.compile(r"chrome-extension://[a-p]{32}\Z")
_JOB_PATH_PATTERN = re.compile(r"/jobs/info/([A-Za-z0-9_-]{1,200})/?\Z")
_HEX_ID_PATTERN = re.compile(r"[0-9a-fA-F]{24}\Z")


def _reject_constant(_value: str) -> None:
    raise ValueError("Invalid JSON constant")


def _validate_batch(body: object) -> list[str]:
    if (not isinstance(body, dict)
            or type(body.get("protocolVersion")) is not int
            or body["protocolVersion"] != 1
            or not isinstance(body.get("links"), list)
            or not 1 <= len(body["links"]) <= MAX_LINKS):
        raise ValueError("Invalid job-link batch")

    canonical_links: list[str] = []
    seen: set[str] = set()
    for text in body["links"]:
        if (not isinstance(text, str) or len(text) > 2048
                or any(ord(character) < 32 or ord(character) == 127 for character in text)
                or text != text.strip()):
            raise ValueError("Invalid job link")
        url = urlsplit(text)
        if (url.scheme != "https" or url.hostname not in {"jobright.ai", "www.jobright.ai"}
                or url.username is not None or url.password is not None
                or url.port not in {None, 443}):
            raise ValueError("Invalid job link")
        match = _JOB_PATH_PATTERN.fullmatch(url.path)
        if match is None:
            raise ValueError("Invalid job link")
        job_id = match.group(1)
        if _HEX_ID_PATTERN.fullmatch(job_id):
            job_id = job_id.lower()
        link = f"https://jobright.ai/jobs/info/{job_id}"
        if link not in seen:
            seen.add(link)
            canonical_links.append(link)
    return canonical_links


class _LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16
    # Unix needs SO_REUSEADDR to reopen promptly after TCP TIME_WAIT. Windows
    # uses exclusive binding instead, so another local listener cannot reuse an
    # active receiver's address and intercept a paired extension's request.
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        # Bound HTTP worker creation; a stuck client cannot create unlimited
        # request threads while the UI is busy handling a replacement.
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Never print request details, pairing credentials, or private UI
        # exceptions from an HTTP worker to a console/log file.
        pass


class BridgeServer:
    """Manage a local receiver without requiring administrator registration."""

    def __init__(self, pairing_key: str, replace_links: Callable[[list[str]], None], port: int = DEFAULT_PORT):
        if not isinstance(pairing_key, str) or not _KEY_PATTERN.fullmatch(pairing_key):
            raise ValueError("The pairing key must contain 64 hexadecimal characters.")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid bridge port.")
        if not callable(replace_links):
            raise TypeError("A list replacement callback is required.")
        self.port = port
        self._key = pairing_key.lower().encode("ascii")
        self._replace_links = replace_links
        self._replacement_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._closing = threading.Event()
        self._server: _LocalHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._server is not None or self._closing.is_set():
                raise RuntimeError("The bridge has already started or closed.")
            handler = self._handler_type()
            server = _LocalHTTPServer(("127.0.0.1", self.port), handler)
            self.port = server.server_port
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": 0.05}, daemon=True,
                                      name="Kula local receiver")
            try:
                thread.start()
            except BaseException:
                server.server_close()
                raise
            self._server = server
            self._thread = thread

    def close(self) -> None:
        with self._lifecycle_lock:
            self._closing.set()
            server, thread = self._server, self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "KulaAPP"
            sys_version = ""

            def setup(self):
                super().setup()
                # Applies to request-line/header parsing and bounded body reads.
                self.connection.settimeout(10)

            def log_message(self, _format, *_args):
                pass

            def _reply(self, status: int, body: dict):
                self.close_connection = True
                payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except (OSError, ValueError):
                    # A client may disconnect after its batch was committed.
                    # This must not release the replacement lock prematurely.
                    pass

            def _error(self, status: int, message: str):
                self._reply(status, {"error": message})

            def _one_header(self, name: str) -> str | None:
                values = self.headers.get_all(name, [])
                return values[0] if len(values) == 1 else None

            def _check_request(self) -> bool:
                if self.path != "/v1/job-links":
                    self._error(404, "Unknown Kula APP endpoint.")
                    return False
                if self._one_header("Host") != f"127.0.0.1:{bridge.port}":
                    self._error(403, "Invalid local destination.")
                    return False
                origins = self.headers.get_all("Origin", [])
                if len(origins) > 1 or (origins and not _ORIGIN_PATTERN.fullmatch(origins[0])):
                    self._error(403, "Only paired browser extensions can send job links.")
                    return False
                authorization = self._one_header("Authorization") or ""
                token = authorization[7:] if authorization.startswith("Bearer ") else ""
                if not _KEY_PATTERN.fullmatch(token) or not hmac.compare_digest(
                        token.lower().encode("ascii"), bridge._key):
                    self._error(401, "Pairing key rejected. Copy the key from Kula APP and save it in the extension.")
                    return False
                return True

            def do_POST(self):
                if not self._check_request():
                    return
                if bridge._closing.is_set():
                    self._error(503, "Kula APP is closing.")
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._error(415, "Send a JSON job-link batch.")
                    return
                if self.headers.get_all("Transfer-Encoding", []):
                    self._error(501, "Chunked transfers are not supported.")
                    return
                length_text = self._one_header("Content-Length")
                if length_text is None:
                    self._error(411, "A Content-Length is required.")
                    return
                if not re.fullmatch(r"[0-9]+", length_text):
                    self._error(400, "Invalid request length.")
                    return
                if len(length_text) > 7 or int(length_text) > MAX_BODY_BYTES:
                    self._error(413, "The link batch is too large.")
                    return
                length = int(length_text)
                try:
                    data = self.rfile.read(length)
                    if len(data) != length:
                        raise ValueError("Incomplete request body")
                    body = json.loads(data, parse_constant=_reject_constant)
                    links = _validate_batch(body)
                except (OSError, ValueError, UnicodeError, RecursionError):
                    self._error(400, "Send protocolVersion 1 and between 1 and 10000 valid HTTPS Jobright job links.")
                    return
                with bridge._replacement_lock:
                    if bridge._closing.is_set():
                        self._error(503, "Kula APP is closing.")
                        return
                    try:
                        # Retain the lock until the actual desktop update ends,
                        # even if the sender closes its popup or times out.
                        bridge._replace_links(links)
                    except Exception:
                        self._error(500, "Kula APP could not update its job list. Keep the app open and try again.")
                        return
                    self._reply(200, {"ok": True, "count": len(links)})

            def do_GET(self):
                self._error(404, "Unknown Kula APP endpoint.")

            do_PUT = do_GET
            do_DELETE = do_GET
            do_PATCH = do_GET
            do_OPTIONS = do_GET
            do_HEAD = do_GET

        return Handler
