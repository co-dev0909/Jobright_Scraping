"""Real loopback HTTP checks for Kula APP's extension connection."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import BridgeServer


PAIRING_KEY = "a" * 64  # Public test data, not an application credential.
OLD_LINK = "https://jobright.ai/jobs/info/previous"
NEW_LINK = "https://jobright.ai/jobs/info/current"


class Fixture:
    def __init__(self, replace=None):
        self.links = [OLD_LINK]
        self.replacements = 0
        self.server = BridgeServer(PAIRING_KEY, replace or self.replace, port=0)
        self.server.start()

    def replace(self, links):
        self.links = list(links)
        self.replacements += 1

    def close(self):
        self.server.close()

    def request(self, payload=None, *, raw=None, token=PAIRING_KEY,
                origin=None, host=None, content_type="application/json",
                route="/v1/job-links"):
        if raw is None:
            raw = json.dumps(payload if payload is not None else {
                "protocolVersion": 1, "links": [NEW_LINK],
            }).encode("utf-8")
        elif isinstance(raw, str):
            raw = raw.encode("utf-8")
        headers = {"Content-Type": content_type}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request("POST", route, body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()


@contextmanager
def running_server(replace=None):
    fixture = Fixture(replace)
    try:
        yield fixture
    finally:
        fixture.close()


class BridgeTests(unittest.TestCase):
    def assert_unchanged(self, fixture):
        self.assertEqual(fixture.links, [OLD_LINK])
        self.assertEqual(fixture.replacements, 0)

    def test_batches_replace_previous_list_and_canonicalize_links(self):
        with running_server() as fixture:
            status, reply = fixture.request({"protocolVersion": 1, "links": [
                "https://www.jobright.ai:443/jobs/info/ABCDEF0123456789ABCDEF01/?source=search#apply",
                "https://jobright.ai/jobs/info/abcdef0123456789abcdef01",
                "https://jobright.ai/jobs/info/second",
            ]})
            self.assertEqual((status, reply), (200, {"ok": True, "count": 2}))
            self.assertEqual(fixture.links, [
                "https://jobright.ai/jobs/info/abcdef0123456789abcdef01",
                "https://jobright.ai/jobs/info/second",
            ])
            self.assertEqual(fixture.request(), (200, {"ok": True, "count": 1}))
            self.assertEqual(fixture.links, [NEW_LINK], "A new batch replaces rather than appends")
            self.assertEqual(fixture.replacements, 2)

    def test_invalid_batch_never_changes_existing_list(self):
        invalid_urls = [
            "http://jobright.ai/jobs/info/insecure",
            "https://example.com/jobs/info/offsite",
            "https://jobright.ai.example.com/jobs/info/spoofed",
            "https://user:password@jobright.ai/jobs/info/credentials",
            "https://jobright.ai:444/jobs/info/port",
            "https://jobright.ai/jobs/info/extra/path",
            "https://jobright.ai/jobs/info/encoded%2Fslash",
            "https://jobright.ai/jobs/info/encoded%5Cslash",
            "https://jobright.ai/jobs/info/",
            "javascript:alert(1)",
            "not a URL",
            "https://jobright.ai/jobs/info/" + "a" * 2050,
        ]
        invalid_batches = [
            {"protocolVersion": 1, "links": []},
            {"protocolVersion": 2, "links": [NEW_LINK]},
            {"protocolVersion": True, "links": [NEW_LINK]},
            {"protocolVersion": "1", "links": [NEW_LINK]},
            {"links": [NEW_LINK]},
            {"protocolVersion": 1, "links": [NEW_LINK, None]},
            {"protocolVersion": 1, "links": "not an array"},
            {"protocolVersion": 1, "links": [NEW_LINK] * 10001},
            [],
            None,
        ]
        with running_server() as fixture:
            for url in invalid_urls:
                with self.subTest(url=url):
                    self.assertEqual(fixture.request({"protocolVersion": 1, "links": [NEW_LINK, url]})[0], 400)
                    self.assert_unchanged(fixture)
            for payload in invalid_batches:
                with self.subTest(payload_type=type(payload).__name__):
                    self.assertEqual(fixture.request(raw=json.dumps(payload))[0], 400)
                    self.assert_unchanged(fixture)
            for malformed in ('{"protocolVersion":1,"links":[', b"\xff"):
                self.assertEqual(fixture.request(raw=malformed)[0], 400)
                self.assert_unchanged(fixture)

    def test_authentication_origin_and_host_checks(self):
        with running_server() as fixture:
            for token in (None, "b" * 64):
                self.assertEqual(fixture.request(token=token)[0], 401)
                self.assert_unchanged(fixture)
            for origin in ("https://jobright.ai", "http://127.0.0.1:43127", "null",
                           "chrome-extension://" + "x" * 32):
                self.assertEqual(fixture.request(origin=origin)[0], 403)
                self.assert_unchanged(fixture)
            self.assertEqual(fixture.request(host=f"localhost:{fixture.server.port}")[0], 403)
            self.assert_unchanged(fixture)
            self.assertEqual(fixture.request(token=PAIRING_KEY.upper(), origin="chrome-extension://" + "a" * 32),
                             (200, {"ok": True, "count": 1}))

    def test_request_limits_and_route_errors_do_not_replace_list(self):
        with running_server() as fixture:
            self.assertEqual(fixture.request(content_type="text/plain")[0], 415)
            self.assertEqual(fixture.request(route="/v1/unknown")[0], 404)
            for headers, expected in (({"Content-Length": str(1_048_577)}, 413), ({}, 411)):
                connection = http.client.HTTPConnection("127.0.0.1", fixture.server.port, timeout=5)
                try:
                    connection.putrequest("POST", "/v1/job-links")
                    connection.putheader("Authorization", "Bearer " + PAIRING_KEY)
                    connection.putheader("Content-Type", "application/json")
                    for name, value in headers.items():
                        connection.putheader(name, value)
                    connection.endheaders()
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
                finally:
                    connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", fixture.server.port, timeout=5)
            try:
                connection.putrequest("POST", "/v1/job-links")
                connection.putheader("Authorization", "Bearer " + PAIRING_KEY)
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Transfer-Encoding", "chunked")
                connection.endheaders()
                response = connection.getresponse()
                self.assertIn(response.status, (400, 411, 501))
                response.read()
            finally:
                connection.close()
            self.assert_unchanged(fixture)

    def test_acknowledgement_waits_for_replacement(self):
        entered, release = threading.Event(), threading.Event()

        def replace(_links):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release replacement")

        with running_server(replace) as fixture, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(fixture.request)
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(pending.done(), "The receiver must wait until the app list is replaced")
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5), (200, {"ok": True, "count": 1}))

    def test_disconnected_sender_cannot_release_replacement_lock_early(self):
        first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def replace(links):
            calls.append(list(links))
            if len(calls) == 1:
                first_entered.set()
                if not release.wait(5):
                    raise TimeoutError("Test did not release first replacement")
            else:
                second_entered.set()

        with running_server(replace) as fixture, ThreadPoolExecutor(max_workers=1) as pool:
            body = json.dumps({"protocolVersion": 1, "links": [NEW_LINK]}).encode()
            client = socket.create_connection(("127.0.0.1", fixture.server.port), timeout=5)
            client.sendall((f"POST /v1/job-links HTTP/1.1\r\nHost: 127.0.0.1:{fixture.server.port}\r\n"
                            f"Authorization: Bearer {PAIRING_KEY}\r\nContent-Type: application/json\r\n"
                            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
            try:
                self.assertTrue(first_entered.wait(5))
                client.close()
                second = pool.submit(fixture.request)
                self.assertFalse(second_entered.wait(0.2), "A disconnected sender's callback must still hold the lock")
            finally:
                client.close()
                release.set()
            self.assertEqual(second.result(timeout=5), (200, {"ok": True, "count": 1}))
            self.assertEqual(len(calls), 2)

    def test_replacement_failure_returns_generic_error(self):
        def replace(_links):
            raise RuntimeError("private replacement details")

        with running_server(replace) as fixture:
            status, reply = fixture.request()
            self.assertEqual(status, 500)
            self.assertIn("error", reply)
            self.assertNotIn("private replacement details", json.dumps(reply))
            self.assert_unchanged(fixture)

    def test_closed_receiver_can_reopen_its_port_after_a_transfer(self):
        fixture = Fixture()
        port = fixture.server.port
        try:
            self.assertEqual(fixture.request()[0], 200)
        finally:
            fixture.close()
        received = []
        replacement = BridgeServer(PAIRING_KEY, lambda links: received.extend(links), port=port)
        try:
            replacement.start()
            self.assertEqual(replacement.port, port)
        finally:
            replacement.close()

    def test_a_second_receiver_cannot_bind_an_active_port(self):
        with running_server() as fixture:
            second = BridgeServer(PAIRING_KEY, lambda _links: None, port=fixture.server.port)
            try:
                with self.assertRaises(OSError):
                    second.start()
                self.assertEqual(fixture.request()[0], 200, "The original receiver remains usable")
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
