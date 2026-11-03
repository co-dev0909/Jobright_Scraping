"""Authenticated local command channel for the two Kula browser extensions.

Commands are leased to one connected browser client and delivered once. A lost
reply never causes an application submission or another side effect to replay.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
import threading
import time
import uuid


DEFAULT_PORT = 43127
MAX_BODY_BYTES = 1_048_576
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
POLL_TIMEOUT = 10.0
LEASE_SECONDS = 45.0
MAX_COMMANDS = 256
ROLES = frozenset({"jobright", "chatgpt"})
_KEY = re.compile(r"[a-fA-F0-9]{64}\Z")
_CLIENT = re.compile(r"(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})\Z")
_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}\Z")


class CommandError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass
class _Client:
    identifier: str
    last_seen: float


@dataclass
class _Command:
    identifier: str
    run_id: str
    role: str
    client_id: str
    action: str
    payload: dict
    deadline: float
    state: str = "pending"
    delivered: bool = False
    result: dict | None = None
    error: str = ""
    reply: dict | None = None


def _invalid_constant(_value: str):
    raise ValueError("Invalid JSON constant")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def __init__(self, address, handler):
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
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
        pass  # Do not log private command data or request details.


class AutomationBridge:
    def __init__(self, pairing_key: str, port: int = DEFAULT_PORT):
        if not isinstance(pairing_key, str) or not _KEY.fullmatch(pairing_key):
            raise ValueError("The pairing key must contain 64 hexadecimal characters.")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid local automation port.")
        self.port = port
        self._key = pairing_key.lower().encode("ascii")
        self._condition = threading.Condition(threading.RLock())
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._run_id: str | None = None
        self._clients: dict[str, _Client] = {}
        self._commands: OrderedDict[str, _Command] = OrderedDict()
        self._queues: dict[str, deque[str]] = {role: deque() for role in ROLES}

    def start(self) -> None:
        with self._condition:
            if self._closed or self._server is not None:
                raise RuntimeError("The automation connection has already started or closed.")
            server = _Server(("127.0.0.1", self.port), self._handler_type())
            self.port = server.server_port
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                                      name="Kula automation connection", daemon=True)
            try:
                thread.start()
            except BaseException:
                server.server_close()
                raise
            self._server, self._thread = server, thread

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._run_id = None
            for command in self._commands.values():
                if command.state in {"pending", "delivered"}:
                    command.state = "cancelled"
                    command.payload = {}
            server, thread = self._server, self._thread
            self._server = self._thread = None
            self._condition.notify_all()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def connection_status(self) -> dict[str, bool]:
        with self._condition:
            now = time.monotonic()
            return {role: not self._closed and role in self._clients and
                    (now - self._clients[role].last_seen <= LEASE_SECONDS or
                     self._client_busy(role, self._clients[role].identifier, now)) for role in ROLES}

    def begin_run(self, run_id: str) -> None:
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 100:
            raise ValueError("Invalid automation run identifier.")
        with self._condition:
            if self._closed or self._server is None:
                raise CommandError("Open the automation connection before starting.")
            if self._run_id is not None and self._run_id != run_id:
                raise CommandError("Another automation run is active.")
            self._run_id = run_id

    def cancel_run(self, run_id: str) -> None:
        with self._condition:
            if self._run_id == run_id:
                self._run_id = None
            for command in self._commands.values():
                if command.run_id == run_id and command.state in {"pending", "delivered"}:
                    command.state = "cancelled"
                    command.payload = {}
            self._condition.notify_all()

    def execute(self, role: str, action: str, payload: dict, *, run_id: str,
                timeout: float = 120, cancel_event: threading.Event | None = None) -> dict:
        if role not in ROLES or not isinstance(action, str) or not 1 <= len(action) <= 80 or not isinstance(payload, dict):
            raise CommandError("Invalid browser automation command.")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 600:
            raise CommandError("Invalid browser command timeout.")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ValueError("Command payload too large")
            safe_payload = json.loads(encoded)
        except (TypeError, ValueError, UnicodeError, RecursionError) as error:
            raise CommandError("The browser command contains invalid or oversized data.") from error
        with self._condition:
            if self._closed or self._run_id != run_id:
                raise CommandError("The automation run is stopped.")
            if cancel_event is not None and cancel_event.is_set():
                self.cancel_run(run_id)
                raise CommandError("The automation run is stopped.")
            client = self._clients.get(role)
            if client is None or time.monotonic() - client.last_seen > LEASE_SECONDS:
                raise CommandError(f"The {role} extension is not connected.")
            self._prune_commands()
            if len(self._commands) >= MAX_COMMANDS:
                raise CommandError("Too many browser commands are pending.")
            command = _Command(uuid.uuid4().hex, run_id, role, client.identifier, action, safe_payload,
                               time.monotonic() + timeout)
            self._commands[command.identifier] = command
            self._queues[role].append(command.identifier)
            self._condition.notify_all()
            while True:
                if command.state == "completed":
                    return json.loads(json.dumps(command.result))
                if command.state == "failed":
                    raise CommandError(command.error or "The browser operation failed.", uncertain=command.delivered)
                if command.state in {"cancelled", "expired"} or self._closed:
                    raise CommandError("The browser command was stopped or timed out.", uncertain=command.delivered)
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel_run(run_id)
                    raise CommandError("The automation run is stopped.", uncertain=command.delivered)
                remaining = command.deadline - time.monotonic()
                if remaining <= 0:
                    command.state = "expired"
                    command.payload = {}
                    self._condition.notify_all()
                    raise CommandError("The browser command timed out. Check its tab before retrying.", uncertain=command.delivered)
                self._condition.wait(min(remaining, 0.1))

    def _prune_commands(self) -> None:
        while len(self._commands) >= MAX_COMMANDS:
            terminal = next((identifier for identifier, command in self._commands.items()
                             if command.state not in {"pending", "delivered"}), None)
            if terminal is None:
                break
            del self._commands[terminal]
        for role in ROLES:
            self._queues[role] = deque(identifier for identifier in self._queues[role]
                                       if identifier in self._commands and self._commands[identifier].state == "pending")

    def _client_busy(self, role: str, identifier: str, now: float) -> bool:
        return any(command.role == role and command.client_id == identifier and
                   command.state in {"pending", "delivered"} and command.deadline > now and
                   command.run_id == self._run_id for command in self._commands.values())

    def _client(self, body: dict) -> tuple[str, str]:
        role, identifier = body.get("role"), body.get("client_id")
        if role not in ROLES or not isinstance(identifier, str) or not _CLIENT.fullmatch(identifier):
            raise ValueError("Invalid extension client.")
        return role, identifier

    def _connected(self, role: str, identifier: str) -> bool:
        client = self._clients.get(role)
        if client is None or client.identifier != identifier:
            return False
        client.last_seen = time.monotonic()
        return True

    def _route(self, path: str, body: dict) -> tuple[int, dict]:
        role, identifier = self._client(body)
        with self._condition:
            if self._closed:
                return 503, {"error": "Kula APP is closing."}
            if path == "/v1/connect":
                old = self._clients.get(role)
                now = time.monotonic()
                if old is not None and old.identifier != identifier and (
                        now - old.last_seen <= LEASE_SECONDS or self._client_busy(role, old.identifier, now)):
                    return 409, {"error": "Another browser is already connected for this extension role."}
                self._clients[role] = _Client(identifier, time.monotonic())
                self._condition.notify_all()
                return 200, {"ok": True, "role": role}
            if not self._connected(role, identifier):
                return 409, {"error": "Connect this browser extension before requesting commands."}
            if path == "/v1/poll":
                deadline = time.monotonic() + POLL_TIMEOUT
                while not self._closed:
                    queue = self._queues[role]
                    for _ in range(len(queue)):
                        command_id = queue.popleft()
                        command = self._commands.get(command_id)
                        if command is None or command.state != "pending":
                            continue
                        if command.deadline <= time.monotonic() or command.run_id != self._run_id:
                            command.state = "expired"
                            command.payload = {}
                            self._condition.notify_all()
                            continue
                        if command.client_id != identifier:
                            queue.append(command_id)
                            continue
                        command.delivered = True
                        command.state = "delivered"
                        delivery = {"command": {"id": command.identifier, "run_id": command.run_id,
                                                "action": command.action, "payload": command.payload}}
                        # A delivered command is never replayed. Avoid keeping
                        # hundreds of resume PDF payloads in the history cache.
                        command.payload = {}
                        return 200, delivery
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return 200, {"command": None}
                    self._condition.wait(remaining)
                return 503, {"error": "Kula APP is closing."}
            if path not in {"/v1/result", "/v1/authorize"}:
                return 404, {"error": "Unknown automation endpoint."}
            command = self._commands.get(body.get("command_id"))
            if (command is None or command.role != role or command.client_id != identifier
                    or command.run_id != body.get("run_id")):
                return 409, {"error": "This command does not belong to the connected browser run."}
            if path == "/v1/result" and command.state in {"completed", "failed"}:
                reply = {key: body.get(key) for key in ("ok", "result", "error")}
                if reply == command.reply:
                    return 200, {"ok": True}
                return 409, {"error": "A different result was already recorded for this command."}
            if (command.state != "delivered" or self._run_id != command.run_id
                    or time.monotonic() >= command.deadline):
                return 409, {"error": "The browser command has expired or was stopped."}
            if path == "/v1/authorize":
                return 200, {"ok": True}
            if type(body.get("ok")) is not bool or not isinstance(body.get("result", {}), dict):
                return 400, {"error": "Invalid browser command result."}
            error = body.get("error", "")
            if not isinstance(error, str) or len(error) > 1000:
                return 400, {"error": "Invalid browser command result."}
            command.reply = {key: body.get(key) for key in ("ok", "result", "error")}
            if body["ok"]:
                command.state = "completed"
                command.result = body.get("result", {})
            else:
                command.state = "failed"
                command.error = error or "The browser operation could not be completed."
            self._condition.notify_all()
            return 200, {"ok": True}

    def _handler_type(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "KulaAPP"
            sys_version = ""

            def setup(self):
                super().setup()
                self.connection.settimeout(12)

            def log_message(self, _format, *_args):
                pass

            def _reply(self, status: int, value: dict):
                data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                self.close_connection = True
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def _one_header(self, name):
                values = self.headers.get_all(name, [])
                return values[0] if len(values) == 1 else None

            def do_POST(self):
                if self.path not in {"/v1/connect", "/v1/poll", "/v1/result", "/v1/authorize"}:
                    self._reply(404, {"error": "Unknown automation endpoint."})
                    return
                if self._one_header("Host") != f"127.0.0.1:{bridge.port}":
                    self._reply(403, {"error": "Invalid local destination."})
                    return
                origins = self.headers.get_all("Origin", [])
                if len(origins) > 1 or (origins and not _ORIGIN.fullmatch(origins[0])):
                    self._reply(403, {"error": "Only paired extensions can use the automation connection."})
                    return
                authorization = self._one_header("Authorization") or ""
                token = authorization[7:] if authorization.startswith("Bearer ") else ""
                if not _KEY.fullmatch(token) or not hmac.compare_digest(token.lower().encode("ascii"), bridge._key):
                    self._reply(401, {"error": "The automation pairing key was not accepted."})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._reply(415, {"error": "Send a JSON automation request."})
                    return
                if self.headers.get_all("Transfer-Encoding", []):
                    self._reply(501, {"error": "Chunked automation requests are not supported."})
                    return
                length = self._one_header("Content-Length")
                if length is None:
                    self._reply(411, {"error": "A Content-Length is required."})
                    return
                if not re.fullmatch(r"[0-9]+", length):
                    self._reply(400, {"error": "Invalid request length."})
                    return
                if len(length) > 7 or int(length) > MAX_BODY_BYTES:
                    self._reply(413, {"error": "The automation request is too large."})
                    return
                try:
                    data = self.rfile.read(int(length))
                    if len(data) != int(length):
                        raise ValueError("Incomplete request")
                    body = json.loads(data, parse_constant=_invalid_constant)
                    if not isinstance(body, dict):
                        raise ValueError("Invalid automation request")
                    status, result = bridge._route(self.path, body)
                except (ValueError, TypeError, UnicodeError, RecursionError, OSError):
                    status, result = 400, {"error": "Invalid automation request."}
                self._reply(status, result)

            def do_GET(self):
                self._reply(404, {"error": "Unknown automation endpoint."})

            do_OPTIONS = do_GET
            do_PUT = do_GET
            do_PATCH = do_GET
            do_DELETE = do_GET
            do_HEAD = do_GET

        return Handler
