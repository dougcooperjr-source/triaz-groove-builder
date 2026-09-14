from __future__ import annotations

from pathlib import Path
import re

ROOT = Path.cwd()


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


# Version bump.
for rel in ("app.py", "launcher.py", "installer/triaz_groove_builder.iss"):
    s = read(rel)
    s = s.replace("v0.1.20", "v0.1.21")
    s = s.replace('APP_VERSION = "0.1.20"', 'APP_VERSION = "0.1.21"')
    s = s.replace('#define MyAppVersion "0.1.20"', '#define MyAppVersion "0.1.21"')
    s = s.replace("TRIAZ_Groove_Builder_Setup_0.1.20.exe", "TRIAZ_Groove_Builder_Setup_0.1.21.exe")
    write(rel, s)
write("VERSION", "0.1.21\n")

app = read("app.py")

# Rename the panel now that the free brace itself defines Bar 1.
app = app.replace("Loop Selection / Bar 1 Override", "Loop Selection / Audition")

# Remember whether the braced source loop is currently being auditioned.
if "self.playing_braced_loop = False" not in app:
    app = app.replace(
        "        self.loop_drag_offset = 0.0\n",
        "        self.loop_drag_offset = 0.0\n        self.playing_braced_loop = False\n",
        1,
    )

# Replace the nudge / Set Bar 1 controls with Play / Stop loop controls.
button_block = re.compile(
    r"        self\.loop_left_btn = ttk\.Button\(loop_buttons, text=.*?"
    r"        self\.loop_hint\.pack\(side=\"left\", padx=\(14, 0\)\)\n",
    re.S,
)
new_buttons = '''        self.loop_play_btn = ttk.Button(loop_buttons, text="▶ Play Braced Loop", command=self.play_braced_loop)
        self.loop_play_btn.pack(side="left")
        self.loop_stop_btn = ttk.Button(loop_buttons, text="■ Stop Loop", command=self.stop_braced_loop)
        self.loop_stop_btn.pack(side="left", padx=(8, 0))
        self.rebuild_brace_btn = ttk.Button(loop_buttons, text="REBUILD PREVIEW FROM BRACE", command=self.rebuild_selected_from_brace)
        self.rebuild_brace_btn.pack(side="left", padx=(8, 0))
        self.loop_hint = ttk.Label(loop_buttons, text="Drag the brace freely, play/stop the loop to confirm it feels right, then rebuild. No preset is exported until Approve & Export.")
        self.loop_hint.pack(side="left", padx=(14, 0))
'''
app, n = button_block.subn(new_buttons, app, count=1)
if n != 1:
    raise RuntimeError("Could not replace loop button block")

# Button enable/disable now only controls play, stop, and rebuild.
set_buttons_pat = re.compile(
    r"    def _set_loop_buttons\(self, enabled: bool\):\n.*?(?=\n    def _selected_index)",
    re.S,
)
new_set_buttons = '''    def _set_loop_buttons(self, enabled: bool):
        state = ["!disabled"] if enabled else ["disabled"]
        for name in ("loop_play_btn", "loop_stop_btn", "rebuild_brace_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.state(state)
'''
app, n = set_buttons_pat.subn(new_set_buttons, app, count=1)
if n != 1:
    raise RuntimeError("Could not replace _set_loop_buttons")

# Remove the Set Bar 1 dialog and the nudge-only method from the code path.
app = re.sub(r"\n    def _nudge_brace_seconds\(self, seconds_delta: float\):\n.*?(?=\n    def _set_bar1_dialog|\n    def _draw_loop_selection)", "\n", app, flags=re.S)
app = re.sub(r"\n    def _set_bar1_dialog\(self\):\n.*?(?=\n    def _draw_loop_selection)", "\n", app, flags=re.S)

# When auditioning, moving the brace should restart playback at mouse release so
# the newly braced section is heard immediately without forcing a preview rebuild.
app = re.sub(
    r"    def _loop_canvas_release\(self, _event\):\n        self\.loop_dragging = False\n",
    "    def _loop_canvas_release(self, _event):\n        self.loop_dragging = False\n        if getattr(self, \"playing_braced_loop\", False):\n            self.play_braced_loop()\n",
    app,
    count=1,
)

loop_play_methods = r'''
    def _write_braced_loop_wav(self, idx: int) -> Path | None:
        """Write the currently braced source context to a temporary WAV for loop audition.

        This is preview-only. It does not rebuild the grid and it does not export
        a TRIAZ preset. The point is immediate loop-start verification.
        """
        info = self._loop_info(idx)
        if info is None:
            return None
        _result, y, sr, context_start, loop_start, loop_duration, _bpm = info
        import numpy as _np
        y = _np.asarray(y, dtype=float)
        if y.size < 2 or sr <= 0:
            return None
        context_duration = len(y) / float(sr)
        rel = max(0.0, min(float(loop_start) - float(context_start), max(0.0, context_duration - float(loop_duration))))
        i0 = max(0, min(len(y) - 1, int(round(rel * float(sr)))))
        i1 = min(len(y), i0 + max(1, int(round(float(loop_duration) * float(sr)))))
        segment = _np.asarray(y[i0:i1], dtype=float)
        if segment.size < 2:
            return None
        if segment.ndim > 1:
            segment = _np.mean(segment, axis=1)
        segment = _np.nan_to_num(segment, nan=0.0, posinf=0.0, neginf=0.0)
        peak = float(_np.max(_np.abs(segment))) if segment.size else 0.0
        if peak > 1.0:
            segment = segment / peak
        pcm = (_np.clip(segment, -1.0, 1.0) * 32767.0).astype(_np.int16)
        PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
        out = PREVIEW_ROOT / f"_braced_loop_{idx}.wav"
        with wave.open(str(out), "wb") as dst:
            dst.setnchannels(1)
            dst.setsampwidth(2)
            dst.setframerate(int(sr))
            dst.writeframes(pcm.tobytes())
        return out

    def play_braced_loop(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo(APP_NAME, "Select an analyzed row first.")
            return
        if os.name != "nt":
            messagebox.showinfo(APP_NAME, "Built-in loop audition playback is currently enabled on Windows only.")
            return
        path = self._write_braced_loop_wav(idx)
        if path is None or not path.exists():
            messagebox.showinfo(APP_NAME, "Analyze this row first so a braced source loop is available.")
            return
        try:
            import winsound
            self._stop_preview()
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP
            winsound.PlaySound(str(path), flags)
            self.playing_braced_loop = True
            self.playing_preview = (idx, "braced_loop")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not play braced loop:\n{exc}")

    def stop_braced_loop(self):
        self._stop_preview()
'''
if "def play_braced_loop" not in app:
    app = app.replace("    def _on_close(self):\n", loop_play_methods + "\n    def _on_close(self):\n", 1)

# Stop state should clear loop audition too.
app = app.replace(
    "        self.playing_preview = None\n",
    "        self.playing_preview = None\n        self.playing_braced_loop = False\n",
    1,
)

# Validate user-requested UI change.
if "Set Bar 1" in app:
    raise RuntimeError("Set Bar 1 control/text still present")
if "10 ms" in app or "_nudge_brace_seconds" in app:
    raise RuntimeError("Nudge control/text still present")
if "▶ Play Braced Loop" not in app or "■ Stop Loop" not in app:
    raise RuntimeError("Braced loop play/stop controls missing")
if "def play_braced_loop" not in app:
    raise RuntimeError("play_braced_loop method missing")

write("app.py", app)
print("v0.1.21 braced loop audition patch applied")
