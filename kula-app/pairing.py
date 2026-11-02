"""Keep Kula APP's pairing identity stable across desktop app versions."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import tempfile


def _read_existing(path: Path) -> str:
    key = path.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", key) is None:
        raise ValueError("The saved pairing key is invalid.")
    return key


def load_or_create_key(directory: Path | None = None) -> str:
    """Return the existing 64-hex key, or atomically create it exactly once.

    The Windows path and format are shared with the earlier .NET app so an
    upgrade does not require pairing the browser again. Existing invalid files
    are left untouched rather than silently rotating a paired identity.
    """
    if directory is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        directory = (Path(local_app_data) if local_app_data else Path.home() / ".local" / "share") / "KulaAPP"
    else:
        directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "pairing-key.txt"
    if path.exists():
        return _read_existing(path)

    key = secrets.token_hex(32)
    temporary: Path | None = None
    try:
        # Write and flush a private temporary file before making it visible at
        # the final name. A second instance can never read a half-written key.
        with tempfile.NamedTemporaryFile(mode="wb", prefix="pairing-key-", suffix=".tmp", dir=directory, delete=False) as file:
            temporary = Path(file.name)
            file.write(key.encode("ascii"))
            file.flush()
            os.fsync(file.fileno())
        try:
            if os.name == "nt":
                # Windows rename is atomic and refuses an existing destination.
                os.rename(temporary, path)
            else:
                # POSIX rename would replace another instance's key; linking
                # instead publishes the complete file with no overwrite.
                os.link(temporary, path)
        except FileExistsError:
            return _read_existing(path)
        return key
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
