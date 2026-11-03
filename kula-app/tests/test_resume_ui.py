"""Graphical checks for importing resumes without losing existing pairings."""

import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kula_app import KulaApp
from profile_store import ProfileStore


PDF = b"%PDF-1.7\n% local test fixture\n%%EOF\n"
LINKS = ["https://jobright.ai/jobs/info/job-one", "https://jobright.ai/jobs/info/job-two"]


@unittest.skipIf(sys.platform != "win32" and not os.environ.get("DISPLAY"), "Resume UI tests require a graphical session")
class ResumeUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / "resumes"
        self.folder.mkdir()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = KulaApp(self.root, profile_store=ProfileStore(Path(self.temp.name) / "profile"))
        for name in ("01.pdf", "02.pdf"):
            (self.folder / name).write_bytes(PDF)

    def tearDown(self):
        self.app.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                if not self.root.winfo_exists():
                    break
                self.root.update()
            except tk.TclError:
                break
            time.sleep(0.01)
        self.temp.cleanup()

    def rows(self):
        return [tuple(str(value) for value in self.app.job_list.item(item, "values"))
                for item in self.app.job_list.get_children()]

    def bindings(self):
        return list(self.app.links), list(self.app.resume_paths), self.app.resume_folder, self.rows()

    def import_current_folder(self):
        self.app._apply_links(LINKS)
        with patch("kula_app.filedialog.askdirectory", return_value=str(self.folder)) as picker:
            self.app.import_resumes()
        picker.assert_called_once()

    def test_import_requires_jobs_and_shows_each_job_resume_pair(self):
        self.assertEqual(str(self.app.import_button["state"]), "disabled")
        self.import_current_folder()
        expected = [(self.folder / name).resolve() for name in ("01.pdf", "02.pdf")]
        self.assertEqual(self.app.resume_paths, expected)
        self.assertEqual(self.app.resume_folder, self.folder.resolve())
        self.assertEqual(self.rows(), [("1", LINKS[0], "01.pdf", "Pending"), ("2", LINKS[1], "02.pdf", "Pending")])
        self.assertIn("2", self.app.resume_status.get())
        self.assertEqual(str(self.app.import_button["state"]), "normal")
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["01.pdf", "02.pdf"])

    def test_cancelling_picker_preserves_existing_resume_bindings(self):
        self.import_current_folder()
        before = self.bindings()
        status = self.app.resume_status.get()
        with patch("kula_app.filedialog.askdirectory", return_value=""):
            self.app.import_resumes()
        self.assertEqual(self.bindings(), before)
        self.assertEqual(self.app.resume_status.get(), status)

    def test_wrong_count_or_invalid_pdf_preserves_previous_import(self):
        self.import_current_folder()
        before = self.bindings()
        with tempfile.TemporaryDirectory() as invalid:
            folder = Path(invalid)
            (folder / "01.pdf").write_bytes(PDF)
            with patch("kula_app.filedialog.askdirectory", return_value=str(folder)):
                self.app.import_resumes()
            self.assertEqual(self.bindings(), before)
            self.assertRegex(self.app.resume_status.get(), r"Found 1.*2 job links")
            (folder / "02.pdf").write_bytes(b"Not a PDF")
            with patch("kula_app.filedialog.askdirectory", return_value=str(folder)):
                self.app.import_resumes()
            self.assertEqual(self.bindings(), before)
            self.assertIn("02.pdf", self.app.resume_status.get())

    def test_receiving_a_new_batch_clears_resumes_even_for_the_same_links(self):
        self.import_current_folder()
        self.app._apply_links(LINKS)
        self.assertEqual(self.app.resume_paths, [])
        self.assertIsNone(self.app.resume_folder)
        self.assertEqual(self.rows(), [("1", LINKS[0], "Not imported", "Pending"), ("2", LINKS[1], "Not imported", "Pending")])
        self.assertEqual(str(self.app.import_button["state"]), "normal")

    def test_new_links_while_picker_is_open_prevent_stale_resume_assignment(self):
        self.import_current_folder()
        new_links = ["https://jobright.ai/jobs/info/new-one", "https://jobright.ai/jobs/info/new-two"]

        def choose_folder(**_kwargs):
            self.app._apply_links(new_links)
            return str(self.folder)

        with patch("kula_app.filedialog.askdirectory", side_effect=choose_folder):
            self.app.import_resumes()
        self.assertEqual(self.app.links, new_links)
        self.assertEqual(self.app.resume_paths, [])
        self.assertIsNone(self.app.resume_folder)
        self.assertRegex(self.app.resume_status.get().lower(), r"chang|again")

    def test_receiving_the_same_batch_while_picker_is_open_also_rejects_stale_import(self):
        self.import_current_folder()

        def choose_folder(**_kwargs):
            self.app._apply_links(LINKS)
            return str(self.folder)

        with patch("kula_app.filedialog.askdirectory", side_effect=choose_folder):
            self.app.import_resumes()
        self.assertEqual(self.app.resume_paths, [])
        self.assertIsNone(self.app.resume_folder)
        self.assertRegex(self.app.resume_status.get().lower(), r"chang|again")


if __name__ == "__main__":
    unittest.main()
