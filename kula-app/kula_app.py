"""Kula APP's job, resume, profile, and optional browser-automation UI."""

from __future__ import annotations

import argparse
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from automation_bridge import AutomationBridge
from automation_runner import AutomationError, AutomationRunner
from job_links import JobLinksImportError, load_job_links
from pairing import load_or_create_key
from profile_tab import ProfileTab
from profile_store import ProfileStore, ProfileStoreError
from resumes import ResumeImportError, assign_resumes


class KulaApp:
    """Manage job links, resume assignments, and a saved local profile."""

    def __init__(self, root: tk.Tk, *, profile_store: ProfileStore | None = None,
                 bridge_factory=None, runner_factory=None) -> None:
        self.root = root
        self.links: list[str] = []
        self.job_links_file: Path | None = None
        self.resume_paths: list[Path] = []
        self.resume_folder: Path | None = None
        self._link_revision = 0
        self._importing = False
        self._loading_links = False
        self._closing = False
        self._running = False
        self._job_statuses: list[str] = []
        self._bridge = None
        self._bridge_factory = bridge_factory if bridge_factory is not None else AutomationBridge
        self._runner_factory = runner_factory if runner_factory is not None else AutomationRunner
        self._run_cancel = threading.Event()
        self._run_thread: threading.Thread | None = None
        self._run_generation = 0
        self._automation_events: queue.Queue[tuple] = queue.Queue()
        self._automation_after = None

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
        self.automation_tab = ttk.Frame(self.tabs, padding=22)
        self.tabs.add(self.automation_tab, text="Automation")
        self._build_automation_tab()
        frame = ttk.Frame(self.jobs_tab, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)
        ttk.Label(frame, text="Kula APP", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="Load your downloaded job links, then import the matching resumes.").grid(
            row=1, column=0, sticky="w", pady=(0, 15))
        self.export_button = ttk.Button(frame, text="Export job links", command=self.export_job_links)
        self.export_button.grid(row=2, column=0, sticky="w", pady=(0, 10))
        ttk.Label(
            frame,
            text="Select a TXT file containing one Jobright job URL per line. Loading a file replaces the current job list.",
            wraplength=850,
        ).grid(row=3, column=0, sticky="w", pady=(0, 20))
        self.job_count = tk.StringVar(value="0 job links")
        toolbar = ttk.Frame(frame)
        toolbar.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.job_count).grid(row=0, column=0, sticky="w")
        self.import_button = ttk.Button(toolbar, text="Import Resumes", command=self.import_resumes, state="disabled")
        self.import_button.grid(row=0, column=1, sticky="e")
        self.start_button = ttk.Button(toolbar, text="Start", command=self.start_automation, state="disabled")
        self.start_button.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.stop_button = ttk.Button(toolbar, text="Stop", command=self.stop_automation, state="disabled")
        self.stop_button.grid(row=0, column=3, sticky="e", padx=(8, 0))
        self.resume_status = tk.StringVar(value="Load job links first, then import the matching resume PDFs.")
        ttk.Label(frame, textvariable=self.resume_status, wraplength=850).grid(row=5, column=0, sticky="w", pady=(0, 8))
        list_frame = ttk.Frame(frame)
        list_frame.grid(row=6, column=0, sticky="nsew", pady=(0, 12))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=27,
                        background="#ffffff", foreground="#15392e", fieldbackground="#ffffff")
        self.job_list = ttk.Treeview(list_frame, columns=("number", "job", "resume", "status"), show="headings", selectmode="browse")
        self.job_table = self.job_list
        self.job_list.heading("number", text="#")
        self.job_list.heading("job", text="Job link")
        self.job_list.heading("resume", text="Resume PDF")
        self.job_list.heading("status", text="Status")
        self.job_list.column("number", width=45, minwidth=40, stretch=False, anchor="center")
        self.job_list.column("job", width=390, minwidth=220)
        self.job_list.column("resume", width=230, minwidth=140)
        self.job_list.column("status", width=150, minwidth=125)
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.job_list.yview)
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.job_list.xview)
        self.job_list.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.job_list.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.transfer_status = tk.StringVar(value="Choose Export job links to open a TXT file.")
        ttk.Label(frame, textvariable=self.transfer_status, wraplength=850).grid(row=7, column=0, sticky="w")
        self._automation_after = root.after(100, self._poll_automation)

    def _build_automation_tab(self) -> None:
        self.automation_tab.columnconfigure(0, weight=1)
        ttk.Label(self.automation_tab, text="Application automation", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 14))
        ttk.Label(
            self.automation_tab,
            text="1. Install the Jobright runner extension in your application browser.\n"
                 "2. Install the Kula ChatGPT extension in the browser signed in to ChatGPT.\n"
                 "3. Copy the connection key below and save it in both extension popups.\n"
                 "4. Load job links and resumes, save your Package profile, then click Start.",
            wraplength=810,
        ).grid(row=1, column=0, sticky="w", pady=(0, 18))
        self.copy_connection_button = ttk.Button(
            self.automation_tab, text="Copy connection key", command=self.copy_connection_key)
        self.copy_connection_button.grid(row=2, column=0, sticky="w", pady=(0, 12))
        self.connection_status = tk.StringVar(value="Connection not started. Copy the key to connect both extensions.")
        ttk.Label(self.automation_tab, textvariable=self.connection_status, wraplength=810).grid(
            row=3, column=0, sticky="w", pady=(0, 18))
        ttk.Label(
            self.automation_tab,
            text="The matching resume and saved profile are sent to your ChatGPT conversation to draft answers. "
                 "Start automatically submits completed applications. Missing facts, sign-in, CAPTCHA, "
                 "or unsupported forms need attention. Stop prevents the next action; it cannot undo "
                 "an application that has already been submitted.",
            wraplength=810,
        ).grid(row=4, column=0, sticky="w", pady=(0, 18))
        self.automation_status = tk.StringVar(value="Ready when both browsers are connected.")
        ttk.Label(self.automation_tab, textvariable=self.automation_status, wraplength=810).grid(
            row=5, column=0, sticky="w")

    def _ensure_automation_bridge(self):
        if self._bridge is None:
            directory = self.package_tab.store.directory
            key = load_or_create_key(directory)
            bridge = self._bridge_factory(key)
            try:
                bridge.start()
            except Exception:
                try:
                    bridge.close()
                except Exception:
                    pass
                raise
            self._bridge = bridge
        return self._bridge

    def copy_connection_key(self) -> None:
        if self._closing or self._running:
            return
        try:
            self._ensure_automation_bridge()
            key = load_or_create_key(self.package_tab.store.directory)
            self.root.clipboard_clear()
            self.root.clipboard_append(key)
            self.automation_status.set("Connection key copied. Save it in both extension popups.")
        except Exception:
            self.automation_status.set("Could not open the automation connection. Check for another Kula APP instance or access to its data folder.")

    def start_automation(self) -> None:
        if self._closing or self._running or self._importing or self._loading_links:
            return
        try:
            if not self.links:
                raise ValueError("Load job links before clicking Start.")
            if len(self.resume_paths) != len(self.links):
                raise ValueError("Import one matching resume PDF for every job before clicking Start.")
            try:
                for path in self.resume_paths:
                    if path.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError("A resume exceeds the 8 MB automation limit. Choose a smaller PDF and import resumes again.")
                    with path.open("rb") as file:
                        if file.read(5) != b"%PDF-":
                            raise ValueError("A resume is not a readable PDF. Import your resumes again.")
            except OSError as error:
                raise ValueError("A resume could not be opened. Check the files and import resumes again.") from error
            profile = self.package_tab.store.load()
            if not profile["full_name"].strip():
                raise ValueError("Save your Full Name in the Package tab before clicking Start.")
            bridge = self._ensure_automation_bridge()
            connected = bridge.connection_status()
            if not all(connected.get(role) is True for role in ("jobright", "chatgpt")):
                raise ValueError("Connect both browser extensions using the Automation tab, then click Start again.")
        except (ValueError, ProfileStoreError) as error:
            self.automation_status.set(str(error))
            self.transfer_status.set(str(error))
            return
        except OSError:
            self.automation_status.set("Could not open the automation connection. Check for another Kula APP instance or access to its data folder.")
            self.transfer_status.set(self.automation_status.get())
            return
        except Exception:
            self.automation_status.set("Could not start the automation connection. Check the Automation tab and try again.")
            self.transfer_status.set(self.automation_status.get())
            return

        links, resumes, saved_profile = list(self.links), list(self.resume_paths), dict(profile)
        self._run_generation += 1
        generation = self._run_generation
        self._run_cancel = threading.Event()
        cancel = self._run_cancel
        self._running = True
        self._job_statuses = ["pending"] * len(links)
        self._render_rows(links, resumes)
        self._update_import_button()
        self.automation_status.set("Running applications in job-list order.")
        self.transfer_status.set("Running applications. Completed forms will be submitted automatically.")

        def progress(index: int, status: str, message: str) -> None:
            self._automation_events.put((generation, "progress", index, status, message))

        def run() -> None:
            try:
                runner = self._runner_factory(bridge, history_path=self.package_tab.store.directory / "application-history.json")
                runner.run(links, resumes, saved_profile, progress, cancel)
                self._automation_events.put((generation, "done", None))
            except AutomationError as error:
                self._automation_events.put((generation, "done", str(error)))
            except Exception:
                # Keep resume text, profile data, and remote answers out of UI errors.
                self._automation_events.put((generation, "done", "Automation stopped because an unexpected error occurred. Check browser tabs before restarting."))

        self._run_thread = threading.Thread(target=run, name="Kula application runner", daemon=True)
        try:
            self._run_thread.start()
        except Exception:
            self._running = False
            self._update_import_button()
            self.transfer_status.set("Could not start the application runner. Please try again.")

    def stop_automation(self) -> None:
        if not self._running or self._closing:
            return
        self._run_cancel.set()
        self.automation_status.set("Stopping. Actions already submitted cannot be undone.")
        self.transfer_status.set(self.automation_status.get())
        self._update_import_button()

    def _poll_automation(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = self._automation_events.get_nowait()
                if event[0] != self._run_generation:
                    continue
                if event[1] == "progress":
                    _, _, index, status, message = event
                    if type(index) is int and 0 <= index < len(self._job_statuses):
                        self._job_statuses[index] = str(status)
                        self.job_list.set(str(index), "status", str(status).replace("_", " ").title())
                        self.job_list.see(str(index))
                    brief = str(message).replace("\n", " ")[:500]
                    self.automation_status.set(brief)
                    self.transfer_status.set(brief)
                elif event[1] == "done":
                    self._running = False
                    message = event[2] or ("Stopped. Review each job's status before restarting." if self._run_cancel.is_set()
                                           else "Run finished. Review each job's status for results or required attention.")
                    self.automation_status.set(message)
                    self.transfer_status.set(message)
                    self._update_import_button()
        except queue.Empty:
            pass
        if self._bridge is not None:
            try:
                connected = self._bridge.connection_status()
                first = "connected" if connected.get("jobright") else "waiting"
                second = "connected" if connected.get("chatgpt") else "waiting"
                self.connection_status.set(f"Jobright browser: {first}. ChatGPT browser: {second}.")
            except Exception:
                self.connection_status.set("Automation connection unavailable. Close and reopen the app to reconnect.")
        self._automation_after = self.root.after(100, self._poll_automation)

    def _update_import_button(self) -> None:
        busy = self._importing or self._loading_links or self._closing or self._running
        self.export_button.configure(state="disabled" if busy else "normal")
        self.import_button.configure(state="normal" if self.links and not busy else "disabled")
        prepared = bool(self.links) and len(self.resume_paths) == len(self.links)
        self.start_button.configure(state="normal" if prepared and not busy else "disabled")
        self.stop_button.configure(state="normal" if self._running and not self._run_cancel.is_set() and not self._closing else "disabled")
        self.copy_connection_button.configure(state="disabled" if self._running or self._closing else "normal")
        self.package_tab.save_button.configure(state="disabled" if self._running or self._closing else "normal")

    def export_job_links(self) -> None:
        """Open the requested TXT picker and replace the list after validation."""
        if self._closing or self._loading_links or self._importing or self._running:
            return
        self._loading_links = True
        self._update_import_button()
        try:
            path = filedialog.askopenfilename(
                parent=self.root, title="Select job links TXT file", filetypes=[("Text files", "*.txt")])
            if not path or self._closing:
                return
            self._apply_job_links_file(path)
        except tk.TclError:
            if not self._closing:
                self.transfer_status.set("Could not open the file picker. Please try again.")
        finally:
            self._loading_links = False
            if not self._closing:
                self._update_import_button()

    def _apply_job_links_file(self, path: str | Path) -> bool:
        """Reject a bad file before changing jobs or their resume assignments."""
        if self._closing or self._running:
            return False
        try:
            replacement = load_job_links(path)
            selected_file = Path(path).resolve()
        except (JobLinksImportError, OSError) as error:
            self.transfer_status.set(f"Job links were not loaded: {error}")
            return False
        self._apply_links(replacement)
        self.job_links_file = selected_file
        self.transfer_status.set(
            f"Loaded {len(replacement):,} job link{'' if len(replacement) == 1 else 's'} from {selected_file.name}. "
            "The previous list was replaced.")
        return True

    def _apply_links(self, links: list[str]) -> None:
        previous = self.links
        previous_file = self.job_links_file
        previous_resumes = self.resume_paths
        previous_folder = self.resume_folder
        previous_resume_status = self.resume_status.get()
        previous_count = self.job_count.get()
        previous_status = self.transfer_status.get()
        previous_job_statuses = self._job_statuses
        try:
            self._job_statuses = ["pending"] * len(links)
            self._render_rows(links, [])
            self.job_count.set(f"{len(links):,} job link{'' if len(links) == 1 else 's'}")
            self.transfer_status.set("Job links loaded. The previous list was replaced.")
            self.resume_status.set("No resumes imported. PDFs match jobs by filename order: name.pdf, name (1).pdf, name (2).pdf. Review the pairings below.")
            self.links = list(links)
            self.job_links_file = None
            self.resume_paths = []
            self.resume_folder = None
            self.job_list.yview_moveto(0)
            self._update_import_button()
        except Exception:
            self.job_count.set(previous_count)
            self.transfer_status.set(previous_status)
            self.resume_status.set(previous_resume_status)
            self.links = previous
            self.job_links_file = previous_file
            self.resume_paths = previous_resumes
            self.resume_folder = previous_folder
            self._job_statuses = previous_job_statuses
            self._render_rows(previous, previous_resumes)
            self._update_import_button()
            raise
        self._link_revision += 1

    def _render_rows(self, links: list[str], resume_paths: list[Path]) -> None:
        self.job_list.delete(*self.job_list.get_children())
        for index, link in enumerate(links):
            filename = resume_paths[index].name if index < len(resume_paths) else "Not imported"
            status = self._job_statuses[index] if index < len(self._job_statuses) else "pending"
            self.job_list.insert("", "end", iid=str(index), values=(index + 1, link, filename, status.replace("_", " ").title()))

    def import_resumes(self) -> None:
        """Choose a resume folder without changing existing bindings on cancel."""
        if self._closing or self._importing or self._loading_links or self._running:
            return
        if not self.links:
            self.resume_status.set("Load job links before importing resumes.")
            return
        expected_links = list(self.links)
        expected_revision = self._link_revision
        self._importing = True
        self._update_import_button()
        try:
            folder = filedialog.askdirectory(parent=self.root, title="Select the folder containing your resume PDFs", mustexist=True)
            if not folder or self._closing:
                return
            self._apply_resume_folder(folder, expected_links, expected_revision=expected_revision)
        except tk.TclError:
            if not self._closing:
                self.resume_status.set("Could not open the folder picker. Please try importing resumes again.")
        finally:
            self._importing = False
            if not self._closing:
                self._update_import_button()

    def _apply_resume_folder(self, folder: str | Path, expected_links: list[str] | None = None,
                             *, expected_revision: int | None = None) -> bool:
        """Validate a complete folder before committing any job/resume pairing."""
        if self._closing or self._running:
            return False
        links = list(self.links if expected_links is None else expected_links)
        if links != self.links or (expected_revision is not None and expected_revision != self._link_revision):
            self.resume_status.set("The job list changed while choosing a folder. Import resumes again for the current batch.")
            return False
        try:
            if not links:
                raise ResumeImportError("Load job links before importing resumes.")
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
            self._update_import_button()
        except Exception:
            self._render_rows(self.links, previous_paths)
            self.resume_status.set(previous_status)
            self.resume_paths = previous_paths
            self.resume_folder = previous_folder
            raise
        return True

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._run_cancel.set()
        if self._automation_after is not None:
            self.root.after_cancel(self._automation_after)
            self._automation_after = None
        if self._bridge is not None:
            bridge = self._bridge
            self._bridge = None
            threading.Thread(target=bridge.close, name="Kula connection shutdown", daemon=True).start()
        self.root.destroy()


def smoke_test() -> int:
    """Exercise local TXT loading, resume matching, and profile persistence."""
    with tempfile.TemporaryDirectory(prefix="kula-smoke-") as temporary:
        smoke_folder = Path(temporary)
        root = tk.Tk()
        root.withdraw()
        app = KulaApp(root, profile_store=ProfileStore(smoke_folder / "profile"))
        expected = ["https://jobright.ai/jobs/info/smoke-one", "https://jobright.ai/jobs/info/smoke-two"]
        try:
            links_file = smoke_folder / "job-links.txt"
            links_file.write_text("\n".join(expected) + "\n", encoding="utf-8")
            if not app._apply_job_links_file(links_file):
                raise RuntimeError("The smoke-test TXT links could not be loaded.")
            rows = [app.job_list.item(item, "values") for item in app.job_list.get_children()]
            if app.links != expected or [row[1] for row in rows] != expected:
                raise RuntimeError("The smoke-test job list was not replaced.")
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
            previous_paths = list(app.resume_paths)
            invalid_folder = smoke_folder / "mismatched-resumes"
            invalid_folder.mkdir()
            (invalid_folder / "name.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
            if app._apply_resume_folder(invalid_folder) or app.resume_paths != previous_paths:
                raise RuntimeError("A mismatched resume count changed the previous import.")
            invalid_file = smoke_folder / "invalid-links.txt"
            invalid_file.write_text(expected[0] + "\nnot a job URL\n", encoding="utf-8")
            if app._apply_job_links_file(invalid_file) or app.links != expected or app.resume_paths != previous_paths:
                raise RuntimeError("Invalid TXT content changed the current jobs or resumes.")
            if not app._apply_job_links_file(links_file) or app.resume_paths or app.resume_folder is not None:
                raise RuntimeError("Loading a TXT file did not clear previous resume pairings.")
            if [app.tabs.tab(tab, "text") for tab in app.tabs.tabs()] != ["Jobs", "Package", "Automation"]:
                raise RuntimeError("The Jobs, Package, and Automation tabs were not available.")
            app.package_tab.fields["full_name"].set("Kula Smoke Test")
            app.package_tab.save_profile()
            profile = app.package_tab.store.load()
            if profile["full_name"] != "Kula Smoke Test" or profile["us_citizen"] != "Not specified":
                raise RuntimeError("The Package profile did not save with explicit answers preserved.")
        except Exception:
            return 1
        finally:
            app.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Kula APP local job-link and resume manager")
    parser.add_argument("--smoke-test", action="store_true", help="test bundled TXT loading, resume import, and profile saving in isolation")
    arguments = parser.parse_args()
    if arguments.smoke_test:
        return smoke_test()
    root = tk.Tk()
    KulaApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
