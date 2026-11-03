"""Application automation state-machine tests; no real job is submitted."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation_runner import AutomationError, AutomationRunner
from automation_bridge import CommandError


URL = "https://jobright.ai/jobs/info/111111111111111111111111"
OTHER_URL = "https://jobright.ai/jobs/info/222222222222222222222222"
PROFILE = {"full_name": "Test Applicant", "linkedin_info": "https://www.linkedin.com/in/test-applicant/",
           "us_citizen": "Not specified", "not_protected_veteran": "Not specified",
           "gender": "Not specified", "race": "Not specified"}


def field(field_id="name", label="Full name", kind="text", required=True, **extra):
    return dict(id=field_id, label=label, type=kind, required=required, **extra)


def form(fields=None, *, token="form-one", action="submit", include_resume=True, **extra):
    fields = list(fields if fields is not None else [field()])
    if include_resume and not any(item["type"] == "file" for item in fields):
        fields.append(field("resume", "Resume PDF", "file"))
    return dict(fields=fields, form_token=token,
                next_action=action, submit_ready=True, url="https://example.com/apply", **extra)


class FakeBridge:
    def __init__(self, handlers=None):
        self.handlers = handlers or {}
        self.calls = []
        self.run_id = None
        self.canceled = []
        self.resume_available = False

    def begin_run(self, run_id):
        self.run_id = run_id

    def cancel_run(self, run_id):
        self.canceled.append(run_id)

    def execute(self, role, action, payload, *, run_id, timeout, cancel_event):
        if run_id != self.run_id:
            raise AssertionError("Unexpected run correlation")
        self.calls.append((role, action, copy.deepcopy(payload)))
        value = self.handlers.get((role, action))
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            result = value(payload)
        elif value is not None:
            result = copy.deepcopy(value)
        elif action == "open_application":
            result = {"tab_id": 7}
        elif action == "inspect_form":
            result = form()
        elif action == "answer_questions":
            result = {"request_id": payload["request_id"], "answers": [
                {"field_id": item["id"], "value": "Supplied resume answer", "source": "resume"}
                for item in payload["fields"]], "unknown_fields": []}
        elif action == "fill_form":
            result = {"ok": True}
            if self.resume_available:
                result.update(resume_uploaded=True, resume_sha256=payload["resume"]["sha256"])
        elif action == "advance":
            result = {"submitted": payload["mode"] == "submit"}
        else:
            raise AssertionError("Unexpected browser action")
        if action == "inspect_form" and isinstance(result, dict):
            self.resume_available = any(item.get("type") == "file" and "resume" in item.get("label", "").lower()
                                        for item in result.get("fields", []))
        return result


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.resume = self.root / "resume.pdf"
        self.resume.write_bytes(b"%PDF-1.7\n% public test fixture\n%%EOF\n")
        self.history = self.root / "application-history.json"
        self.events = []
        self.cancel = threading.Event()

    def tearDown(self):
        self.temporary.cleanup()

    def run_jobs(self, bridge, links=None, profile=None, progress=None):
        links = links if links is not None else [URL]
        runner = AutomationRunner(bridge, history_path=self.history)
        runner.run(links, [self.resume] * len(links), profile or PROFILE,
                   progress or (lambda *event: self.events.append(event)), self.cancel)
        return runner

    def terminal(self, row=0):
        return [event[1] for event in self.events if event[0] == row][-1]

    def test_happy_path_records_intent_before_submit_and_confirms_after(self):
        def advance(payload):
            self.assertEqual(payload["mode"], "submit")
            self.assertEqual(json.loads(self.history.read_text())["jobs"][URL]["status"], "uncertain")
            return {"submitted": True}

        bridge = FakeBridge({("jobright", "advance"): advance})
        self.run_jobs(bridge)
        self.assertEqual(self.terminal(), "submitted")
        self.assertEqual(json.loads(self.history.read_text())["jobs"][URL]["status"], "submitted")
        filled = next(payload for _, action, payload in bridge.calls if action == "fill_form")
        self.assertEqual(filled["answers"], [{"field_id": "name", "value": "Test Applicant"}])
        self.assertEqual(filled["resume"]["name"], "resume.pdf")
        self.assertEqual(len(filled["resume"]["sha256"]), 64)
        self.assertEqual(bridge.canceled, [bridge.run_id])

    def test_chatgpt_response_is_correlated_and_only_requested_fields_are_filled(self):
        bridge = FakeBridge({("jobright", "inspect_form"): form([
            field(), field("motivation", "Why this role?", "textarea"),
            field("resume", "Resume PDF", "file"),
        ])})
        self.run_jobs(bridge)
        asked = next(payload for _, action, payload in bridge.calls if action == "answer_questions")
        self.assertEqual([item["id"] for item in asked["fields"]], ["motivation"])
        self.assertEqual(asked["profile"], PROFILE)
        self.assertEqual(self.terminal(), "submitted")

    def test_next_pages_are_filled_before_the_final_submission(self):
        bridge = FakeBridge({("jobright", "inspect_form"): [
            form(token="one", action="next"),
            form([field("name-two", "Full name")], token="two"),
        ]})
        self.run_jobs(bridge)
        self.assertEqual([payload["mode"] for _, action, payload in bridge.calls if action == "advance"], ["next", "submit"])
        self.assertEqual(self.terminal(), "submitted")

    def test_unknown_required_answer_stops_this_job_before_fill_or_submission(self):
        bridge = FakeBridge({
            ("jobright", "inspect_form"): form([field("experience", "Describe a skill", "textarea")]),
            ("chatgpt", "answer_questions"): lambda payload: {
                "request_id": payload["request_id"], "answers": [], "unknown_fields": ["experience"]},
        })
        self.run_jobs(bridge)
        self.assertEqual(self.terminal(), "needs_attention")
        self.assertFalse(any(action in {"fill_form", "advance"} for _, action, _ in bridge.calls))
        self.assertFalse(self.history.exists())

    def test_browser_blocked_reasons_are_preserved_for_open_and_inspect(self):
        reason = "The Jobright apply button was clicked but no application page opened."
        for action in ("open_application", "inspect_form"):
            with self.subTest(action=action):
                self.events.clear()
                bridge = FakeBridge({("jobright", action): {"blocked": reason}})
                self.run_jobs(bridge)
                self.assertEqual(self.terminal(), "needs_attention")
                self.assertEqual(self.events[-1][2], reason)
                self.assertFalse(any(name == "advance" for _, name, _ in bridge.calls))

    def test_browser_blocked_details_are_bounded_single_line_and_handle_bad_types(self):
        for blocked in (True, {"private": "not a status message"}, ["not a status message"],
                        "  \n\t", "Reason\n\x00" + "x" * 3000):
            with self.subTest(kind=type(blocked).__name__):
                self.events.clear()
                bridge = FakeBridge({("jobright", "open_application"): {"blocked": blocked}})
                self.run_jobs(bridge)
                self.assertEqual(self.terminal(), "needs_attention")
                detail = self.events[-1][2]
                self.assertLessEqual(len(detail), 500)
                self.assertNotIn("\n", detail)
                self.assertNotIn("\x00", detail)
                if not isinstance(blocked, str) or not blocked.strip():
                    self.assertEqual(detail, "The application requires attention in the browser.")

    def test_browser_blocker_explains_why_the_job_needs_attention(self):
        bridge = FakeBridge({("jobright", "open_application"): CommandError("Sign in to the application browser first.")})
        self.run_jobs(bridge)
        self.assertEqual(self.terminal(), "needs_attention")
        self.assertIn("Sign in", self.events[-1][2])
        self.assertFalse(any(action == "advance" for _, action, _ in bridge.calls))

    def test_unprovided_protected_and_legal_answers_are_never_inferred(self):
        labels = ["Gender", "Race", "Are you a US citizen?", "Veteran status",
                  "Do you need sponsorship?", "Are you authorized to work in the US?",
                  "Work authorization", "I certify these answers are true", "Do you agree to the terms?"]
        for label in labels:
            with self.subTest(label=label):
                self.events.clear()
                bridge = FakeBridge({("jobright", "inspect_form"): form([field("controlled", label)])})
                self.run_jobs(bridge)
                self.assertEqual(self.terminal(), "needs_attention")
                self.assertFalse(any(role == "chatgpt" or action == "advance" for role, action, _ in bridge.calls))

    def test_explicit_demographic_profile_answers_match_existing_options(self):
        bridge = FakeBridge({("jobright", "inspect_form"): form([
            field("gender", "Gender", "select", options=[{"value": "f", "label": "Female"}, {"value": "m", "label": "Male"}]),
            field("veteran", "Veteran status", "radio", options=[
                {"value": "not-protected", "label": "I am not a protected veteran"},
                {"value": "protected", "label": "I am a protected veteran"}]),
        ])})
        self.run_jobs(bridge, profile=dict(PROFILE, gender="Female", not_protected_veteran="Yes"))
        filled = next(payload for _, action, payload in bridge.calls if action == "fill_form")
        self.assertEqual(filled["answers"], [{"field_id": "gender", "value": "f"},
                                             {"field_id": "veteran", "value": "not-protected"}])
        self.assertEqual(self.terminal(), "submitted")

    def test_unmatched_option_wrong_correlation_and_extra_fields_are_rejected(self):
        question = field("choice", "Preferred editor", "select", options=[{"value": "known", "label": "Known"}])
        callbacks = [
            lambda payload: {"request_id": "another-request", "answers": [], "unknown_fields": []},
            lambda payload: {"request_id": payload["request_id"], "answers": [{"field_id": "other", "value": "Known"}], "unknown_fields": []},
            lambda payload: {"request_id": payload["request_id"], "answers": [{"field_id": "choice", "value": "not offered"}], "unknown_fields": []},
            lambda payload: {"request_id": payload["request_id"], "answers": [{"field_id": "choice", "value": "known", "source": "unknown"}], "unknown_fields": []},
            lambda payload: {"request_id": payload["request_id"], "answers": [{"field_id": "choice", "value": "known"}], "unknown_fields": []},
            lambda payload: {"request_id": payload["request_id"], "answers": [{"field_id": "choice", "value": "known", "source": "profile"}], "unknown_fields": []},
        ]
        for answer in callbacks:
            with self.subTest(answer=callbacks.index(answer)):
                self.events.clear()
                bridge = FakeBridge({("jobright", "inspect_form"): form([question]), ("chatgpt", "answer_questions"): answer})
                self.run_jobs(bridge)
                self.assertEqual(self.terminal(), "needs_attention")
                self.assertFalse(any(action == "advance" for _, action, _ in bridge.calls))

    def test_generic_veteran_yes_no_cannot_be_inferred_from_not_protected_status(self):
        bridge = FakeBridge({("jobright", "inspect_form"): form([
            field("veteran", "Veteran status", "radio", options=[
                {"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}])])})
        self.run_jobs(bridge, profile=dict(PROFILE, not_protected_veteran="Yes"))
        self.assertEqual(self.terminal(), "needs_attention")
        self.assertFalse(any(action == "advance" for _, action, _ in bridge.calls))

    def test_matching_readonly_profile_values_are_preserved_and_mismatches_block(self):
        bridge = FakeBridge({("jobright", "inspect_form"): form([
            field(readonly=True, current_value="Test Applicant")])})
        self.run_jobs(bridge)
        filled = next(payload for _, action, payload in bridge.calls if action == "fill_form")
        self.assertEqual(filled["answers"], [])
        self.assertEqual(self.terminal(), "submitted")
        self.history.unlink()
        self.events.clear()
        mismatch = FakeBridge({("jobright", "inspect_form"): form([
            field(readonly=True, current_value="Different Applicant")])})
        self.run_jobs(mismatch)
        self.assertEqual(self.terminal(), "needs_attention")
        self.assertFalse(any(action == "fill_form" for _, action, _ in mismatch.calls))

    def test_matching_resume_proof_is_required_before_next_and_submit(self):
        for action in ("next", "submit"):
            with self.subTest(action=action):
                self.events.clear()
                bridge = FakeBridge({("jobright", "inspect_form"): form(action=action, include_resume=False)})
                self.run_jobs(bridge)
                self.assertEqual(self.terminal(), "needs_attention")
                self.assertFalse(any(command == "advance" for _, command, _ in bridge.calls))
                self.assertFalse(self.history.exists())
        wrong = FakeBridge({("jobright", "fill_form"): {"ok": True, "resume_uploaded": True, "resume_sha256": "wrong"}})
        self.events.clear()
        self.run_jobs(wrong)
        self.assertEqual(self.terminal(), "needs_attention")
        self.assertFalse(any(command == "advance" for _, command, _ in wrong.calls))

    def test_next_timeout_is_journaled_and_never_automatically_retried(self):
        def uncertain_next(payload):
            self.assertEqual(payload["mode"], "next")
            self.assertEqual(json.loads(self.history.read_text())["jobs"][URL]["status"], "uncertain")
            raise TimeoutError("private transition details")

        bridge = FakeBridge({("jobright", "inspect_form"): form(action="next"),
                             ("jobright", "advance"): uncertain_next})
        self.run_jobs(bridge)
        self.assertEqual(self.terminal(), "uncertain")
        self.events.clear()
        later = FakeBridge()
        self.run_jobs(later)
        self.assertEqual(self.terminal(), "skipped")
        self.assertFalse(later.calls)

    def test_next_journal_is_cleared_only_after_a_new_editable_form(self):
        def second_form(_payload):
            self.assertEqual(json.loads(self.history.read_text())["jobs"][URL]["status"], "uncertain")
            return form([field("next-name", "Full name")], token="next", include_resume=False)

        fills = []
        def fill(payload):
            fills.append(payload)
            if len(fills) == 2:
                self.assertNotIn(URL, json.loads(self.history.read_text())["jobs"])
                return {"ok": True}
            return {"ok": True, "resume_uploaded": True, "resume_sha256": payload["resume"]["sha256"]}

        bridge = FakeBridge({("jobright", "inspect_form"): [form(action="next"), second_form],
                             ("jobright", "fill_form"): fill})
        self.run_jobs(bridge)
        self.assertEqual(self.terminal(), "submitted")
        self.assertEqual(len(fills), 2, "Verified earlier upload remains attached across form pages")

    def test_same_form_after_next_preserves_uncertainty(self):
        bridge = FakeBridge({("jobright", "inspect_form"): form(action="next")})
        self.run_jobs(bridge)
        self.assertEqual(self.terminal(), "uncertain")
        self.assertEqual(json.loads(self.history.read_text())["jobs"][URL]["status"], "uncertain")
        self.assertEqual(sum(action == "advance" for _, action, _ in bridge.calls), 1)

    def test_uncertain_submission_is_not_retried_even_in_a_new_runner(self):
        bridge = FakeBridge({("jobright", "advance"): TimeoutError("private browser diagnostics")})
        self.run_jobs(bridge)
        self.assertEqual(self.terminal(), "uncertain")
        again = FakeBridge()
        self.events.clear()
        self.run_jobs(again)
        self.assertEqual(self.terminal(), "skipped")
        self.assertEqual(again.calls, [])

    def test_successful_history_and_duplicate_rows_are_skipped(self):
        bridge = FakeBridge()
        self.run_jobs(bridge, [URL, URL])
        self.assertEqual(self.terminal(0), "submitted")
        self.assertEqual(self.terminal(1), "skipped")
        self.assertEqual(sum(action == "open_application" for _, action, _ in bridge.calls), 1)
        self.events.clear()
        later = FakeBridge()
        self.run_jobs(later)
        self.assertEqual(self.terminal(), "skipped")
        self.assertFalse(later.calls)

    def test_stop_prevents_any_following_browser_action(self):
        bridge = FakeBridge()

        def progress(index, status, message):
            self.events.append((index, status, message))
            if status == "filling":
                self.cancel.set()

        self.run_jobs(bridge, [URL, OTHER_URL], progress=progress)
        self.assertEqual(self.terminal(0), "stopped")
        self.assertEqual(self.terminal(1), "stopped")
        self.assertFalse(any(action in {"fill_form", "advance"} for _, action, _ in bridge.calls))

    def test_preflight_rejects_inputs_before_any_browser_command(self):
        bridge = FakeBridge()
        runner = AutomationRunner(bridge, self.history)
        for links, resumes, profile in (([URL], [], PROFILE), ([URL], [self.resume], dict(PROFILE, full_name=""))):
            with self.subTest(profile=bool(profile["full_name"])), self.assertRaises(AutomationError):
                runner.run(links, resumes, profile, lambda *_: None, self.cancel)
        self.resume.write_bytes(b"not PDF")
        with self.assertRaises(AutomationError):
            self.run_jobs(bridge)
        self.assertEqual(bridge.calls, [])
        self.assertIsNone(bridge.run_id)

    def test_corrupt_history_is_not_replaced_and_starts_no_application(self):
        self.history.write_bytes(b"{not valid JSON")
        bridge = FakeBridge()
        with self.assertRaises(AutomationError):
            self.run_jobs(bridge)
        self.assertEqual(bridge.calls, [])
        self.assertEqual(self.history.read_bytes(), b"{not valid JSON")


if __name__ == "__main__":
    unittest.main()
