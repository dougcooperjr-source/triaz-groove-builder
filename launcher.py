from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

APP_NAME = "TRIAZ Groove Builder"
APP_VERSION = "0.1.9"


def install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def local_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME


APP_DIR = install_dir()
RUNTIME_DIR = local_data_dir() / "runtime"
BASE_PY_DIR = RUNTIME_DIR / "python311"
VENV_DIR = RUNTIME_DIR / ".venv"
REQ_FILE = APP_DIR / "requirements.txt"
REQ_MARKER = RUNTIME_DIR / "requirements.txt"
PY_INSTALLER = APP_DIR / "bootstrap" / "python-3.11.9-amd64.exe"
LOG_FILE = local_data_dir() / "launcher.log"


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(text: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8", errors="replace") as f:
        f.write(text.rstrip() + "\n")


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    _log("RUN: " + " ".join(str(x) for x in args))
    cp = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        creationflags=_creation_flags(),
    )
    _log(cp.stdout or "")
    if check and cp.returncode != 0:
        raise RuntimeError(f"Command failed ({cp.returncode}): {' '.join(args)}\n\n{(cp.stdout or '')[-3500:]}")
    return cp


def runtime_python() -> Path:
    return VENV_DIR / "Scripts" / "python.exe"


def runtime_pythonw() -> Path:
    p = VENV_DIR / "Scripts" / "pythonw.exe"
    return p if p.exists() else runtime_python()


def runtime_current() -> bool:
    py = runtime_python()
    if not py.exists() or not REQ_MARKER.exists() or not REQ_FILE.exists():
        return False
    try:
        return REQ_MARKER.read_bytes() == REQ_FILE.read_bytes()
    except OSError:
        return False


def _find_python311() -> Path | None:
    private = BASE_PY_DIR / "python.exe"
    if private.exists():
        return private
    py_launcher = shutil.which("py")
    if py_launcher:
        cp = _run([py_launcher, "-3.11", "-c", "import sys; print(sys.executable)"], check=False)
        if cp.returncode == 0:
            candidate = Path((cp.stdout or "").strip().splitlines()[-1])
            if candidate.exists():
                return candidate
    return None


def _install_private_python() -> Path:
    if not PY_INSTALLER.exists():
        raise RuntimeError(f"Bundled Python installer is missing: {PY_INSTALLER}")
    BASE_PY_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        str(PY_INSTALLER),
        "/quiet",
        "InstallAllUsers=0",
        f"TargetDir={BASE_PY_DIR}",
        "Include_pip=1",
        "Include_launcher=0",
        "PrependPath=0",
        "Shortcuts=0",
        "Include_test=0",
        "Include_doc=0",
        "Include_debug=0",
        "Include_symbols=0",
        "Include_tcltk=1",
        "Include_dev=1",
        "Include_exe=1",
        "Include_lib=1",
    ]
    _run(args)
    py = BASE_PY_DIR / "python.exe"
    if not py.exists():
        raise RuntimeError("Python 3.11 installation completed but python.exe was not found.")
    return py


def ensure_runtime(status_cb) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if runtime_current():
        return
    base_py = _find_python311()
    if base_py is None:
        status_cb("Installing the private Python runtime…")
        base_py = _install_private_python()
    py = runtime_python()
    if not py.exists():
        status_cb("Creating the shared audio runtime…")
        _run([str(base_py), "-m", "venv", str(VENV_DIR)])
    status_cb("Updating the shared audio runtime…")
    _run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(py), "-m", "pip", "install", "-r", str(REQ_FILE)])
    status_cb("Verifying audio components…")
    _run([
        str(py), "-c",
        "import numpy, scipy, librosa, soundfile, tkinterdnd2, demucs; print('runtime ok')",
    ])
    REQ_MARKER.write_bytes(REQ_FILE.read_bytes())


def launch_app() -> None:
    app = APP_DIR / "app.py"
    if not app.exists():
        raise RuntimeError(f"Application source is missing: {app}")
    subprocess.Popen(
        [str(runtime_pythonw()), str(app)],
        cwd=str(APP_DIR),
        creationflags=_creation_flags(),
    )


class BootstrapWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("520x175")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Preparing the audio engine. This is only needed on the first install or when the runtime changes.",
            wraplength=470,
        ).pack(anchor="w", pady=(5, 14))
        self.status = tk.StringVar(value="Checking the shared runtime…")
        ttk.Label(frame, textvariable=self.status).pack(anchor="w")
        self.bar = ttk.Progressbar(frame, mode="indeterminate")
        self.bar.pack(fill="x", pady=(10, 0))
        self.bar.start(12)

    def set_status(self, text: str) -> None:
        self.root.after(0, self.status.set, text)

    def run(self) -> int:
        result = {"error": None}
        def worker():
            try:
                ensure_runtime(self.set_status)
                self.set_status("Starting TRIAZ Groove Builder…")
                launch_app()
            except Exception as exc:
                result["error"] = exc
                _log(traceback.format_exc())
            finally:
                self.root.after(0, self.root.quit)
        threading.Thread(target=worker, daemon=True).start()
        self.root.mainloop()
        self.bar.stop()
        self.root.destroy()
        if result["error"] is not None:
            msg = str(result["error"])
            tmp = tk.Tk(); tmp.withdraw()
            messagebox.showerror(
                APP_NAME,
                "The audio runtime could not be prepared.\n\n" + msg + f"\n\nLog: {LOG_FILE}",
                parent=tmp,
            )
            tmp.destroy()
            return 1
        return 0


def main() -> int:
    try:
        if runtime_current():
            launch_app()
            return 0
        return BootstrapWindow().run()
    except Exception as exc:
        _log(traceback.format_exc())
        tmp = tk.Tk(); tmp.withdraw()
        messagebox.showerror(APP_NAME, f"Could not start {APP_NAME}.\n\n{exc}\n\nLog: {LOG_FILE}", parent=tmp)
        tmp.destroy()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
