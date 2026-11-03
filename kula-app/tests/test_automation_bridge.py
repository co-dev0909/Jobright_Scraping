"""Real HTTP tests for command ownership, cancellation, and no replay."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import automation_bridge
from automation_bridge import AutomationBridge, CommandError
from pairing import load_or_create_key


KEY = "a" * 64  # Public fixture key; no saved application credentials are read.
CLIENT = "a" * 32
OTHER_CLIENT = "b" * 32


class AutomationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = AutomationBridge(KEY, port=0)
        self.bridge.start()
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.poll_patch = patch.object(automation_bridge, "POLL_TIMEOUT", 0.03)
        self.poll_patch.start()

    def tearDown(self):
        self.bridge.close()
        self.pool.shutdown(wait=True)
        self.poll_patch.stop()

    def request(self, route, body, *, key=KEY, origin=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.bridge.port, timeout=5)
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        try:
            connection.request("POST", route, json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def client_body(self, role="jobright", client=CLIENT):
        return {"role": role, "client_id": client}

    def connect(self, role="jobright", client=CLIENT):
        status, result = self.request("/v1/connect", self.client_body(role, client))
        self.assertEqual((status, result), (200, {"ok": True, "role": role}))

    def poll(self, role="jobright", client=CLIENT):
        status, result = self.request("/v1/poll", self.client_body(role, client))
        self.assertEqual(status, 200)
        return result["command"]

    def command_body(self, command, role="jobright", client=CLIENT):
        return dict(self.client_body(role, client), command_id=command["id"], run_id=command["run_id"])

    def execute(self, role="jobright", **kwargs):
        return self.pool.submit(self.bridge.execute, role, "inspect", {"job": "fixture"}, run_id="run-1", **kwargs)

    def test_connection_requires_pairing_and_browser_origin(self):
        body = self.client_body()
        self.assertEqual(self.request("/v1/connect", body, key="b"*64)[0], 401)
        self.assertEqual(self.request("/v1/connect", body, origin="https://jobright.ai")[0], 403)
        self.assertEqual(self.request("/v1/connect", body, host=f"localhost:{self.bridge.port}")[0], 403)
        self.assertEqual(self.request("/v1/connect", body, origin="chrome-extension://"+"a"*32)[0], 200)
        self.assertEqual(self.request("/v1/connect", self.client_body("chatgpt", OTHER_CLIENT), key=KEY.upper())[0], 200)
        self.assertEqual(self.bridge.connection_status(), {"jobright": True, "chatgpt": True})

    def test_commands_are_claimed_once_and_results_require_exact_owner_and_run(self):
        self.connect()
        self.bridge.begin_run("run-1")
        future = self.execute()
        command = self.poll()
        self.assertEqual(command["action"], "inspect")
        self.assertEqual(command["payload"], {"job": "fixture"})
        self.assertIsNone(self.poll(), "A delivered operation must never replay on another poll")
        body = self.command_body(command)
        self.assertEqual(self.request("/v1/authorize", body), (200, {"ok": True}))
        self.assertEqual(self.request("/v1/result", dict(body, run_id="wrong", ok=True, result={}))[0], 409)
        self.assertEqual(self.request("/v1/result", dict(body, client_id=OTHER_CLIENT, ok=True, result={}))[0], 409)
        reply = dict(body, ok=True, result={"fields": ["fixture"]})
        self.assertEqual(self.request("/v1/result", reply), (200, {"ok": True}))
        self.assertEqual(future.result(timeout=2), {"fields": ["fixture"]})
        self.assertEqual(self.request("/v1/result", reply), (200, {"ok": True}), "An identical result acknowledgement is idempotent")
        self.assertEqual(self.request("/v1/result", dict(reply, result={"changed": True}))[0], 409)
        self.assertEqual(self.request("/v1/authorize", body)[0], 409)

    def test_roles_cannot_claim_each_others_commands(self):
        self.connect()
        self.connect("chatgpt", OTHER_CLIENT)
        self.bridge.begin_run("run-1")
        future = self.execute()
        self.assertIsNone(self.poll("chatgpt", OTHER_CLIENT))
        command = self.poll()
        self.assertEqual(self.request("/v1/authorize", self.command_body(command, "chatgpt", OTHER_CLIENT))[0], 409)
        body = dict(self.command_body(command), ok=True, result={"role": "jobright"})
        self.request("/v1/result", body)
        self.assertEqual(future.result(timeout=2), {"role": "jobright"})

    def test_cancel_revokes_authorization_and_rejects_late_results(self):
        self.connect()
        self.bridge.begin_run("run-1")
        future = self.execute()
        command = self.poll()
        self.bridge.cancel_run("run-1")
        with self.assertRaises(CommandError) as raised:
            future.result(timeout=2)
        self.assertTrue(raised.exception.uncertain)
        body = self.command_body(command)
        self.assertEqual(self.request("/v1/authorize", body)[0], 409)
        self.assertEqual(self.request("/v1/result", dict(body, ok=True, result={}))[0], 409)
        self.assertIsNone(self.poll())

    def test_cancel_event_removes_an_undelivered_command(self):
        self.connect()
        self.bridge.begin_run("run-1")
        stopped = threading.Event()
        future = self.execute(cancel_event=stopped)
        stopped.set()
        with self.assertRaises(CommandError) as raised:
            future.result(timeout=2)
        self.assertFalse(raised.exception.uncertain)
        self.assertIsNone(self.poll())

    def test_timeouts_distinguish_undelivered_and_delivered_commands_without_replay(self):
        self.connect()
        self.bridge.begin_run("run-1")
        with self.assertRaises(CommandError) as pending:
            self.execute(timeout=0.03).result(timeout=2)
        self.assertFalse(pending.exception.uncertain)
        self.assertIsNone(self.poll())
        future = self.execute(timeout=0.15)
        command = self.poll()
        with self.assertRaises(CommandError) as delivered:
            future.result(timeout=2)
        self.assertTrue(delivered.exception.uncertain)
        self.assertEqual(self.request("/v1/authorize", self.command_body(command))[0], 409)
        self.assertIsNone(self.poll())

    def test_other_browser_cannot_take_over_a_connected_or_busy_role(self):
        self.connect()
        self.assertEqual(self.request("/v1/connect", self.client_body(client=OTHER_CLIENT))[0], 409)
        self.bridge.begin_run("run-1")
        future = self.execute()
        command = self.poll()
        with patch.object(automation_bridge, "LEASE_SECONDS", 0.01):
            time.sleep(0.025)
            self.assertTrue(self.bridge.connection_status()["jobright"], "A long-running browser command keeps its role reservation")
            self.assertEqual(self.request("/v1/connect", self.client_body(client=OTHER_CLIENT))[0], 409)
            self.request("/v1/result", dict(self.command_body(command), ok=True, result={}))
            future.result(timeout=2)
            time.sleep(0.025)
            self.connect(client=OTHER_CLIENT)

    def test_close_wakes_pending_commands_and_polls(self):
        self.connect()
        self.bridge.begin_run("run-1")
        future = self.execute()
        self.bridge.close()
        with self.assertRaises(CommandError):
            future.result(timeout=2)
        self.assertEqual(self.bridge.connection_status(), {"jobright": False, "chatgpt": False})

    def test_failed_browser_result_is_reported_and_cannot_reauthorize(self):
        self.connect()
        self.bridge.begin_run("run-1")
        future = self.execute()
        command = self.poll()
        body = dict(self.command_body(command), ok=False, result={}, error="A required answer is missing.")
        self.assertEqual(self.request("/v1/result", body)[0], 200)
        with self.assertRaisesRegex(CommandError, "required answer"):
            future.result(timeout=2)
        self.assertEqual(self.request("/v1/authorize", self.command_body(command))[0], 409)

    def test_pairing_key_persists_and_preserves_invalid_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = load_or_create_key(path)
            self.assertRegex(first, r"^[a-fA-F0-9]{64}$")
            self.assertEqual(load_or_create_key(path), first)
            key_file = path / "pairing-key.txt"
            key_file.write_text("invalid")
            with self.assertRaises(ValueError):
                load_or_create_key(path)
            self.assertEqual(key_file.read_text(), "invalid")


if __name__ == "__main__":
    unittest.main()
