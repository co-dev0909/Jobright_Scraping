"""Sequential application preparation/submission with a durable no-retry ledger.

Browser pages and model replies are untrusted inputs. This module accepts only
the requested field schema, requires explicit profile answers for self-ID fields,
and records a potentially submitted job before asking a browser to submit it.
"""

from __future__ import annotations

import base64
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Callable
from urllib.parse import urlsplit
import uuid

from job_links import _canonical_link
from automation_bridge import CommandError


MAX_RESUME_BYTES = 8 * 1024 * 1024
MAX_FORM_FIELDS = 100
MAX_FORM_PAGES = 8
MAX_HISTORY_BYTES = 16 * 1024 * 1024
_UNSPECIFIED = {"", "not specified"}
_FIELD_TYPES = {"text", "textarea", "email", "tel", "url", "number", "date",
                "select", "select-one", "select-multiple", "radio", "checkbox", "file", "password"}
_MANUAL_LABEL = re.compile(
    r"\b(sponsor(?:ship|ed|ing)?|work authori[sz](?:ation|ed)?|legally authori[sz]ed|visa|"
    r"authori[sz](?:ed|ation).{0,30}work|eligible to work|right to work|work permit|"
    r"relocat(?:e|ion|ing)|salary|compensation|travel|availability|start date|notice period|"
    r"criminal|convict(?:ion|ions|ed)?|felon(?:y)?|disabilit(?:y|ies)|birth(?:date|day)?|dob|age|"
    r"social security|ssn|signature|certif(?:y|ication)|"
    r"consent|agree(?:ment)?|acknowledge(?:ment)?|terms|background check|minimum age|at least \d+ years old)\b", re.I)


class AutomationError(ValueError):
    """A run cannot start without changing or verifying its local inputs."""


class _NeedsAttention(Exception):
    pass


class _Stopped(Exception):
    pass


def _normal(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _known(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() not in _UNSPECIFIED


def _filled(field: dict) -> bool:
    value = field.get("current_value", field.get("value"))
    if field["type"] == "file":
        return field.get("existing_file") is True
    if field["type"] == "checkbox":
        return value is True or field.get("checked") is True
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, str) and item.strip() for item in value)
    return isinstance(value, str) and bool(value.strip())


def _profile_policy(field: dict, profile: dict) -> tuple[bool, list[object]]:
    """Return (must-use-explicit-profile, possible explicitly supplied answers)."""
    label = _normal(field["label"])
    if _MANUAL_LABEL.search(field["label"]) or field["type"] == "password":
        return True, []
    if re.search(r"\b(gender|sex)\b", label):
        value = profile.get("gender")
        choices = [value] if _known(value) else []
        if value == "Prefer not to answer":
            choices.extend(["Decline to self identify", "I do not wish to answer"])
        return True, choices
    if re.search(r"\b(race|ethnic|ethnicity)\b", label):
        value = profile.get("race")
        choices = [value] if _known(value) else []
        if value == "Prefer not to answer":
            choices.extend(["Decline to self identify", "I do not wish to answer"])
        return True, choices
    if "citizen" in label or "citizenship" in label:
        value = profile.get("us_citizen")
        is_us = bool(re.search(r"\b(us|u s|usa|united states|american)\b", label))
        if value not in {"Yes", "No"}:
            return True, []
        # A country selection can use explicit US citizenship. Non-US does not
        # identify another citizenship, and work authorization is not inferred.
        if not is_us:
            if label in {"citizenship", "country of citizenship"} and value == "Yes":
                return True, ["United States", "United States of America", "US", "USA"]
            return True, []
        if field["type"] == "checkbox":
            return True, [value == "Yes"]
        return True, [value, "United States", "United States of America", "US", "USA"] if value == "Yes" else ["No"]
    if "veteran" in label:
        value = profile.get("not_protected_veteran")
        if value == "Prefer not to answer":
            return True, ["Prefer not to answer", "I do not wish to answer", "Decline to self identify"]
        if value not in {"Yes", "No"}:
            return True, []
        negative_question = "not a protected veteran" in label or "not protected veteran" in label
        protected_question = "protected veteran" in label
        if not protected_question and label not in {"veteran status", "veteran"}:
            return True, []  # Not protected does not imply not a veteran.
        answer = value if negative_question else ("No" if value == "Yes" else "Yes")
        if field["type"] == "checkbox":
            return True, [answer == "Yes"] if protected_question else []
        # Generic veteran status may offer a precise protected-status option,
        # but a generic Yes/No cannot be inferred from protected status.
        choices = [answer] if protected_question else []
        if value == "Yes":
            return True, [*choices, "I am not a protected veteran", "Not a protected veteran"]
        return True, [*choices, "I am a protected veteran", "Protected veteran"]
    if label in {"name", "full name", "legal name", "your name", "your full name"}:
        return True, [profile["full_name"]]
    if "linkedin" in label:
        value = profile.get("linkedin_info")
        return True, [value] if _known(value) else []
    return False, []


def _fit_answer(field: dict, value: object) -> object:
    kind = field["type"]
    if kind == "checkbox":
        if type(value) is not bool:
            raise _NeedsAttention("A checkbox answer could not be verified.")
        return value
    if kind in {"select", "select-one", "radio", "select-multiple"}:
        supplied = value if kind == "select-multiple" else [value]
        if not isinstance(supplied, list) or not supplied or any(not isinstance(item, str) for item in supplied):
            raise _NeedsAttention("A selection answer could not be verified.")
        mapped = []
        for item in supplied:
            matches = [option["value"] for option in field["options"]
                       if item == option["value"] or _normal(item) == _normal(option["label"])]
            matches = list(dict.fromkeys(matches))
            if len(matches) != 1 or matches[0] == "":
                raise _NeedsAttention("An answer did not identify one available form option.")
            mapped.append(matches[0])
        return list(dict.fromkeys(mapped)) if kind == "select-multiple" else mapped[0]
    if kind not in {"text", "textarea", "email", "tel", "url", "number", "date"}:
        raise _NeedsAttention("An application control requires manual input.")
    if not isinstance(value, str) or not value.strip() or len(value) > 12_000 or "\x00" in value:
        raise _NeedsAttention("A text answer could not be verified.")
    value = value.strip()
    if kind == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise _NeedsAttention("An email answer could not be verified.")
    if kind == "url":
        try:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None:
                raise ValueError()
        except ValueError:
            raise _NeedsAttention("A profile URL needs your attention.") from None
    if kind == "number":
        try:
            if not Decimal(value).is_finite():
                raise InvalidOperation()
        except InvalidOperation:
            raise _NeedsAttention("A numeric answer could not be verified.") from None
    if kind == "date":
        try:
            value = date.fromisoformat(value).isoformat()
        except ValueError:
            raise _NeedsAttention("A date answer could not be verified.") from None
    return value


def _inspection(result: object) -> dict:
    if not isinstance(result, dict):
        raise _NeedsAttention("The browser did not return a usable application form.")
    if result.get("blocked"):
        raise _NeedsAttention("The application requires attention in the browser.")
    if result.get("complete") is True:
        return result
    fields = result.get("fields")
    if (not isinstance(fields, list) or len(fields) > MAX_FORM_FIELDS
            or not isinstance(result.get("form_token"), str) or not result["form_token"]
            or len(result["form_token"]) > 200
            or result.get("next_action") not in {"next", "submit", "none"}):
        raise _NeedsAttention("The browser could not identify a supported application form.")
    ids = set()
    clean = []
    for source in fields:
        if (not isinstance(source, dict) or not isinstance(source.get("id"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source["id"])
                or source["id"] in ids or not isinstance(source.get("label"), str)
                or len(source["label"]) > 2000 or source.get("type") not in _FIELD_TYPES
                or type(source.get("required", False)) is not bool):
            raise _NeedsAttention("The browser returned an unsupported form control.")
        options = source.get("options", [])
        if (not isinstance(options, list) or len(options) > 200
                or any(not isinstance(option, dict) or not isinstance(option.get("value"), str)
                       or not isinstance(option.get("label"), str)
                       or len(option["value"]) > 2000 or len(option["label"]) > 2000 for option in options)):
            raise _NeedsAttention("The browser returned unsupported form options.")
        field = dict(source, required=source.get("required", False), options=options)
        ids.add(field["id"])
        clean.append(field)
    return dict(result, fields=clean)


class _History:
    def __init__(self, path: Path):
        self.path = path
        try:
            with path.open("rb") as file:
                data = file.read(MAX_HISTORY_BYTES + 1)
        except FileNotFoundError:
            self.jobs = {}
            return
        except OSError as error:
            raise AutomationError("Could not read application history. No applications were started.") from error
        try:
            if len(data) > MAX_HISTORY_BYTES:
                raise ValueError()
            body = json.loads(data)
            if (not isinstance(body, dict) or type(body.get("version")) is not int
                    or body["version"] != 1 or not isinstance(body.get("jobs"), dict)):
                raise ValueError()
            for url, entry in body["jobs"].items():
                if (_canonical_link(url) != url or not isinstance(entry, dict)
                        or entry.get("status") not in {"submitted", "uncertain"}):
                    raise ValueError()
            self.jobs = body["jobs"]
        except (ValueError, TypeError, RecursionError) as error:
            raise AutomationError("Application history is invalid. Check it before starting another run.") from error

    def record(self, url: str, status: str, run_id: str) -> None:
        updated = dict(self.jobs)
        updated[url] = {"status": status, "run_id": run_id,
                        "updated_at": datetime.now(timezone.utc).isoformat()}
        self._write(updated)

    def clear_verified_transition(self, url: str) -> None:
        updated = dict(self.jobs)
        updated.pop(url, None)
        self._write(updated)

    def _write(self, updated: dict) -> None:
        data = json.dumps({"version": 1, "jobs": updated}, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_HISTORY_BYTES:
            raise AutomationError("Application history is full. Check it before submitting more applications.")
        temporary = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", prefix="application-history-",
                                             suffix=".tmp", dir=self.path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise AutomationError("Could not save application history. Check the browser before retrying.") from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        self.jobs = updated


class AutomationRunner:
    def __init__(self, bridge, history_path: Path | None = None):
        self.bridge = bridge
        if history_path is None:
            local = os.environ.get("LOCALAPPDATA")
            directory = (Path(local) if local else Path.home() / ".local" / "share") / "KulaAPP"
            history_path = directory / "application-history.json"
        self.history_path = Path(history_path)
        self._run_lock = threading.Lock()

    def run(self, links: list[str], resume_paths: list[Path], profile: dict,
            progress: Callable[[int, str, str], None], cancel_event: threading.Event) -> None:
        if not self._run_lock.acquire(blocking=False):
            raise AutomationError("An application run is already active.")
        run_id = str(uuid.uuid4())
        begun = False
        try:
            urls, paths, supplied_profile = self._preflight(links, resume_paths, profile)
            history = _History(self.history_path)
            for index in range(len(urls)):
                progress(index, "pending", "Waiting for this job.")
            if cancel_event.is_set():
                for index in range(len(urls)):
                    progress(index, "stopped", "Stopped before opening an application.")
                return
            self.bridge.begin_run(run_id)
            begun = True
            seen = set()
            for index, (url, path) in enumerate(zip(urls, paths)):
                if cancel_event.is_set():
                    for remaining in range(index, len(urls)):
                        progress(remaining, "stopped", "Stopped before the next browser action.")
                    break
                if url in seen or url in history.jobs:
                    reason = "Duplicate job in this run." if url in seen else "Previously submitted or uncertain; check application history."
                    progress(index, "skipped", reason)
                    continue
                seen.add(url)
                try:
                    status, message = self._run_job(url, path, supplied_profile, run_id, history,
                                                    lambda status, message: progress(index, status, message), cancel_event)
                    progress(index, status, message)
                except _Stopped:
                    if url in history.jobs and history.jobs[url]["status"] == "uncertain":
                        progress(index, "uncertain", "Stopped after submission began. Check the browser before any retry.")
                    else:
                        progress(index, "stopped", "Stopped before the next browser action.")
                    for remaining in range(index + 1, len(urls)):
                        progress(remaining, "stopped", "Stopped before opening an application.")
                    break
                except _NeedsAttention as error:
                    if url in history.jobs and history.jobs[url]["status"] == "uncertain":
                        progress(index, "uncertain", "Submission was not confirmed. Check the browser before any retry.")
                    else:
                        progress(index, "needs_attention", str(error))
                except Exception as error:
                    if cancel_event.is_set():
                        progress(index, "stopped", "Stopped. Check the browser for any action already in progress.")
                        for remaining in range(index + 1, len(urls)):
                            progress(remaining, "stopped", "Stopped before opening an application.")
                        break
                    if url in history.jobs and history.jobs[url]["status"] == "uncertain":
                        progress(index, "uncertain", "Submission was not confirmed. Check the browser; this job will not be retried automatically.")
                    elif isinstance(error, CommandError):
                        message = str(error).replace("\n", " ")[:500]
                        progress(index, "needs_attention", message or "The browser requires your attention before continuing.")
                    else:
                        progress(index, "failed", "The browser step failed. No automatic retry was made.")
        finally:
            if begun:
                try:
                    self.bridge.cancel_run(run_id)
                except Exception:
                    pass
            self._run_lock.release()

    @staticmethod
    def _preflight(links, resume_paths, profile):
        if (not isinstance(links, list) or not links or len(links) > 10_000
                or not isinstance(resume_paths, list) or len(links) != len(resume_paths)):
            raise AutomationError("Load jobs and import exactly one resume for each job before starting.")
        if (not isinstance(profile, dict) or not isinstance(profile.get("full_name"), str)
                or not profile["full_name"].strip()):
            raise AutomationError("Save your full name in the Package profile before starting.")
        try:
            urls = [_canonical_link(link) for link in links]
            paths = [Path(path) for path in resume_paths]
            for path in paths:
                if path.suffix.casefold() != ".pdf" or not path.is_file() or path.stat().st_size > MAX_RESUME_BYTES:
                    raise ValueError()
                with path.open("rb") as file:
                    if file.read(5) != b"%PDF-":
                        raise ValueError()
        except (OSError, TypeError, ValueError) as error:
            raise AutomationError("Check that every job link is valid and every matching PDF is readable and at most 8 MB.") from error
        return urls, paths, dict(profile)

    def _execute(self, role, action, payload, run_id, cancel_event, timeout=120):
        if cancel_event.is_set():
            self.bridge.cancel_run(run_id)
            raise _Stopped()
        result = self.bridge.execute(role, action, payload, run_id=run_id,
                                     timeout=timeout, cancel_event=cancel_event)
        if cancel_event.is_set():
            self.bridge.cancel_run(run_id)
            raise _Stopped()
        if not isinstance(result, dict):
            raise _NeedsAttention("The browser returned an invalid response.")
        if result.get("blocked"):
            raise _NeedsAttention("The application requires attention in the browser.")
        return result

    @staticmethod
    def _resume(path: Path) -> dict:
        with path.open("rb") as file:
            data = file.read(MAX_RESUME_BYTES + 1)
        if len(data) > MAX_RESUME_BYTES or not data.startswith(b"%PDF-"):
            raise _NeedsAttention("The selected resume changed or cannot be uploaded.")
        return {"name": path.name, "mime": "application/pdf", "base64": base64.b64encode(data).decode("ascii"),
                "sha256": hashlib.sha256(data).hexdigest()}

    def _answers(self, inspection, profile, resume, url, run_id, cancel_event):
        answers = []
        requested = []
        for field in inspection["fields"]:
            if field["type"] == "file":
                if field.get("purpose") == "resume" or re.search(r"\b(resume|cv|curriculum vitae)\b", field["label"], re.I):
                    continue
                if field["required"] and not _filled(field):
                    raise _NeedsAttention("The application requires another document.")
                continue
            explicit, candidates = _profile_policy(field, profile)
            if explicit:
                value = None
                for candidate in candidates:
                    try:
                        value = _fit_answer(field, candidate)
                        break
                    except _NeedsAttention:
                        continue
                if value is not None:
                    if field["required"] and field["type"] == "checkbox" and value is False:
                        raise _NeedsAttention("A required checkbox does not match your explicit profile answer.")
                    if field.get("readonly") is True:
                        current = field.get("current_value", field.get("value"))
                        if current != value:
                            raise _NeedsAttention("A read-only personal field does not match your saved profile.")
                        continue
                    answers.append({"field_id": field["id"], "value": value})
                elif field["required"] or _filled(field):
                    raise _NeedsAttention("A required personal, legal, consent, or preference answer needs your attention.")
                continue
            if _filled(field):
                continue
            if field.get("readonly") is True:
                if field["required"]:
                    raise _NeedsAttention("A required read-only field is empty.")
                continue
            requested.append(field)

        if not requested:
            return answers
        request_id = str(uuid.uuid4())
        result = self._execute("chatgpt", "answer_questions", {
            "request_id": request_id, "job_url": url,
            "job_context": str(inspection.get("context", ""))[:20_000],
            "profile": profile, "resume": resume, "fields": requested,
        }, run_id, cancel_event, timeout=240)
        if (result.get("request_id") != request_id or not isinstance(result.get("answers"), list)
                or not isinstance(result.get("unknown_fields", []), list)):
            raise _NeedsAttention("ChatGPT did not return a verified response for this application.")
        fields = {field["id"]: field for field in requested}
        unknown = result.get("unknown_fields", [])
        if any(not isinstance(value, str) or value not in fields for value in unknown):
            raise _NeedsAttention("ChatGPT returned unknown form controls.")
        returned = set()
        values = {}
        for answer in result["answers"]:
            if (not isinstance(answer, dict) or not isinstance(answer.get("field_id"), str)
                    or answer["field_id"] not in fields
                    or answer["field_id"] in returned):
                raise _NeedsAttention("ChatGPT returned an unexpected form answer.")
            field_id = answer["field_id"]
            returned.add(field_id)
            value = answer.get("value")
            if value is None:
                continue
            source = answer.get("source")
            if not isinstance(source, str) or source not in {"resume", *(f"profile.{key}" for key in profile)}:
                raise _NeedsAttention("ChatGPT did not identify a supplied source for an answer.")
            if field_id in unknown:
                raise _NeedsAttention("ChatGPT returned conflicting answers.")
            values[field_id] = _fit_answer(fields[field_id], value)
        for field_id, field in fields.items():
            if field_id not in values:
                if field["required"]:
                    raise _NeedsAttention("A required application answer is not present in your supplied information.")
                continue
            if field["required"] and field["type"] == "checkbox" and values[field_id] is False:
                raise _NeedsAttention("A required application checkbox needs your attention.")
            answers.append({"field_id": field_id, "value": values[field_id]})
        return answers

    def _run_job(self, url, path, profile, run_id, history, emit, cancel_event):
        resume = self._resume(path)
        emit("opening", "Opening this job's application.")
        opened = self._execute("jobright", "open_application", {"url": url}, run_id, cancel_event)
        if opened.get("already_applied") is True:
            return "skipped", "The browser reports this application is already completed."
        tab_id = opened.get("tab_id")
        if type(tab_id) is not int or tab_id < 0:
            raise _NeedsAttention("The application tab could not be identified.")
        seen_forms = set()
        resume_verified = False
        pending_transition = False
        previous_signature = None
        for _page in range(MAX_FORM_PAGES):
            inspection = _inspection(self._execute("jobright", "inspect_form", {"tab_id": tab_id}, run_id, cancel_event))
            if inspection.get("complete") is True:
                if pending_transition:
                    return "uncertain", "The previous control may have submitted. Check the browser; this job will not be retried automatically."
                return "skipped", "The browser reports this application is already completed."
            signature = json.dumps([inspection.get("url"), inspection["next_action"],
                                    [(field["id"], field["label"], field["type"], field.get("current_value"))
                                     for field in inspection["fields"]]], sort_keys=True)
            if pending_transition:
                editable = any(field.get("readonly") is not True and field["type"] != "password"
                               for field in inspection["fields"])
                if signature == previous_signature or signature in seen_forms or not editable:
                    raise _NeedsAttention("The previous browser action did not confirm a new editable form.")
                history.clear_verified_transition(url)
                pending_transition = False
            if signature in seen_forms:
                raise _NeedsAttention("The application did not advance to a new form page.")
            seen_forms.add(signature)
            emit("answering", "Preparing answers from your supplied profile and resume.")
            answers = self._answers(inspection, profile, resume, url, run_id, cancel_event)
            emit("filling", "Filling the application and attaching the matching resume.")
            filled = self._execute("jobright", "fill_form", {
                "tab_id": tab_id, "form_token": inspection["form_token"], "answers": answers, "resume": resume,
            }, run_id, cancel_event)
            if filled.get("ok") is not True or filled.get("validation_errors"):
                raise _NeedsAttention("The application has missing or invalid fields.")
            if filled.get("resume_uploaded") is True:
                if filled.get("resume_sha256") != resume["sha256"]:
                    raise _NeedsAttention("The browser did not confirm the matching resume file.")
                resume_verified = True
            if not resume_verified:
                raise _NeedsAttention("The matching resume was not uploaded. Check the application in the browser.")
            mode = inspection["next_action"]
            if mode == "none":
                raise _NeedsAttention("No supported next or submit action was found.")
            if mode == "submit" and inspection.get("submit_ready") is not True:
                raise _NeedsAttention("The final submission action could not be verified.")
            if cancel_event.is_set():
                raise _Stopped()
            # Even a control labelled Continue can submit a form. Journal every
            # advance before dispatch; only a new editable form clears intent.
            history.record(url, "uncertain", run_id)
            emit("submitting" if mode == "submit" else "filling",
                 "Submitting this application once." if mode == "submit" else "Advancing the application once.")
            result = self._execute("jobright", "advance", {
                "tab_id": tab_id, "form_token": inspection["form_token"], "mode": mode,
            }, run_id, cancel_event)
            if result.get("submitted") is True:
                history.record(url, "submitted", run_id)
                return "submitted", "Application submission confirmed in the browser."
            if mode == "submit":
                return "uncertain", "Submission was not confirmed. Check the browser; this job will not be retried automatically."
            pending_transition = True
            previous_signature = signature
        raise _NeedsAttention("The application exceeded the supported form-page limit.")
