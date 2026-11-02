"""Kula APP's Windows desktop UI, bundled with Python by PyInstaller."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.client import HTTPConnection
import json
import queue
import secrets
import threading
import time
import tkinter as tk
from tkinter import ttk

from bridge import BridgeServer
from pairing import load_or_create_key


@dataclass
class _Replacement:
    links: list[str]
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


class KulaApp:
    """Marshal complete receiver batches onto Tk's main thread before ack."""

    def __init__(self, root: tk.Tk, *, pairing_key: str | None = None, port: int = 43127) -> None:
        self.root = root
        self.links: list[str] = []
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
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)
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
        ttk.Label(frame, textvariable=self.job_count).grid(row=5, column=0, sticky="w", pady=(0, 8))
        list_frame = ttk.Frame(frame)
        list_frame.grid(row=6, column=0, sticky="nsew", pady=(0, 12))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self._list_values = tk.StringVar(value=())
        self.job_list = tk.Listbox(
            list_frame, listvariable=self._list_values, font=("Segoe UI", 10),
            background="#ffffff", foreground="#15392e", selectbackground="#087f55",
            borderwidth=1, relief="solid", activestyle="none", exportselection=False,
        )
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.job_list.yview)
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.job_list.xview)
        self.job_list.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.job_list.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.transfer_status = tk.StringVar(value="Waiting for job links. The next received batch will replace this list.")
        ttk.Label(frame, textvariable=self.transfer_status, wraplength=850).grid(row=7, column=0, sticky="w")
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
        previous_count = self.job_count.get()
        previous_status = self.transfer_status.get()
        try:
            # Setting the Tcl list replaces its contents in one UI operation.
            self._list_values.set(tuple(links))
            self.job_count.set(f"{len(links):,} job link{'' if len(links) == 1 else 's'}")
            self.transfer_status.set("Received from your extension. The previous list was replaced.")
            self.links = list(links)
            self.job_list.yview_moveto(0)
        except Exception:
            self._list_values.set(tuple(previous))
            self.job_count.set(previous_count)
            self.transfer_status.set(previous_status)
            self.links = previous
            raise

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
    """Exercise bundled Tcl/Tk and real receiver-to-UI replacement without persistence."""
    root = tk.Tk()
    root.withdraw()
    key = secrets.token_hex(32)
    app = KulaApp(root, pairing_key=key, port=0)
    finished = threading.Event()
    failures: list[Exception] = []
    expected = ["https://jobright.ai/jobs/info/smoke-final"]

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
            if app.links != expected or tuple(app.job_list.get(0, tk.END)) != tuple(expected):
                failures.append(RuntimeError("The smoke-test list was not replaced."))
            app.close()
        elif time.monotonic() >= deadline:
            failures.append(RuntimeError("The smoke test timed out."))
            app.close()
        else:
            root.after(25, check_result)

    root.after(25, check_result)
    root.mainloop()
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
