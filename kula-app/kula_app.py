"""Kula APP's Windows desktop UI, bundled with Python by PyInstaller."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.client import HTTPConnection
import json
from pathlib import Path
import queue
import secrets
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from bridge import BridgeServer
from pairing import load_or_create_key
from profile_tab import ProfileTab
from profile_store import ProfileStore
from resumes import ResumeImportError, assign_resumes


@dataclass
class _Replacement:
    links: list[str]
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


class KulaApp:
    """Marshal complete receiver batches onto Tk's main thread before ack."""

    def __init__(self, root: tk.Tk, *, pairing_key: str | None = None, port: int = 43127,
                 profile_store: ProfileStore | None = None) -> None:
        self.root = root
        self.links: list[str] = []
        self.resume_paths: list[Path] = []
        self.resume_folder: Path | None = None
        self._link_revision = 0
        self._importing = False
        self.bridge: BridgeServer | None = None
        self.startup_error: Exception | None = None
        self._pairing_key = pairing_key
        self._port = port
        self._closing = threading.Event()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _Replacement] = {}
        self._requests: queue.Queue[_Replacement] = queue.Queue()
        self._shutdown_done = threading.Event()

        root.title("Kula APP")
        root.geometry("900x650")
        root.minsize(720, 540)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#fbfdfc")
        style.configure("TLabel", background="#fbfdfc", foreground="#15392e", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 10))
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)
        self.jobs_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.jobs_tab, text="Jobs")
        self.package_tab = ProfileTab(self.tabs, store=profile_store)
        self.tabs.add(self.package_tab, text="Package")
        frame = ttk.Frame(self.jobs_tab, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="Kula APP", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="Receive filtered job links from your browser extension.").grid(row=1, column=0, sticky="w", pady=(0, 15))
        self.connection_status = tk.StringVar(value="Starting local connection…")
        ttk.Label(frame, textvariable=self.connection_status).grid(row=2, column=0, sticky="w", pady=(0, 12))
        self.copy_button = ttk.Button(frame, text="Copy pairing key", command=self._copy_pairing_key, state="disabled")
        self.copy_button.grid(row=3, column=0, sticky="w", pady=(0, 10))
        ttk.Label(
            frame,
            text="First time: copy the pairing key into the Jobright extension’s Kula APP settings.\n"
                 "Then open your filtered Jobright list and click “Send to Kula APP”. Keep this app open.",
            wraplength=850,
        ).grid(row=4, column=0, sticky="w", pady=(0, 20))
        self.job_count = tk.StringVar(value="0 job links")
        toolbar = ttk.Frame(frame)
        toolbar.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.job_count).grid(row=0, column=0, sticky="w")
        self.import_button = ttk.Button(toolbar, text="Import Resumes", command=self.import_resumes, state="disabled")
        self.import_button.grid(row=0, column=1, sticky="e")
        self.resume_status = tk.StringVar(value="Receive job links first, then import the matching resume PDFs.")
        ttk.Label(frame, textvariable=self.resume_status, wraplength=850).grid(row=6, column=0, sticky="w", pady=(0, 8))
        list_frame = ttk.Frame(frame)
        list_frame.grid(row=7, column=0, sticky="nsew", pady=(0, 12))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=27,
                        background="#ffffff", foreground="#15392e", fieldbackground="#ffffff")
        self.job_list = ttk.Treeview(list_frame, columns=("number", "job", "resume"), show="headings", selectmode="browse")
        self.job_table = self.job_list
        self.job_list.heading("number", text="#")
        self.job_list.heading("job", text="Job link")
        self.job_list.heading("resume", text="Resume PDF")
        self.job_list.column("number", width=45, minwidth=40, stretch=False, anchor="center")
        self.job_list.column("job", width=480, minwidth=250)
        self.job_list.column("resume", width=280, minwidth=150)
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.job_list.yview)
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.job_list.xview)
        self.job_list.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.job_list.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.transfer_status = tk.StringVar(value="Waiting for job links. The next received batch will replace this list.")
        ttk.Label(frame, textvariable=self.transfer_status, wraplength=850).grid(row=8, column=0, sticky="w")
        root.after(25, self._process_replacements)
        self._start_bridge()

    def _start_bridge(self) -> None:
        try:
            if self._pairing_key is None:
                self._pairing_key = load_or_create_key()
            self.bridge = BridgeServer(self._pairing_key, self.replace_links, port=self._port)
            self.bridge.start()
            self.connection_status.set(f"Connected locally: 127.0.0.1:{self.bridge.port}")
            self.copy_button.configure(state="normal")
        except ValueError as error:
            self.startup_error = error
            self.connection_status.set("The saved pairing key is invalid. Kula APP could not start.")
            self.transfer_status.set("Close this app and remove the invalid pairing-key.txt in %LOCALAPPDATA%\\KulaAPP, then pair again.")
        except Exception as error:
            self.startup_error = error
            self.connection_status.set("Could not start the local connection on 127.0.0.1:43127.")
            self.transfer_status.set("Close another copy of Kula APP if it is running. Also check access to %LOCALAPPDATA%\\KulaAPP, then reopen this app.")

    def replace_links(self, links: list[str]) -> None:
        """Receiver-thread callback; return only after the list has changed."""
        replacement = _Replacement(list(links))
        with self._pending_lock:
            if self._closing.is_set():
                raise RuntimeError("Kula APP is closing.")
            self._pending[id(replacement)] = replacement
            self._requests.put(replacement)
        replacement.done.wait()
        if replacement.error is not None:
            raise replacement.error

    def _process_replacements(self) -> None:
        if self._closing.is_set():
            return
        while True:
            try:
                replacement = self._requests.get_nowait()
            except queue.Empty:
                break
            try:
                self._apply_links(replacement.links)
            except Exception as error:
                replacement.error = error
            finally:
                with self._pending_lock:
                    self._pending.pop(id(replacement), None)
                replacement.done.set()
        self.root.after(25, self._process_replacements)

    def _apply_links(self, links: list[str]) -> None:
        previous = self.links
        previous_resumes = self.resume_paths
        previous_folder = self.resume_folder
        previous_resume_status = self.resume_status.get()
        previous_count = self.job_count.get()
        previous_status = self.transfer_status.get()
        try:
            self._render_rows(links, [])
            self.job_count.set(f"{len(links):,} job link{'' if len(links) == 1 else 's'}")
            self.transfer_status.set("Received from your extension. The previous list was replaced.")
            self.resume_status.set("No resumes imported. PDFs match jobs by filename order: name.pdf, name (1).pdf, name (2).pdf. Review the pairings below.")
            self.links = list(links)
            self.resume_paths = []
            self.resume_folder = None
            self.job_list.yview_moveto(0)
            self._update_import_button()
        except Exception:
            self._render_rows(previous, previous_resumes)
            self.job_count.set(previous_count)
            self.transfer_status.set(previous_status)
            self.resume_status.set(previous_resume_status)
            self.links = previous
            self.resume_paths = previous_resumes
            self.resume_folder = previous_folder
            self._update_import_button()
            raise
        self._link_revision += 1

    def _render_rows(self, links: list[str], resume_paths: list[Path]) -> None:
        self.job_list.delete(*self.job_list.get_children())
        for index, link in enumerate(links):
            filename = resume_paths[index].name if index < len(resume_paths) else "Not imported"
            self.job_list.insert("", "end", iid=str(index), values=(index + 1, link, filename))

    def _update_import_button(self) -> None:
        enabled = bool(self.links) and not self._importing and not self._closing.is_set()
        self.import_button.configure(state="normal" if enabled else "disabled")

    def import_resumes(self) -> None:
        """Choose a resume folder without changing existing bindings on cancel."""
        if self._closing.is_set() or self._importing:
            return
        if not self.links:
            self.resume_status.set("Receive job links before importing resumes.")
            return
        expected_links = list(self.links)
        expected_revision = self._link_revision
        self._importing = True
        self._update_import_button()
        try:
            folder = filedialog.askdirectory(parent=self.root, title="Select the folder containing your resume PDFs", mustexist=True)
            if not folder or self._closing.is_set():
                return
            self._apply_resume_folder(folder, expected_links, expected_revision=expected_revision)
        except tk.TclError:
            if not self._closing.is_set():
                self.resume_status.set("Could not open the folder picker. Please try importing resumes again.")
        finally:
            self._importing = False
            if not self._closing.is_set():
                self._update_import_button()

    def _apply_resume_folder(self, folder: str | Path, expected_links: list[str] | None = None,
                             *, expected_revision: int | None = None) -> bool:
        """Validate a complete folder before committing any job/resume pairing."""
        if self._closing.is_set():
            return False
        links = list(self.links if expected_links is None else expected_links)
        if links != self.links or (expected_revision is not None and expected_revision != self._link_revision):
            self.resume_status.set("The job list changed while choosing a folder. Import resumes again for the current batch.")
            return False
        try:
            if not links:
                raise ResumeImportError("Receive job links before importing resumes.")
            replacement = assign_resumes(folder, links)
            selected_folder = Path(folder).resolve()
        except (ResumeImportError, OSError) as error:
            self.resume_status.set(f"Resumes were not imported: {error}")
            return False
        if links != self.links or (expected_revision is not None and expected_revision != self._link_revision):
            self.resume_status.set("The job list changed while checking the folder. Import resumes again for the current batch.")
            return False

        previous_paths = self.resume_paths
        previous_folder = self.resume_folder
        previous_status = self.resume_status.get()
        try:
            self._render_rows(self.links, replacement)
            self.resume_status.set(f"Imported {len(replacement):,} resume PDF{'s' if len(replacement) != 1 else ''} in filename order. Check each job/resume pairing below.")
            self.resume_paths = list(replacement)
            self.resume_folder = selected_folder
        except Exception:
            self._render_rows(self.links, previous_paths)
            self.resume_status.set(previous_status)
            self.resume_paths = previous_paths
            self.resume_folder = previous_folder
            raise
        return True

    def _copy_pairing_key(self) -> None:
        if self._pairing_key is None or self._closing.is_set():
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._pairing_key)
            self.transfer_status.set("Pairing key copied. Paste it into the extension’s Kula APP settings.")
        except tk.TclError:
            self.transfer_status.set("The clipboard is busy. Click “Copy pairing key” again.")

    def close(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        self.copy_button.configure(state="disabled")
        self.import_button.configure(state="disabled")
        self.connection_status.set("Stopping local connection…")
        with self._pending_lock:
            for replacement in self._pending.values():
                replacement.error = RuntimeError("Kula APP is closing.")
                replacement.done.set()
            self._pending.clear()

        def stop_receiver() -> None:
            try:
                if self.bridge is not None:
                    self.bridge.close()
            finally:
                self._shutdown_done.set()

        threading.Thread(target=stop_receiver, name="kula-shutdown", daemon=True).start()
        deadline = time.monotonic() + 5

        def finish_close() -> None:
            if self._shutdown_done.is_set() or time.monotonic() >= deadline:
                self.root.destroy()
            else:
                self.root.after(25, finish_close)

        self.root.after(25, finish_close)


def smoke_test() -> int:
    """Exercise the bundled GUI, local receiver, and resume import in isolation."""
    temporary = tempfile.TemporaryDirectory(prefix="kula-smoke-")
    smoke_folder = Path(temporary.name)
    root = tk.Tk()
    root.withdraw()
    key = secrets.token_hex(32)
    app = KulaApp(root, pairing_key=key, port=0, profile_store=ProfileStore(smoke_folder / "profile"))
    finished = threading.Event()
    failures: list[Exception] = []
    expected = ["https://jobright.ai/jobs/info/smoke-final-one", "https://jobright.ai/jobs/info/smoke-final-two"]

    def send_batches() -> None:
        try:
            if app.startup_error is not None or app.bridge is None:
                raise RuntimeError("The smoke-test receiver did not start.")
            for links in [["https://jobright.ai/jobs/info/smoke-one", "https://jobright.ai/jobs/info/smoke-two"], expected]:
                connection = HTTPConnection("127.0.0.1", app.bridge.port, timeout=5)
                try:
                    connection.request("POST", "/v1/job-links", json.dumps({"protocolVersion": 1, "links": links}),
                                       {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    if response.status != 200 or payload != {"ok": True, "count": len(links)}:
                        raise RuntimeError("The smoke-test replacement was not acknowledged.")
                finally:
                    connection.close()
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    threading.Thread(target=send_batches, name="kula-smoke-client", daemon=True).start()
    deadline = time.monotonic() + 15

    def check_result() -> None:
        if finished.is_set():
            try:
                rows = [app.job_list.item(item, "values") for item in app.job_list.get_children()]
                if app.links != expected or [row[1] for row in rows] != expected:
                    raise RuntimeError("The smoke-test list was not replaced.")
                resumes = smoke_folder / "resumes"
                resumes.mkdir()
                for filename in ("name.pdf", "name (1).pdf"):
                    (resumes / filename).write_bytes(b"%PDF-1.7\n%%EOF\n")
                if not app._apply_resume_folder(resumes):
                    raise RuntimeError("The smoke-test resumes could not be imported.")
                if [path.name for path in app.resume_paths] != ["name.pdf", "name (1).pdf"]:
                    raise RuntimeError("The smoke-test resumes were not matched in filename order.")
                rows = [app.job_list.item(item, "values") for item in app.job_list.get_children()]
                if [row[2] for row in rows] != ["name.pdf", "name (1).pdf"]:
                    raise RuntimeError("The resume table did not display the imported filenames.")
                invalid_folder = smoke_folder / "mismatched-resumes"
                invalid_folder.mkdir()
                (invalid_folder / "name.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
                previous_paths = list(app.resume_paths)
                if app._apply_resume_folder(invalid_folder) or app.resume_paths != previous_paths:
                    raise RuntimeError("A mismatched resume count changed the previous import.")
                app._apply_links(expected)
                if app.resume_paths or app.resume_folder is not None:
                    raise RuntimeError("A new job batch did not clear previous resume pairings.")
                if [app.tabs.tab(tab, "text") for tab in app.tabs.tabs()] != ["Jobs", "Package"]:
                    raise RuntimeError("The Jobs and Package tabs were not available.")
                app.package_tab.fields["full_name"].set("Kula Smoke Test")
                app.package_tab.save_profile()
                saved_profile = app.package_tab.store.load()
                if saved_profile["full_name"] != "Kula Smoke Test" or saved_profile["us_citizen"] != "Not specified":
                    raise RuntimeError("The Package profile did not save with explicit answers preserved.")
            except Exception as error:
                failures.append(error)
            finally:
                app.close()
        elif time.monotonic() >= deadline:
            failures.append(RuntimeError("The smoke test timed out."))
            app.close()
        else:
            root.after(25, check_result)

    root.after(25, check_result)
    try:
        root.mainloop()
    finally:
        temporary.cleanup()
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Kula APP desktop receiver")
    parser.add_argument("--smoke-test", action="store_true", help="test the bundled GUI and local transfer without saving a pairing key")
    arguments = parser.parse_args()
    if arguments.smoke_test:
        return smoke_test()
    root = tk.Tk()
    KulaApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
