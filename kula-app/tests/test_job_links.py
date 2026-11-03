"""TXT link-file parsing, whole-file validation, and resume-order checks."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_links import JobLinksImportError, load_job_links


FIRST = "https://jobright.ai/jobs/info/first"
SECOND = "https://jobright.ai/jobs/info/second"


class JobLinkFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.file = self.folder / "jobs.txt"

    def tearDown(self):
        self.temp.cleanup()

    def test_link_order_and_duplicates_are_preserved_for_resume_alignment(self):
        self.file.write_text(f"  {SECOND}  \n\n{FIRST}\n{SECOND}\n", encoding="utf-8")
        self.assertEqual(load_job_links(self.file), [SECOND, FIRST, SECOND])

    def test_utf8_and_notepad_bom_encodings_are_supported(self):
        for encoding in ("utf-8", "utf-8-sig", "utf-16"):
            with self.subTest(encoding=encoding):
                self.file.write_bytes(f"{FIRST}\r\n{SECOND}\r\n".encode(encoding))
                self.assertEqual(load_job_links(self.file), [FIRST, SECOND])

    def test_jobright_urls_are_canonicalized_without_changing_line_order(self):
        self.file.write_text(
            "https://www.jobright.ai:443/jobs/info/ABCDEF0123456789ABCDEF01/?source=search#apply\n"
            f"{FIRST}\n", encoding="utf-8")
        self.assertEqual(load_job_links(self.file), [
            "https://jobright.ai/jobs/info/abcdef0123456789abcdef01", FIRST])

    def test_txt_extension_is_case_insensitive(self):
        file = self.folder / "Jobs.TXT"
        file.write_text(FIRST, encoding="utf-8")
        self.assertEqual(load_job_links(file), [FIRST])

    def test_invalid_lines_reject_the_entire_file_without_changing_it(self):
        invalid = (
            "http://jobright.ai/jobs/info/insecure",
            "https://example.com/jobs/info/offsite",
            "https://jobright.ai.example.com/jobs/info/spoofed",
            "https://user:password@jobright.ai/jobs/info/credentials",
            "https://jobright.ai:444/jobs/info/port",
            "https://jobright.ai/jobs/info/extra/path",
            "https://jobright.ai/jobs/info/encoded%2Fslash",
            "https://jobright.ai/jobs/info/", "javascript:alert(1)", "not a URL",
            "https://jobright.ai/jobs/info/" + "a" * 2050,
        )
        for link in invalid:
            with self.subTest(link=link):
                contents = f"{FIRST}\n{link}\n{SECOND}\n".encode()
                self.file.write_bytes(contents)
                with self.assertRaises(JobLinksImportError):
                    load_job_links(self.file)
                self.assertEqual(self.file.read_bytes(), contents)

    def test_empty_and_malformed_text_files_are_rejected(self):
        for contents in (b"", b"\n \r\n", b"\xff", b"\xef\xbb\xbf\n"):
            with self.subTest(contents=contents):
                self.file.write_bytes(contents)
                with self.assertRaises(JobLinksImportError):
                    load_job_links(self.file)

    def test_missing_non_file_and_non_txt_selections_are_rejected(self):
        other = self.folder / "jobs.csv"
        other.write_text(FIRST, encoding="utf-8")
        for file in (self.folder / "missing.txt", self.folder, other):
            with self.subTest(file=file), self.assertRaises(JobLinksImportError):
                load_job_links(file)

    def test_unreadable_file_is_reported_as_an_import_error(self):
        self.file.write_text(FIRST, encoding="utf-8")
        with patch.object(Path, "open", side_effect=PermissionError("Private filesystem detail")):
            with self.assertRaises(JobLinksImportError):
                load_job_links(self.file)

    def test_files_larger_than_five_mib_and_more_than_ten_thousand_links_are_rejected(self):
        for contents in (b" " * (5 * 1024 * 1024 + 1), (FIRST + "\n").encode() * 10001):
            self.file.write_bytes(contents)
            with self.assertRaises(JobLinksImportError):
                load_job_links(self.file)


if __name__ == "__main__":
    unittest.main()
