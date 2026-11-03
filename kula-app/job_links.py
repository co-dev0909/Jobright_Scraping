"""Load a complete, ordered set of Jobright links from a local text file."""

from __future__ import annotations

import codecs
from pathlib import Path
import re
from urllib.parse import urlsplit


MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_LINKS = 10_000
_JOB_PATH = re.compile(r"/jobs/info/([A-Za-z0-9_-]{1,200})/?\Z")
_HEX_ID = re.compile(r"[a-fA-F0-9]{24}\Z")


class JobLinksImportError(ValueError):
    """A TXT file could not supply a complete job list."""


def _canonical_link(text: str) -> str:
    if len(text) > 2048 or any(character.isspace() or ord(character) < 32 or ord(character) == 127
                               for character in text):
        raise ValueError("Invalid job link")
    url = urlsplit(text)
    if (url.scheme != "https" or url.hostname not in {"jobright.ai", "www.jobright.ai"}
            or url.username is not None or url.password is not None or url.port not in {None, 443}):
        raise ValueError("Invalid job link")
    match = _JOB_PATH.fullmatch(url.path)
    if match is None:
        raise ValueError("Invalid job link")
    job_id = match.group(1)
    if _HEX_ID.fullmatch(job_id):
        job_id = job_id.lower()
    return f"https://jobright.ai/jobs/info/{job_id}"


def load_job_links(path: Path | str) -> list[str]:
    """Validate the whole TXT file before returning its ordered job links.

    Blank lines are ignored. Duplicate links remain in place so each nonblank
    line still corresponds to one resume row. Files are never changed.
    """
    try:
        source = Path(path).expanduser().resolve(strict=True)
        if source.suffix.casefold() != ".txt" or not source.is_file():
            raise JobLinksImportError("Choose a TXT file containing one Jobright job link per line.")
        with source.open("rb") as file:
            data = file.read(MAX_FILE_BYTES + 1)
    except JobLinksImportError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise JobLinksImportError("Could not read the selected TXT file. Check that it exists and you can open it.") from error
    if len(data) > MAX_FILE_BYTES:
        raise JobLinksImportError("The TXT file is too large. Select a file of at most 5 MB.")
    try:
        encoding = "utf-16" if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
        text = data.decode(encoding)
    except UnicodeError as error:
        raise JobLinksImportError("Could not read the text encoding. Save the TXT file as UTF-8 or UTF-16 with a BOM.") from error

    links: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if len(links) >= MAX_LINKS:
            raise JobLinksImportError("The TXT file contains more than 10000 job links. Choose a smaller list.")
        try:
            links.append(_canonical_link(line))
        except ValueError as error:
            raise JobLinksImportError(
                f"Invalid Jobright job link on line {number}. Use one HTTPS Jobright job link per line."
            ) from error
    if not links:
        raise JobLinksImportError("No job links found. Choose a TXT file containing one Jobright job link per line.")
    return links
