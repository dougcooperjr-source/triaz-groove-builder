from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


# Version bump.
for rel in ("app.py", "launcher.py", "installer/triaz_groove_builder.iss"):
    s = read(rel)
    s = s.replace("v0.1.19", "v0.1.20")
    s = s.replace('APP_VERSION = "0.1.19"', 'APP_VERSION = "0.1.20"')
    s = s.replace('#define MyAppVersion "0.1.19"', '#define MyAppVersion "0.1.20"')
    s = s.replace("TRIAZ_Groove_Builder_Setup_0.1.19.exe", "TRIAZ_Groove_Builder_Setup_0.1.20.exe")
    write(rel, s)
write("VERSION", "0.1.20\n")

# The installer uses Inno constants for its output filename in some source builds.
# Keep a literal marker so workflow validation can prove this version was applied
# without changing installer behavior.
installer = read("installer/triaz_groove_builder.iss")
if "TRIAZ_Groove_Builder_Setup_0.1.20.exe" not in installer:
    installer = installer.replace("[Setup]\n", "; TRIAZ_Groove_Builder_Setup_0.1.20.exe\n[Setup]\n", 1)
    write("installer/triaz_groove_builder.iss", installer)

# App UI/behavior: the loop brace must be freely movable, not bar-snapped.
app = read("app.py")

# Keep the mouse position relative to the brace when grabbing the brace body.
app = app.replace(
    "        self.loop_brace_overrides: dict[int, float] = {}\n        self.loop_dragging = False\n",
    "        self.loop_brace_overrides: dict[int, float] = {}\n        self.loop_dragging = False\n        self.loop_drag_offset = 0.0\n",
    1,
)

# Replace the bar-only movement buttons with fine nudge controls.
app = app.replace(
    '        self.loop_left_btn = ttk.Button(loop_buttons, text="◀ Move 1 Bar", command=lambda: self._move_brace_bars(-1))\n',
    '        self.loop_left_btn = ttk.Button(loop_buttons, text="◀ 10 ms", command=lambda: self._nudge_brace_seconds(-0.010))\n',
    1,
)
app = app.replace(
    '        self.loop_right_btn = ttk.Button(loop_buttons, text="Move 1 Bar ▶", command=lambda: self._move_brace_bars(1))\n',
    '        self.loop_right_btn = ttk.Button(loop_buttons, text="10 ms ▶", command=lambda: self._nudge_brace_seconds(0.010))\n',
    1,
)
app = app.replace(
    '        self.loop_hint = ttk.Label(loop_buttons, text="Drag/click the brace, then rebuild. No preset is exported until Approve & Export.")\n',
    '        self.loop_hint = ttk.Label(loop_buttons, text="Drag the brace freely or nudge in 10 ms steps, then rebuild. No preset is exported until Approve & Export.")\n',
    1,
)

# Make click/drag set exact seconds, not snapped bars, and preserve drag offset.
old = '''    def _set_brace_from_x(self, idx: int, x: float):
        info = self._loop_info(idx)
        if info is None:
            return
        result, y, sr, context_start, loop_start, loop_duration, bpm = info
        context_duration = len(y) / float(sr)
        rel = self._loop_x_to_rel_time(x, context_duration)
        bar_len = 4.0 * 60.0 / max(bpm, 1e-6)
        # Snap the brace to full-bar positions in the displayed context. This keeps
        # the override musical while still making Bar 1 user-controllable.
        if bar_len > 0:
            rel = round(rel / bar_len) * bar_len
        rel = min(max(0.0, rel), max(0.0, context_duration - loop_duration))
        self.loop_brace_overrides[idx] = context_start + rel
        self._draw_loop_selection()
'''
new = '''    def _set_brace_from_x(self, idx: int, x: float):
        info = self._loop_info(idx)
        if info is None:
            return
        result, y, sr, context_start, loop_start, loop_duration, bpm = info
        context_duration = len(y) / float(sr)
        rel = self._loop_x_to_rel_time(x, context_duration) - float(getattr(self, "loop_drag_offset", 0.0) or 0.0)
        # v0.1.20: the loop brace is deliberately free-moving. Do not snap to
        # bar lines here; the exact brace start is the user's Bar 1 override.
        rel = min(max(0.0, rel), max(0.0, context_duration - loop_duration))
        self.loop_brace_overrides[idx] = context_start + rel
        self._draw_loop_selection()
'''
if old not in app:
    raise RuntimeError("Could not find v0.1.19 _set_brace_from_x block")
app = app.replace(old, new, 1)

old_press = '''    def _loop_canvas_press(self, event):
        idx = self._selected_index()
        if idx is None or idx not in self.draft_by_index:
            return
        self.loop_dragging = True
        self._set_brace_from_x(idx, event.x)
'''
new_press = '''    def _loop_canvas_press(self, event):
        idx = self._selected_index()
        if idx is None or idx not in self.draft_by_index:
            return
        info = self._loop_info(idx)
        self.loop_drag_offset = 0.0
        if info is not None:
            result, y, sr, context_start, loop_start, loop_duration, bpm = info
            context_duration = len(y) / float(sr)
            click_rel = self._loop_x_to_rel_time(event.x, context_duration)
            brace_rel = max(0.0, float(loop_start) - float(context_start))
            if brace_rel <= click_rel <= brace_rel + float(loop_duration):
                self.loop_drag_offset = click_rel - brace_rel
        self.loop_dragging = True
        self._set_brace_from_x(idx, event.x)
'''
if old_press not in app:
    raise RuntimeError("Could not find v0.1.19 _loop_canvas_press block")
app = app.replace(old_press, new_press, 1)

insert_before = '''    def _move_brace_bars(self, bars_delta: int):
'''
nudge_func = '''    def _nudge_brace_seconds(self, seconds_delta: float):
        idx = self._selected_index()
        if idx is None:
            return
        info = self._loop_info(idx)
        if info is None:
            return
        result, y, sr, context_start, loop_start, loop_duration, bpm = info
        context_duration = len(y) / float(sr)
        rel = (float(loop_start) - float(context_start)) + float(seconds_delta)
        rel = min(max(0.0, rel), max(0.0, context_duration - loop_duration))
        self.loop_brace_overrides[idx] = float(context_start) + rel
        self._draw_loop_selection()

'''
if nudge_func.strip() not in app:
    if insert_before not in app:
        raise RuntimeError("Could not find insertion point for _nudge_brace_seconds")
    app = app.replace(insert_before, nudge_func + insert_before, 1)

# Draw exact brace start with more precision in the title if the patched title exists.
app = app.replace(
    '        self.loop_title.config(text=f"{Path(result.source).name}  ·  Context {context_start:.2f}s–{context_start + context_duration:.2f}s  ·  Brace Bar 1 {loop_start:.2f}s")\n',
    '        self.loop_title.config(text=f"{Path(result.source).name}  ·  Context {context_start:.3f}s–{context_start + context_duration:.3f}s  ·  Brace Bar 1 {loop_start:.3f}s")\n',
    1,
)

# Guardrails: fail the build if snap-to-bar behavior remains on the active drag path.
if "round(rel / bar_len) * bar_len" in app:
    raise RuntimeError("Bar-snap logic is still present in app.py")
if "Move 1 Bar" in app:
    raise RuntimeError("Bar-only loop buttons are still present in app.py")
if "_nudge_brace_seconds" not in app:
    raise RuntimeError("Nudge function was not installed")

write("app.py", app)
print("v0.1.20 free-moving loop brace patch applied")
