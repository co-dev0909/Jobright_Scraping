"""Real local command-channel runs with two fixture browser clients.

These clients return public fixture form data; no real application is opened.
"""

from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation_bridge import AutomationBridge
from automation_runner import AutomationRunner


KEY = "a" * 64
URL = "https://jobright.ai/jobs/info/111111111111111111111111"


class BrowserFixture:
    def __init__(self, bridge, role, *, unknown=False):
        self.bridge, self.role, self.unknown = bridge, role, unknown
        self.identifier = uuid.uuid4().hex
        self.stopped = threading.Event()
        self.events = []
        self.errors = []
        self.post("/v1/connect", {})
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.thread.start()

    def post(self, route, body):
        connection = HTTPConnection("127.0.0.1", self.bridge.port, timeout=12)
        try:
            connection.request("POST", route,
                               json.dumps(dict(body, role=self.role, client_id=self.identifier)),
                               {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}",
                                "Origin": "chrome-extension://" + "a" * 32})
            response = connection.getresponse()
            value = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(f"Fixture command rejected: HTTP {response.status}")
            return value
        finally:
            connection.close()

    def poll(self):
        try:
            while not self.stopped.is_set():
                command = self.post("/v1/poll", {}).get("command")
                if command is None:
                    continue
                self.post("/v1/authorize", {"command_id": command["id"], "run_id": command["run_id"]})
                self.events.append(command["action"])
                payload = command["payload"]
                if command["action"] == "open_application":
                    result = {"tab_id": 7}
                elif command["action"] == "inspect_form":
                    result = {"url": "https://example.com/apply", "form_token": "fixture-form",
                              "next_action": "submit", "submit_ready": True, "context": "Public fixture job",
                              "fields": [
                                  {"id": "name", "label": "Full name", "type": "text", "required": True},
                                  {"id": "summary", "label": "Describe your experience", "type": "textarea", "required": True},
                                  {"id": "resume", "label": "Resume PDF", "type": "file", "required": True},
                              ]}
                elif command["action"] == "answer_questions":
                    result = {"request_id": payload["request_id"], "unknown_fields": ["summary"] if self.unknown else [],
                              "answers": [] if self.unknown else [
                                  {"field_id": "summary", "value": "Public fixture resume experience.", "source": "resume"}]}
                elif command["action"] == "fill_form":
                    if payload["resume"]["mime"] != "application/pdf":
                        raise AssertionError("The matching PDF was not routed to the application browser")
                    result = {"ok": True, "validation_errors": [], "resume_uploaded": True,
                              "resume_sha256": payload["resume"]["sha256"]}
                elif command["action"] == "advance":
                    if payload["mode"] != "submit":
                        raise AssertionError("Unexpected fixture application navigation")
                    result = {"submitted": True}
                else:
                    raise AssertionError("Unexpected fixture command")
                self.post("/v1/result", {"command_id": command["id"], "run_id": command["run_id"],
                                         "ok": True, "result": result})
        except Exception as error:
            if not self.stopped.is_set():
                self.errors.append(error)


class AutomationIntegrationTests(unittest.TestCase):
    def exercise(self, unknown=False):
        with tempfile.TemporaryDirectory() as directory:
            resume = Path(directory) / "resume.pdf"
            resume.write_bytes(b"%PDF-1.7\nPublic fixture resume experience.\n%%EOF\n")
            history = Path(directory) / "history.json"
            bridge = AutomationBridge(KEY, port=0)
            bridge.start()
            clients = [BrowserFixture(bridge, "jobright"), BrowserFixture(bridge, "chatgpt", unknown=unknown)]
            cancel = threading.Event()
            progress, errors = [], []
            runner = AutomationRunner(bridge, history_path=history)

            def run():
                try:
                    runner.run([URL], [resume], {"full_name": "Test Applicant"},
                               lambda *event: progress.append(event), cancel)
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=run, daemon=True)
            try:
                thread.start()
                thread.join(8)
                self.assertFalse(thread.is_alive(), "The fixture run did not finish")
                self.assertFalse(errors)
                self.assertTrue(all(not client.errors for client in clients))
                self.assertIn("answer_questions", clients[1].events)
                self.assertEqual(progress[-1][1], "needs_attention" if unknown else "submitted")
                if unknown:
                    self.assertNotIn("advance", clients[0].events)
                    self.assertNotIn("fill_form", clients[0].events)
                    self.assertFalse(history.exists())
                else:
                    self.assertEqual(clients[0].events.count("advance"), 1)
                    self.assertEqual(json.loads(history.read_text())["jobs"][URL]["status"], "submitted")
                    runner.run([URL], [resume], {"full_name": "Test Applicant"},
                               lambda *event: progress.append(event), cancel)
                    self.assertEqual(progress[-1][1], "skipped")
                    self.assertEqual(clients[0].events.count("advance"), 1)
            finally:
                cancel.set()
                for client in clients:
                    client.stopped.set()
                bridge.close()
                thread.join(2)
                for client in clients:
                    client.thread.join(2)

    def test_two_real_http_browser_roles_complete_one_application_and_history_blocks_replay(self):
        self.exercise()

    def test_unknown_chatgpt_answer_prevents_filling_or_submission(self):
        self.exercise(unknown=True)
