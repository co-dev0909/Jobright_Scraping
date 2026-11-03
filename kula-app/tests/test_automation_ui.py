"""Start/Stop controls snapshot inputs and keep worker updates off Tk threads."""

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kula_app import KulaApp
from profile_store import ProfileStore


LINKS = ["https://jobright.ai/jobs/info/one", "https://jobright.ai/jobs/info/two"]
PDF = b"%PDF-1.7\n%%EOF\n"


class FakeBridge:
    def __init__(self):
        self.started = False
        self.closed = threading.Event()
        self.connected = {"jobright": True, "chatgpt": True}

    def start(self):
        self.started = True

    def close(self):
        self.closed.set()

    def connection_status(self):
        return dict(self.connected)


class FakeRunner:
    def __init__(self, *, block=False):
        self.block = block
        self.started = threading.Event()
        self.cancel_seen = threading.Event()
        self.inputs = None

    def run(self, links, resumes, profile, progress, cancel_event):
        self.inputs = (links, resumes, profile)
        self.started.set()
        if self.block:
            if not cancel_event.wait(5):
                raise TimeoutError("The test did not stop the runner")
            self.cancel_seen.set()
            for index in range(len(links)):
                progress(index, "stopped", "Stopped before the next application action.")
        else:
            for index in range(len(links)):
                progress(index, "submitted", "Application submission confirmed.")


@unittest.skipIf(sys.platform != "win32" and not os.environ.get("DISPLAY"),
                 "Automation UI tests require a graphical session")
class AutomationUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.store = ProfileStore(self.directory / "profile")
        self.bridge = FakeBridge()
        self.bridge_creations = 0
        self.runner = FakeRunner()
        self.runner_creations = 0
        self.root = tk.Tk()
        self.root.withdraw()

        def bridge_factory(_key):
            self.bridge_creations += 1
            return self.bridge

        def runner_factory(bridge, history_path):
            self.assertIs(bridge, self.bridge)
            self.assertEqual(history_path, self.store.directory / "application-history.json")
            self.runner_creations += 1
            return self.runner

        self.app = KulaApp(self.root, profile_store=self.store,
                           bridge_factory=bridge_factory, runner_factory=runner_factory)

    def tearDown(self):
        self.app.close()
        if self.app._run_thread is not None:
            self.app._run_thread.join(timeout=1)
        self.temp.cleanup()

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail("The expected UI state was not reached")

    def prepare(self, *, save_profile=True):
        folder = self.directory / "resumes"
        folder.mkdir(exist_ok=True)
        for name in ("name.pdf", "name (1).pdf"):
            (folder / name).write_bytes(PDF)
        self.app._apply_links(LINKS)
        self.assertTrue(self.app._apply_resume_folder(folder))
        if save_profile:
            values = self.store.load()
            values["full_name"] = "Test Person"
            self.store.save(values)

    def test_offline_startup_does_not_create_a_key_or_open_a_connection(self):
        self.root.update()
        self.assertEqual(self.bridge_creations, 0)
        self.assertFalse((self.store.directory / "pairing-key.txt").exists())
        self.assertIsNone(self.app._bridge)
        self.assertEqual([self.app.tabs.tab(tab, "text") for tab in self.app.tabs.tabs()],
                         ["Jobs", "Package", "Automation"])

    def test_start_requires_jobs_resumes_saved_name_and_both_browser_connections(self):
        self.app.start_automation()
        self.assertIn("Load job links", self.app.transfer_status.get())
        self.assertEqual(self.bridge_creations, 0)
        self.app._apply_links(LINKS)
        self.app.start_automation()
        self.assertIn("resume", self.app.transfer_status.get())
        self.prepare(save_profile=False)
        self.app.start_automation()
        self.assertIn("Full Name", self.app.transfer_status.get())
        self.assertEqual(self.bridge_creations, 0)
        profile = self.store.load()
        profile["full_name"] = "Test Person"
        self.store.save(profile)
        self.bridge.connected["chatgpt"] = False
        self.app.start_automation()
        self.assertIn("both browser extensions", self.app.transfer_status.get())
        self.assertFalse(self.app._running)
        self.assertTrue(self.bridge.started)
        self.assertEqual(self.runner_creations, 0)

    def test_changed_or_missing_resume_is_rejected_before_browser_actions(self):
        self.prepare()
        self.app.resume_paths[0].write_bytes(b"Not a PDF")
        self.app.start_automation()
        self.assertIn("readable PDF", self.app.transfer_status.get())
        self.app.resume_paths[0].unlink()
        self.app.start_automation()
        self.assertIn("could not be opened", self.app.transfer_status.get())
        self.assertEqual(self.bridge_creations, 0)
        self.assertEqual(self.runner_creations, 0)

    def test_run_uses_saved_profile_snapshot_and_displays_confirmed_job_status(self):
        self.prepare()
        self.app.package_tab.fields["full_name"].set("Unsaved Draft")
        original_links = list(self.app.links)
        original_resumes = list(self.app.resume_paths)
        self.app.start_automation()
        self.wait_for(lambda: not self.app._running)
        self.assertEqual(self.runner_creations, 1)
        links, resumes, profile = self.runner.inputs
        self.assertEqual(links, original_links)
        self.assertEqual(resumes, original_resumes)
        self.assertIsNot(links, self.app.links)
        self.assertIsNot(resumes, self.app.resume_paths)
        self.assertEqual(profile["full_name"], "Test Person")
        self.assertEqual(self.app.package_tab.fields["full_name"].get(), "Unsaved Draft")
        self.assertEqual([self.app.job_list.set(item, "status") for item in self.app.job_list.get_children()],
                         ["Submitted", "Submitted"])
        self.assertEqual(str(self.app.stop_button["state"]), "disabled")
        self.assertEqual(str(self.app.start_button["state"]), "normal")

    def test_active_run_locks_input_and_profile_save_controls_and_does_not_start_twice(self):
        self.prepare()
        self.runner.block = True
        self.app.start_automation()
        self.assertTrue(self.runner.started.wait(1))
        self.assertEqual(str(self.app.export_button["state"]), "disabled")
        self.assertEqual(str(self.app.import_button["state"]), "disabled")
        self.assertEqual(str(self.app.package_tab.save_button["state"]), "disabled")
        with patch("kula_app.filedialog.askopenfilename") as file_picker, patch("kula_app.filedialog.askdirectory") as folder_picker:
            self.app.export_job_links()
            self.app.import_resumes()
            self.app.start_automation()
        file_picker.assert_not_called()
        folder_picker.assert_not_called()
        self.assertEqual(self.runner_creations, 1)
        self.app.stop_automation()
        self.wait_for(lambda: not self.app._running)
        self.assertTrue(self.runner.cancel_seen.is_set())
        self.assertEqual(str(self.app.export_button["state"]), "normal")
        self.assertEqual(str(self.app.package_tab.save_button["state"]), "normal")
        self.assertEqual([self.app.job_list.set(item, "status") for item in self.app.job_list.get_children()],
                         ["Stopped", "Stopped"])

    def test_connection_key_setup_is_lazy_and_does_not_change_the_profile(self):
        self.prepare()
        before = self.store.load()
        self.app.copy_connection_key()
        self.assertEqual(self.bridge_creations, 1)
        self.assertTrue(self.bridge.started)
        self.assertEqual(len(self.root.clipboard_get()), 64)
        self.assertEqual(self.store.load(), before)
        self.app.copy_connection_key()
        self.assertEqual(self.bridge_creations, 1)

    def test_close_cancels_worker_and_closes_the_connection_without_waiting_on_tk(self):
        self.prepare()
        self.runner.block = True
        self.app.start_automation()
        self.assertTrue(self.runner.started.wait(1))
        self.app.close()
        self.assertTrue(self.app._run_cancel.is_set())
        self.assertTrue(self.runner.cancel_seen.wait(1))
        self.assertTrue(self.bridge.closed.wait(1))


if __name__ == "__main__":
    unittest.main()
