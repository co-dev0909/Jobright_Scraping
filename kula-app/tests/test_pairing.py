"""Pairing key persistence remains compatible with the original Windows app."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pairing import load_or_create_key


class PairingTests(unittest.TestCase):
    def test_new_key_is_persisted_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            key = load_or_create_key(location)
            self.assertRegex(key, r"\A[0-9a-fA-F]{64}\Z")
            self.assertEqual((location / "pairing-key.txt").read_text().strip(), key)
            self.assertEqual(load_or_create_key(location), key)

    def test_original_uppercase_and_lowercase_keys_remain_compatible(self):
        for key in ("ABCDEF0123456789" * 4, "abcdef0123456789" * 4):
            with self.subTest(case="upper" if key.isupper() else "lower"), tempfile.TemporaryDirectory() as directory:
                location = Path(directory)
                saved = key + "\n"
                (location / "pairing-key.txt").write_text(saved)
                self.assertEqual(load_or_create_key(location), key)
                self.assertEqual((location / "pairing-key.txt").read_text(), saved)

    def test_invalid_existing_key_is_not_replaced(self):
        for invalid in ("", "a" * 63, "g" * 64, "not a pairing key"):
            with self.subTest(length=len(invalid)), tempfile.TemporaryDirectory() as directory:
                location = Path(directory)
                (location / "pairing-key.txt").write_text(invalid)
                with self.assertRaises((ValueError, OSError)):
                    load_or_create_key(location)
                self.assertEqual((location / "pairing-key.txt").read_text(), invalid)

    def test_simultaneous_first_loads_share_one_persisted_key(self):
        with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=6) as pool:
            location = Path(directory)
            keys = list(pool.map(lambda _index: load_or_create_key(location), range(12)))
            self.assertEqual(len(set(keys)), 1)
            self.assertEqual((location / "pairing-key.txt").read_text().strip(), keys[0])


if __name__ == "__main__":
    unittest.main()
