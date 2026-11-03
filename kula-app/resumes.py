"""Discover and validate a complete local folder of resume PDFs.

Discovery does not decide which job belongs to a PDF. Callers must apply their
chosen matching rule after this function validates the entire folder.
"""

from __future__ import annotations

import os
from pathlib import Path
import re


class ResumeImportError(ValueError):
    """A folder cannot supply a complete, usable resume import."""


def _natural_filename_key(path: Path) -> tuple:
    # Group Windows-style numbered copies with their original: name.pdf,
    # name (1).pdf, name (2).pdf, ... name (10).pdf. Comparing the full names
    # directly would put the space before "(1)" ahead of the original's dot.
    copy = re.fullmatch(r"(.+?)\s+\(([0-9]+)\)", path.stem)
    base = copy.group(1) if copy else path.stem
    copy_number = int(copy.group(2)) if copy else -1
    # Tag components to compare arbitrary filenames and numerical prefixes.
    pieces = tuple((1, int(piece)) if re.fullmatch(r"[0-9]+", piece) else (0, piece.casefold())
                   for piece in re.split(r"([0-9]+)", base))
    return pieces, copy_number, path.name.casefold(), path.name


def discover_resume_pdfs(folder: Path | str, expected_count: int) -> list[Path]:
    """Return naturally sorted absolute PDF paths after validating all files.

    Only regular files immediately inside the selected folder are considered;
    subdirectories, symlinks, and other file extensions are ignored. File
    extensions are case-insensitive. The lightweight PDF header check confirms
    the expected file signature, not document validity or ATS suitability.

    A failed import raises :class:`ResumeImportError`; no partial result is
    returned and none of the user's files are changed.
    """
    if type(expected_count) is not int or expected_count < 1:
        raise ResumeImportError("Receive job links in Kula APP before importing resumes.")
    if isinstance(folder, str) and not folder.strip():
        raise ResumeImportError("Select a folder containing your resume PDFs.")

    try:
        directory = Path(folder).expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise ResumeImportError("Select a folder containing your resume PDFs.")
        with os.scandir(directory) as entries:
            pdfs = [Path(entry.path) for entry in entries
                    if Path(entry.name).suffix.casefold() == ".pdf"
                    and entry.is_file(follow_symlinks=False)]
    except ResumeImportError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise ResumeImportError("Could not read the selected folder. Choose an accessible resume folder.") from error

    if len(pdfs) != expected_count:
        raise ResumeImportError(
            f"Found {len(pdfs)} resume PDF files, but there are {expected_count} job links. "
            "Choose a folder with exactly one PDF for each job link."
        )

    pdfs.sort(key=_natural_filename_key)
    for pdf in pdfs:
        try:
            with pdf.open("rb") as file:
                header = file.read(5)
        except OSError as error:
            raise ResumeImportError(f"Could not read resume PDF: {pdf.name}. Close it if needed and check access.") from error
        if header != b"%PDF-":
            raise ResumeImportError(f"The file {pdf.name} does not have a PDF header. Choose PDF resume files.")
    return pdfs


def assign_resumes(folder: Path | str, links: list[str]) -> list[Path]:
    """Pair naturally ordered local PDFs with the displayed job-link order.

    File counts and PDF headers are checked for the whole folder first. Names
    provide an ordering rule, not evidence that a resume suits a particular job;
    the desktop table shows each pairing for the user to review.
    """
    return discover_resume_pdfs(folder, len(links))
