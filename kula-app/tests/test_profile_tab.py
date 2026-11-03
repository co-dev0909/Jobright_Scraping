"""Graphical checks for explicit profile entry, saving, and error recovery."""

import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from profile_store import ProfileStore, ProfileStoreError
from profile_tab import ProfileTab


PROFILE = {
    "full_name": "Test Person",
    "linkedin_info": "https://www.linkedin.com/in/test-person/",
    "us_citizen": "No",
    "not_protected_veteran": "Yes",
    "gender": "Self-described test value",
    "race": "Prefer not to answer",
}


@unittest.skipIf(sys.platform != "win32" and not os.environ.get("DISPLAY"),
                 "Profile UI tests require a graphical session")
class ProfileTabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "profile"
        self.store = ProfileStore(self.directory)
        self.root = tk.Tk()
        self.root.withdraw()
        self.tab = ProfileTab(self.root, store=self.store)
        self.tab.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.temp.cleanup()

    def values(self):
        return {key: variable.get() for key, variable in self.tab.fields.items()}

    def enter(self, values):
        for key, value in values.items():
            self.tab.fields[key].set(value)

    def test_new_profile_has_only_user_controlled_unspecified_answers(self):
        self.assertEqual(set(self.tab.fields), set(PROFILE))
        self.assertEqual(self.values(), {
            "full_name": "", "linkedin_info": "", "us_citizen": "Not specified",
            "not_protected_veteran": "Not specified", "gender": "Not specified", "race": "Not specified",
        })
        self.assertFalse((self.directory / "profile.json").exists())

    def test_profile_is_saved_and_loaded_when_the_tab_is_reopened(self):
        self.enter(PROFILE)
        self.tab.save_profile()
        self.assertEqual(self.store.load(), PROFILE)
        self.assertRegex(self.tab.status.get().lower(), r"saved")
        self.tab.destroy()
        self.tab = ProfileTab(self.root, store=ProfileStore(self.directory))
        self.tab.pack()
        self.assertEqual(self.values(), PROFILE)

    def test_failed_save_preserves_the_existing_profile_and_form_edits(self):
        self.store.save(PROFILE)
        edited = dict(PROFILE, full_name="Edited Test Person")
        self.enter(edited)
        with patch.object(self.store, "save", side_effect=ProfileStoreError("Could not save profile.")):
            self.tab.save_profile()
        self.assertEqual(self.store.load(), PROFILE)
        self.assertEqual(self.values(), edited, "A failed save must not discard the user's form edits")
        self.assertRegex(self.tab.status.get().lower(), r"could|failed|save|error")

    def test_invalid_saved_file_is_reported_without_replacing_it_with_defaults(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "profile.json"
        path.write_bytes(b"{not valid json")
        self.tab.destroy()
        self.tab = ProfileTab(self.root, store=self.store)
        self.tab.pack()
        self.assertEqual(path.read_bytes(), b"{not valid json")
        self.assertRegex(self.tab.status.get().lower(), r"could|invalid|failed|error")
        self.assertEqual(self.values()["us_citizen"], "Not specified")


if __name__ == "__main__":
    unittest.main()
