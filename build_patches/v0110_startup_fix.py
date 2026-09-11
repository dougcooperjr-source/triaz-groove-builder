from pathlib import Path


def replace_exact(path: str, old: str, new: str) -> None:
    p = Path(path)
    s = p.read_text(encoding='utf-8')
    if old not in s:
        raise SystemExit(f'Expected patch target not found in {path}: {old[:100]!r}')
    p.write_text(s.replace(old, new, 1), encoding='utf-8')

# launcher.py
replace_exact('launcher.py', 'APP_VERSION = "0.1.9"', 'APP_VERSION = "0.1.10"')
replace_exact(
    'launcher.py',
    'LOG_FILE = local_data_dir() / "launcher.log"',
    'LOG_FILE = local_data_dir() / "launcher.log"\nAPP_LOG_FILE = local_data_dir() / "app.log"\nSTARTUP_SENTINEL = local_data_dir() / "startup.ok"',
)
replace_exact(
    'launcher.py',
    '''def runtime_current() -> bool:\n    py = runtime_python()\n    if not py.exists() or not REQ_MARKER.exists() or not REQ_FILE.exists():\n        return False\n    try:\n        return REQ_MARKER.read_bytes() == REQ_FILE.read_bytes()\n    except OSError:\n        return False\n''',
    '''def runtime_current() -> bool:\n    py = runtime_python()\n    if not py.exists() or not REQ_MARKER.exists() or not REQ_FILE.exists():\n        return False\n    try:\n        if REQ_MARKER.read_bytes() != REQ_FILE.read_bytes():\n            return False\n    except OSError:\n        return False\n\n    # Do not trust a stale marker if the shared venv was damaged or was created\n    # with the wrong Python version. This check is deliberately lightweight.\n    cp = _run([\n        str(py), "-c",\n        "import sys, tkinter; raise SystemExit(0 if sys.version_info[:2] == (3,11) else 2)",\n    ], check=False)\n    return cp.returncode == 0\n''',
)
replace_exact(
    'launcher.py',
    '''def launch_app() -> None:\n    app = APP_DIR / "app.py"\n    if not app.exists():\n        raise RuntimeError(f"Application source is missing: {app}")\n    subprocess.Popen(\n        [str(runtime_pythonw()), str(app)],\n        cwd=str(APP_DIR),\n        creationflags=_creation_flags(),\n    )\n''',
    '''def launch_app() -> None:\n    app = APP_DIR / "app.py"\n    if not app.exists():\n        raise RuntimeError(f"Application source is missing: {app}")\n\n    STARTUP_SENTINEL.parent.mkdir(parents=True, exist_ok=True)\n    try:\n        STARTUP_SENTINEL.unlink(missing_ok=True)\n    except OSError:\n        pass\n\n    # Keep the GUI console-free, but capture Python startup failures instead of\n    # letting pythonw.exe disappear silently. app.py writes STARTUP_SENTINEL only\n    # after the Tk window and full App object are constructed successfully.\n    with APP_LOG_FILE.open("a", encoding="utf-8", errors="replace") as log:\n        log.write("\\n=== TRIAZ Groove Builder startup ===\\n")\n        log.flush()\n        proc = subprocess.Popen(\n            [str(runtime_python()), str(app), "--startup-sentinel", str(STARTUP_SENTINEL)],\n            cwd=str(APP_DIR),\n            stdout=log,\n            stderr=subprocess.STDOUT,\n            creationflags=_creation_flags(),\n        )\n\n        import time\n        deadline = time.monotonic() + 15.0\n        while time.monotonic() < deadline:\n            if STARTUP_SENTINEL.exists():\n                return\n            rc = proc.poll()\n            if rc is not None:\n                try:\n                    detail = APP_LOG_FILE.read_text(encoding="utf-8", errors="replace")[-4500:]\n                except OSError:\n                    detail = "No application log was available."\n                raise RuntimeError(\n                    f"The application exited during startup (code {rc}).\\n\\n{detail}\\n\\nApp log: {APP_LOG_FILE}"\n                )\n            time.sleep(0.15)\n\n        # If the process is alive after 15 seconds, do not kill it. Heavy first\n        # imports can be slow on some systems; startup errors will still be logged.\n        if proc.poll() is not None:\n            raise RuntimeError(f"The application exited during startup. App log: {APP_LOG_FILE}")\n''',
)

# app.py
replace_exact('app.py', 'root.title(f"{APP_NAME} v0.1.9")', 'root.title(f"{APP_NAME} v0.1.10")')
replace_exact('app.py', 'text=("v0.1.9 is approval-gated:', 'text=("v0.1.10 is approval-gated:')
replace_exact(
    'app.py',
    '''def main():\n    if HAS_DND:\n        root = TkinterDnD.Tk()\n    else:\n        root = tk.Tk()\n    App(root)\n    root.mainloop()\n\n\nif __name__ == "__main__":\n    main()\n''',
    '''def _startup_sentinel_arg() -> Path | None:\n    try:\n        i = sys.argv.index("--startup-sentinel")\n        return Path(sys.argv[i + 1])\n    except (ValueError, IndexError):\n        return None\n\n\ndef main():\n    if HAS_DND:\n        root = TkinterDnD.Tk()\n    else:\n        root = tk.Tk()\n    App(root)\n    sentinel = _startup_sentinel_arg()\n    if sentinel is not None:\n        sentinel.parent.mkdir(parents=True, exist_ok=True)\n        sentinel.write_text("ok", encoding="utf-8")\n    root.mainloop()\n\n\nif __name__ == "__main__":\n    try:\n        main()\n    except Exception:\n        crash_log = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME / "app.log"\n        try:\n            crash_log.parent.mkdir(parents=True, exist_ok=True)\n            with crash_log.open("a", encoding="utf-8", errors="replace") as f:\n                f.write("\\n=== APP STARTUP EXCEPTION ===\\n")\n                f.write(traceback.format_exc())\n        except Exception:\n            pass\n        try:\n            tmp = tk.Tk(); tmp.withdraw()\n            messagebox.showerror(APP_NAME, f"TRIAZ Groove Builder could not start.\\n\\nSee log:\\n{crash_log}", parent=tmp)\n            tmp.destroy()\n        except Exception:\n            pass\n        raise\n''',
)

# Installer metadata
replace_exact('installer/triaz_groove_builder.iss', '#define MyAppVersion "0.1.9"', '#define MyAppVersion "0.1.10"')
Path('VERSION').write_text('0.1.10\n', encoding='utf-8')
Path('README.md').write_text(
    '# TRIAZ Groove Builder v0.1.10\n\n'
    'Startup reliability build. The launcher validates the shared Python 3.11 runtime, captures application startup output, waits for the real GUI to signal successful construction, and surfaces startup failures instead of silently disappearing. v0.1.9 Auto Align, Groove Editor, approval-gated export, whole-number BPM, and remembered output folder are retained.\n',
    encoding='utf-8',
)
print('Applied v0.1.10 startup reliability patch')
