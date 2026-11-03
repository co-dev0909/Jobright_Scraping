"""Resume folder validation and deterministic job matching checks."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resumes import ResumeImportError, discover_resume_pdfs


PDF = b"%PDF-1.7\n% local test fixture\n%%EOF\n"


class ResumeFolderTests(unittest.TestCase):
    def test_original_resume_precedes_naturally_numbered_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            names = ("name (10).pdf", "name (2).pdf", "name.pdf", "name (1).pdf")
            for name in names:
                (folder / name).write_bytes(PDF)
            self.assertEqual([path.name for path in discover_resume_pdfs(folder, 4)],
                             ["name.pdf", "name (1).pdf", "name (2).pdf", "name (10).pdf"])

    def test_resume_paths_are_paired_with_links_in_natural_filename_order(self):
        from resumes import assign_resumes
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            expected = ("name.pdf", "name (1).pdf", "name (2).pdf", "name (10).pdf")
            for name in reversed(expected):
                (folder / name).write_bytes(PDF)
            links = [f"https://jobright.ai/jobs/info/job-{index}" for index in range(4)]
            self.assertEqual(assign_resumes(folder, links), [(folder / name).resolve() for name in expected])

    def test_direct_pdfs_are_case_insensitive_naturally_sorted_and_absolute(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("10.PDF", "02.pdf", "1.pdf"):
                (folder / name).write_bytes(PDF)
            (folder / "notes.txt").write_text("Not a resume")
            (folder / "nested").mkdir()
            (folder / "nested" / "extra.pdf").write_bytes(PDF)
            (folder / "looks-like-a-file.pdf").mkdir()
            found = discover_resume_pdfs(folder, 3)
            self.assertEqual([path.name for path in found], ["1.pdf", "02.pdf", "10.PDF"])
            self.assertTrue(all(path.is_absolute() for path in found))

    def test_too_few_and_too_many_pdfs_report_actual_and_expected_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "01.pdf").write_bytes(PDF)
            for expected in (2, 3):
                with self.subTest(expected=expected), self.assertRaisesRegex(ResumeImportError, rf"Found 1.*{expected} job links"):
                    discover_resume_pdfs(folder, expected)
            (folder / "02.pdf").write_bytes(PDF)
            with self.assertRaisesRegex(ResumeImportError, r"Found 2.*1 job links"):
                discover_resume_pdfs(folder, 1)

    def test_jobs_are_required_before_importing(self):
        with tempfile.TemporaryDirectory() as directory:
            for count in (0, -1, True):
                with self.subTest(count=count), self.assertRaisesRegex(ResumeImportError, "job links"):
                    discover_resume_pdfs(directory, count)

    def test_missing_empty_or_non_directory_selections_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            file = folder / "01.pdf"
            file.write_bytes(PDF)
            for selected in ("", " ", folder / "missing", file):
                with self.subTest(selected=str(selected)), self.assertRaises(ResumeImportError):
                    discover_resume_pdfs(selected, 1)

    def test_a_pdf_extension_without_pdf_contents_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original = {"01.pdf": PDF, "02.pdf": b"This is not PDF content"}
            for name, contents in original.items():
                (folder / name).write_bytes(contents)
            with self.assertRaisesRegex(ResumeImportError, r"02\.pdf.*PDF header"):
                discover_resume_pdfs(folder, 2)
            self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, original)

    def test_unreadable_pdf_reports_the_file_without_a_partial_import(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("01.pdf", "02.pdf"):
                (folder / name).write_bytes(PDF)
            original_open = Path.open

            def open_file(path, *args, **kwargs):
                if path.name == "02.pdf":
                    raise PermissionError("Private filesystem details")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", open_file), self.assertRaisesRegex(ResumeImportError, r"Could not read.*02\.pdf"):
                discover_resume_pdfs(folder, 2)

    def test_an_inaccessible_folder_has_a_useful_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("resumes.os.scandir", side_effect=PermissionError("Private filesystem details")):
                with self.assertRaisesRegex(ResumeImportError, "accessible resume folder"):
                    discover_resume_pdfs(directory, 1)


if __name__ == "__main__":
    unittest.main()
