"""Profile persistence is local, bounded, and preserves previous saved data."""

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from profile_store import ProfileStore


DEFAULTS = {
    "full_name": "",
    "linkedin_info": "",
    "us_citizen": "Not specified",
    "not_protected_veteran": "Not specified",
    "gender": "Not specified",
    "race": "Not specified",
}
PROFILE = {
    "full_name": "Test Person",
    "linkedin_info": "https://www.linkedin.com/in/test-person/",
    "us_citizen": "Yes",
    "not_protected_veteran": "Prefer not to answer",
    "gender": "Self-described test value",
    "race": "Prefer not to answer",
}


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "profile"
        self.store = ProfileStore(self.directory)
        self.path = self.directory / "profile.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_profile_defaults_do_not_assume_demographic_answers(self):
        self.assertEqual(self.store.load(), DEFAULTS)
        self.assertFalse(self.path.exists(), "Loading defaults must not save a profile automatically")

    def test_saved_profile_round_trips_unicode_and_custom_self_identification(self):
        values = dict(PROFILE, full_name="  Test Person Ω  ", linkedin_info="  @test-person  ",
                      race="Self-described test group")
        self.store.save(values)
        expected = dict(values, full_name="Test Person Ω", linkedin_info="@test-person")
        self.assertEqual(ProfileStore(self.directory).load(), expected)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), expected)
        self.assertEqual(set(self.directory.iterdir()), {self.path}, "Saving must clean temporary files")

    def test_loaded_profile_is_independent_of_later_in_memory_edits(self):
        self.store.save(PROFILE)
        loaded = self.store.load()
        loaded["full_name"] = "Changed only in memory"
        self.assertEqual(self.store.load(), PROFILE)

    def test_invalid_values_never_replace_the_previous_saved_profile(self):
        self.store.save(PROFILE)
        previous = self.path.read_bytes()
        invalid_values = [
            dict(PROFILE, full_name="x" * 201),
            dict(PROFILE, linkedin_info="x" * 2049),
            dict(PROFILE, gender="x" * 201),
            dict(PROFILE, race="x" * 201),
            dict(PROFILE, us_citizen="Assumed yes"),
            dict(PROFILE, not_protected_veteran="Assumed no"),
            dict(PROFILE, full_name=None),
            dict(PROFILE, us_citizen=True),
            dict(PROFILE, extra_field="Unexpected data"),
        ]
        for values in invalid_values:
            with self.subTest(changed_fields=[key for key in values if values.get(key) != PROFILE.get(key)]):
                with self.assertRaises(ValueError):
                    self.store.save(values)
                self.assertEqual(self.path.read_bytes(), previous)

    def test_failed_atomic_replace_preserves_the_previous_profile(self):
        self.store.save(PROFILE)
        previous = self.path.read_bytes()
        with patch("profile_store.os.replace", side_effect=OSError("Simulated file access failure")):
            with self.assertRaises(ValueError):
                self.store.save(dict(PROFILE, full_name="Updated Test Person"))
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(set(self.directory.iterdir()), {self.path}, "Failed writes must clean temporary files")

    def test_malformed_or_oversized_existing_profiles_are_not_overwritten(self):
        self.directory.mkdir()
        malformed = [b"{", b"[]", b"null", b"\xff", b" " * 65_537,
                     json.dumps(dict(PROFILE, us_citizen=False)).encode("utf-8"),
                     json.dumps(dict(PROFILE, unexpected="value")).encode("utf-8")]
        for content in malformed:
            with self.subTest(size=len(content)):
                self.path.write_bytes(content)
                with self.assertRaises(ValueError):
                    self.store.load()
                self.assertEqual(self.path.read_bytes(), content)

    @unittest.skipIf(os.name == "nt", "Windows uses inherited per-user ACLs rather than POSIX file modes")
    def test_saved_profile_file_is_private_on_posix(self):
        self.store.save(PROFILE)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode) & 0o077, 0)

    def test_saving_and_loading_does_not_print_profile_values(self):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.store.save(PROFILE)
            self.assertEqual(self.store.load(), PROFILE)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
