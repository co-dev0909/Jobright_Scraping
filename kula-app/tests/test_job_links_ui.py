"""File-picker replacement preserves jobs and resumes until validation succeeds."""

import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kula_app import KulaApp
from profile_store import ProfileStore


LINKS = ["https://jobright.ai/jobs/info/one", "https://jobright.ai/jobs/info/two"]
PDF = b"%PDF-1.7\n%%EOF\n"


@unittest.skipIf(sys.platform != "win32" and not os.environ.get("DISPLAY"),
                 "Job-link UI tests require a graphical session")
class JobLinkUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.txt = self.directory / "jobs.txt"
        self.txt.write_text("\n".join(LINKS), encoding="utf-8")
        self.resumes = self.directory / "resumes"
        self.resumes.mkdir()
        for name in ("name.pdf", "name (1).pdf"):
            (self.resumes / name).write_bytes(PDF)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = KulaApp(self.root, profile_store=ProfileStore(self.directory / "profile"))

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def rows(self):
        return [tuple(str(value) for value in self.app.job_list.item(item, "values"))
                for item in self.app.job_list.get_children()]

    def state(self):
        return list(self.app.links), list(self.app.resume_paths), self.app.resume_folder, self.rows()

    def load_with_resumes(self):
        self.assertTrue(self.app._apply_job_links_file(self.txt))
        self.assertTrue(self.app._apply_resume_folder(self.resumes))

    def test_export_job_links_opens_txt_picker_and_replaces_the_list_in_file_order(self):
        self.txt.write_text("\n".join([LINKS[1], LINKS[0], LINKS[1]]), encoding="utf-8")
        self.assertEqual(str(self.app.export_button["text"]), "Export job links")
        with patch("kula_app.filedialog.askopenfilename", return_value=str(self.txt)) as picker:
            self.app.export_job_links()
        picker.assert_called_once()
        self.assertEqual(self.app.links, [LINKS[1], LINKS[0], LINKS[1]])
        self.assertEqual([row[1] for row in self.rows()], self.app.links)
        self.assertEqual([row[0] for row in self.rows()], ["1", "2", "3"])
        self.assertEqual(str(self.app.import_button["state"]), "normal")

    def test_cancelling_file_selection_preserves_jobs_and_resume_assignments(self):
        self.load_with_resumes()
        before = self.state()
        with patch("kula_app.filedialog.askopenfilename", return_value=""):
            self.app.export_job_links()
        self.assertEqual(self.state(), before)

    def test_invalid_or_missing_file_preserves_the_complete_previous_state(self):
        self.load_with_resumes()
        before = self.state()
        for contents in ("", "not a job URL", LINKS[0] + "\nhttps://example.com/jobs/info/no"):
            self.txt.write_text(contents, encoding="utf-8")
            with patch("kula_app.filedialog.askopenfilename", return_value=str(self.txt)):
                self.app.export_job_links()
            self.assertEqual(self.state(), before)
        self.assertFalse(self.app._apply_job_links_file(self.directory / "missing.txt"))
        self.assertEqual(self.state(), before)

    def test_successful_file_load_clears_resumes_even_when_job_links_are_unchanged(self):
        self.load_with_resumes()
        self.assertTrue(self.app._apply_job_links_file(self.txt))
        self.assertEqual(self.app.links, LINKS)
        self.assertEqual(self.app.resume_paths, [])
        self.assertIsNone(self.app.resume_folder)
        self.assertTrue(all(row[2] == "Not imported" for row in self.rows()))

    def test_loading_job_links_keeps_the_saved_profile(self):
        self.app.package_tab.fields["full_name"].set("Test Person")
        self.app.package_tab.save_profile()
        self.assertTrue(self.app._apply_job_links_file(self.txt))
        self.assertEqual(self.app.package_tab.store.load()["full_name"], "Test Person")
        self.assertEqual(self.app.package_tab.fields["full_name"].get(), "Test Person")

    def test_reentrant_file_picker_does_not_start_a_second_dialog(self):
        def choose(**_kwargs):
            self.app.export_job_links()
            return str(self.txt)
        with patch("kula_app.filedialog.askopenfilename", side_effect=choose) as picker:
            self.app.export_job_links()
        picker.assert_called_once()
        self.assertEqual(self.app.links, LINKS)
        self.assertEqual(str(self.app.export_button["state"]), "normal")


if __name__ == "__main__":
    unittest.main()
