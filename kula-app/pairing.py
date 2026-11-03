"""Persist the local automation pairing key without replacing an existing key."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import tempfile


def _read_existing(path: Path) -> str:
    with path.open("r", encoding="utf-8") as file:
        value = file.read(1024).strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("The saved automation pairing key is invalid.")
    return value


def load_or_create_key(directory: Path | None = None) -> str:
    if directory is None:
        local = os.environ.get("LOCALAPPDATA")
        directory = (Path(local) if local else Path.home() / ".local" / "share") / "KulaAPP"
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / "pairing-key.txt"
    if destination.exists():
        return _read_existing(destination)

    value = secrets.token_hex(32)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=directory, prefix="pairing-key-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(value.encode("ascii"))
            file.flush()
            os.fsync(file.fileno())
        try:
            if os.name == "nt":
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        except FileExistsError:
            return _read_existing(destination)
        return value
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
