"""Local, atomic persistence for the profile entered in Kula APP."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


DEFAULT_PROFILE = {
    "full_name": "",
    "linkedin_info": "",
    "us_citizen": "Not specified",
    "not_protected_veteran": "Not specified",
    "gender": "Not specified",
    "race": "Not specified",
}
CITIZENSHIP_CHOICES = ("Not specified", "Yes", "No")
VETERAN_CHOICES = (*CITIZENSHIP_CHOICES, "Prefer not to answer")
MAX_PROFILE_BYTES = 65_536
_LIMITS = {"full_name": 200, "linkedin_info": 2048,
           "us_citizen": 32, "not_protected_veteran": 32,
           "gender": 200, "race": 200}
_LABELS = {"full_name": "Full Name", "linkedin_info": "LinkedIn Info",
           "us_citizen": "US Citizen", "not_protected_veteran": "Not a protected veteran",
           "gender": "Gender", "race": "Race"}


class ProfileStoreError(ValueError):
    """A profile could not be validated, loaded, or saved."""


def _normalize_profile(values: object) -> dict[str, str]:
    if not isinstance(values, dict) or set(values) != set(DEFAULT_PROFILE):
        raise ProfileStoreError("The profile must contain all six supported fields.")
    profile = {}
    for name, limit in _LIMITS.items():
        value = values[name]
        if not isinstance(value, str):
            raise ProfileStoreError(f"{_LABELS[name]} must be text.")
        value = value.strip()
        if name in {"gender", "race"} and not value:
            value = "Not specified"
        if len(value) > limit:
            raise ProfileStoreError(f"{_LABELS[name]} must be {limit} characters or fewer.")
        profile[name] = value
    if profile["us_citizen"] not in CITIZENSHIP_CHOICES:
        raise ProfileStoreError("Choose a listed US Citizen option.")
    if profile["not_protected_veteran"] not in VETERAN_CHOICES:
        raise ProfileStoreError("Choose a listed Not a protected veteran option.")
    return profile


class ProfileStore:
    """Store the user's explicit answers on this computer, without networking."""

    def __init__(self, directory: Path | None = None) -> None:
        if directory is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            directory = ((Path(local_app_data) if local_app_data else Path.home() / ".local" / "share")
                         / "KulaAPP")
        self.directory = Path(directory)
        self.path = self.directory / "profile.json"

    def load(self) -> dict[str, str]:
        try:
            with self.path.open("rb") as file:
                data = file.read(MAX_PROFILE_BYTES + 1)
        except FileNotFoundError:
            return dict(DEFAULT_PROFILE)
        except OSError as error:
            raise ProfileStoreError("Could not read your saved profile. Check access to the Kula APP data folder.") from error
        if len(data) > MAX_PROFILE_BYTES:
            raise ProfileStoreError("The saved profile is too large. Its file was left unchanged.")
        try:
            return _normalize_profile(json.loads(data.decode("utf-8")))
        except (UnicodeError, ValueError, RecursionError) as error:
            raise ProfileStoreError("The saved profile is invalid. Its file was left unchanged.") from error

    def save(self, values: dict[str, str]) -> None:
        profile = _normalize_profile(values)
        data = (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(data) > MAX_PROFILE_BYTES:
            raise ProfileStoreError("The profile is too large to save.")
        temporary: Path | None = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Temporary files are private (0600 on POSIX). On Windows the
            # directory inherits the current user's LocalAppData permissions.
            with tempfile.NamedTemporaryFile(mode="wb", prefix="profile-", suffix=".tmp",
                                             dir=self.directory, delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise ProfileStoreError("Could not save your profile. Check access to the Kula APP data folder and try again.") from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
