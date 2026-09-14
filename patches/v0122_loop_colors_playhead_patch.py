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
    s = s.replace("v0.1.21", "v0.1.22")
    s = s.replace('APP_VERSION = "0.1.21"', 'APP_VERSION = "0.1.22"')
    s = s.replace('#define MyAppVersion "0.1.21"', '#define MyAppVersion "0.1.22"')
    s = s.replace("TRIAZ_Groove_Builder_Setup_0.1.21.exe", "TRIAZ_Groove_Builder_Setup_0.1.22.exe")
    write(rel, s)
write("VERSION", "0.1.22\n")

app = read("app.py")

# The loop-audition view now needs real-time visual feedback: source-family colors
# in the waveform and a playhead inside the free-moving 4-bar brace.
if "import time" not in app:
    if "import wave\n" in app:
        app = app.replace("import wave\n", "import wave\nimport time\n", 1)
    else:
        app = app.replace("import json\n", "import json\nimport time\n", 1)

if "self.loop_marker_cache" not in app:
    app = app.replace(
        "        self.playing_braced_loop = False\n",
        "        self.playing_braced_loop = False\n"
        "        self.loop_play_start_time = 0.0\n"
        "        self.loop_play_duration = 0.0\n"
        "        self.loop_playhead_job = None\n"
        "        self.loop_marker_cache = {}\n",
        1,
    )

app = app.replace(
    'text="Drag the brace freely, play/stop the loop to confirm it feels right, then rebuild. No preset is exported until Approve & Export."',
    'text="Drag the brace freely. Red=kick, yellow=snare/clap, cyan=hat. Play/stop the loop to confirm it loops correctly, then rebuild."',
)

helpers = r'''
    def _loop_source_family_markers(self, idx: int, y, sr: int, context_start: float):
        """Return lightweight kick/snare/hat markers for the loop-selection waveform.

        This is display-only. It does not rewrite the TRIAZ grid. It makes the
        braced loop view match the editor expectation: red kick, yellow
        snare/clap, cyan hat.
        """
        import numpy as _np
        y = _np.asarray(y, dtype=float)
        if y.ndim > 1:
            y = _np.mean(y, axis=1)
        if sr <= 0 or y.size < 2048:
            return []
        cache_key = (int(idx), int(y.size), int(sr), round(float(context_start), 3))
        cached = getattr(self, "loop_marker_cache", {}).get(cache_key)
        if cached is not None:
            return cached

        hop = 256 if sr >= 32000 else 128
        win = 2048 if sr >= 32000 else 1024
        if y.size <= win:
            return []
        n_frames = 1 + (y.size - win) // hop
        if n_frames <= 2:
            return []

        # Frame energy onset detection.
        frames = []
        rms = _np.empty(n_frames, dtype=float)
        for i in range(n_frames):
            a = i * hop
            seg = y[a:a + win]
            seg = _np.nan_to_num(seg, nan=0.0, posinf=0.0, neginf=0.0)
            rms[i] = float(_np.sqrt(_np.mean(seg * seg) + 1e-12))
        onset = _np.maximum(0.0, _np.diff(rms, prepend=rms[0]))
        positive = onset[onset > 0]
        if positive.size == 0:
            return []
        threshold = max(float(_np.quantile(positive, 0.82)), float(_np.max(onset)) * 0.10)
        min_sep = max(1, int(round(0.045 * float(sr) / float(hop))))

        candidates = []
        last = -10**9
        for i in range(1, n_frames - 1):
            if onset[i] < threshold or onset[i] < onset[i - 1] or onset[i] < onset[i + 1]:
                continue
            if i - last < min_sep:
                if candidates and onset[i] > candidates[-1][0]:
                    candidates[-1] = (float(onset[i]), i)
                    last = i
                continue
            candidates.append((float(onset[i]), i))
            last = i

        if not candidates:
            return []
        # Keep the visible overlay readable.
        if len(candidates) > 180:
            candidates = sorted(candidates, reverse=True)[:180]
            candidates.sort(key=lambda item: item[1])

        freqs = _np.fft.rfftfreq(win, 1.0 / float(sr))
        low_mask = freqs <= 180.0
        lowmid_mask = (freqs > 180.0) & (freqs <= 450.0)
        mid_mask = (freqs > 450.0) & (freqs <= 3500.0)
        high_mask = freqs > 3500.0
        window = _np.hanning(win)
        markers = []
        for strength, frame_idx in candidates:
            a = max(0, min(y.size - win, int(frame_idx) * hop))
            seg = _np.nan_to_num(y[a:a + win], nan=0.0, posinf=0.0, neginf=0.0)
            mag = _np.abs(_np.fft.rfft(seg * window)) + 1e-12
            e_low = float(_np.sum(mag[low_mask]))
            e_lowmid = float(_np.sum(mag[lowmid_mask]))
            e_mid = float(_np.sum(mag[mid_mask]))
            e_high = float(_np.sum(mag[high_mask]))
            total = max(e_low + e_lowmid + e_mid + e_high, 1e-12)
            low_ratio = (e_low + 0.45 * e_lowmid) / total
            mid_ratio = (0.35 * e_lowmid + e_mid) / total
            high_ratio = e_high / total
            if low_ratio >= 0.34 and low_ratio >= high_ratio * 0.85:
                fam = "kick"
            elif high_ratio >= 0.48 and low_ratio < 0.27:
                fam = "hat"
            else:
                fam = "snare"
            t = (a + win * 0.5) / float(sr)
            markers.append((float(t), fam, float(strength)))

        self.loop_marker_cache[cache_key] = markers
        # Keep the cache bounded across batch work.
        if len(self.loop_marker_cache) > 24:
            for key in list(self.loop_marker_cache.keys())[:-12]:
                self.loop_marker_cache.pop(key, None)
        return markers

    def _schedule_loop_playhead(self):
        if not getattr(self, "playing_braced_loop", False):
            self.loop_playhead_job = None
            return
        self._draw_loop_selection()
        self.loop_playhead_job = self.root.after(60, self._schedule_loop_playhead)
'''
if "def _loop_source_family_markers" not in app:
    app = app.replace("\n    def _draw_loop_selection(self):", "\n" + helpers.rstrip() + "\n\n    def _draw_loop_selection(self):", 1)

new_draw = r'''
    def _draw_loop_selection(self):
        c = getattr(self, "loop_canvas", None)
        if c is None:
            return
        c.delete("all")
        idx = self._selected_index()
        if idx is None or idx not in self.draft_by_index:
            self.loop_title.config(text="Analyze a row to view the suggested source context and 4-bar brace.")
            self._set_loop_buttons(False)
            c.create_text(16, 52, anchor="w", fill="#aeb2b6", text="No analyzed row selected.")
            return
        info = self._loop_info(idx)
        if info is None:
            self.loop_title.config(text="Loop context unavailable for this row.")
            self._set_loop_buttons(False)
            c.create_text(16, 52, anchor="w", fill="#aeb2b6", text="No loop context available.")
            return
        result, y, sr, context_start, loop_start, loop_duration, bpm = info
        self._set_loop_buttons(not self.processing)

        import numpy as _np
        y = _np.asarray(y, dtype=float)
        if y.ndim > 1:
            y = _np.mean(y, axis=1)
        context_duration = len(y) / float(sr)
        x0, x1 = self._loop_bounds()
        top, mid, bottom = 20, 52, 88
        c.create_rectangle(x0, top, x1, bottom, fill="#151719", outline="#414346")

        # Audio waveform.
        if y.size:
            bins = max(180, int(x1 - x0))
            edges = _np.linspace(0, len(y), bins + 1, dtype=int)
            peak = max(float(_np.max(_np.abs(y))), 1e-9)
            points = []
            for i in range(bins):
                seg = y[edges[i]:edges[i + 1]]
                amp = float(_np.max(_np.abs(seg))) / peak if seg.size else 0.0
                x = x0 + (x1 - x0) * i / max(1, bins - 1)
                yy = mid - amp * 27.0
                points.append((x, yy, mid + amp * 27.0))
            for x, ya, yb in points:
                c.create_line(x, ya, x, yb, fill="#6d747c")

        # Bar guides.
        bar_len = 4.0 * 60.0 / max(float(bpm), 1e-6)
        if bar_len > 0:
            n = int(context_duration / bar_len) + 2
            for bar in range(n):
                t = bar * bar_len
                if 0.0 <= t <= context_duration:
                    x = self._loop_time_to_x(t, context_duration)
                    c.create_line(x, top, x, bottom, fill="#303337")
                    c.create_text(x + 3, top + 2, anchor="nw", fill="#777d84", text=f"{bar + 1}")

        # Colored source-family markers in the waveform.
        colors = {"kick": "#ff4b4b", "snare": "#ffd447", "hat": "#38d8ff"}
        labels = {"kick": "K", "snare": "S", "hat": "H"}
        for t, fam, strength in self._loop_source_family_markers(idx, y, sr, context_start):
            if not (0.0 <= float(t) <= context_duration):
                continue
            x = self._loop_time_to_x(float(t), context_duration)
            color = colors.get(fam, "#aeb2b6")
            width = 3 if fam == "kick" else 2
            c.create_line(x, top + 2, x, bottom - 2, fill=color, width=width)
            if fam in labels and strength >= 0.05:
                c.create_text(x + 2, bottom - 14, anchor="w", fill=color, text=labels[fam])

        brace_rel = max(0.0, min(float(loop_start) - float(context_start), max(0.0, context_duration - float(loop_duration))))
        brace_end = min(context_duration, brace_rel + float(loop_duration))
        bx0 = self._loop_time_to_x(brace_rel, context_duration)
        bx1 = self._loop_time_to_x(brace_end, context_duration)
        c.create_rectangle(bx0, top - 5, bx1, bottom + 5, outline="#4ee07f", width=3)
        c.create_rectangle(bx0 - 3, top - 7, bx0 + 3, bottom + 7, fill="#4ee07f", outline="#4ee07f")
        c.create_rectangle(bx1 - 3, top - 7, bx1 + 3, bottom + 7, fill="#4ee07f", outline="#4ee07f")
        c.create_text(bx0 + 6, top - 16, anchor="w", fill="#4ee07f", text="4-bar brace")

        # Playhead inside the braced loop while loop audition is running.
        if getattr(self, "playing_braced_loop", False) and float(loop_duration) > 0:
            elapsed = max(0.0, time.monotonic() - float(getattr(self, "loop_play_start_time", 0.0)))
            play_rel = brace_rel + (elapsed % float(loop_duration))
            px = self._loop_time_to_x(play_rel, context_duration)
            c.create_line(px, top - 10, px, bottom + 10, fill="#ffffff", width=2)
            c.create_polygon(px - 5, top - 10, px + 5, top - 10, px, top - 2, fill="#ffffff", outline="#ffffff")

        source_name = getattr(getattr(result, "source", None), "name", "selected source")
        self.loop_title.config(
            text=(
                f"{source_name} — brace {float(loop_start):.3f}s to {float(loop_start) + float(loop_duration):.3f}s "
                f"| red=kick yellow=snare/clap cyan=hat"
            )
        )
        c.create_text(x0, bottom + 10, anchor="nw", fill="#aeb2b6", text="Play Braced Loop shows this exact free brace with a live playhead.")
'''
draw_pat = re.compile(r"\n    def _draw_loop_selection\(self\):\n.*?(?=\n    def [A-Za-z_][A-Za-z0-9_]*\(self)", re.S)
app, n = draw_pat.subn("\n" + new_draw.rstrip() + "\n", app, count=1)
if n != 1:
    raise RuntimeError("Could not replace _draw_loop_selection")

# Reset the playhead timing whenever a new braced loop starts.
old_play = '            self.playing_braced_loop = True\n            self.playing_preview = (idx, "braced_loop")\n'
new_play = '''            if getattr(self, "loop_playhead_job", None) is not None:
                try:
                    self.root.after_cancel(self.loop_playhead_job)
                except Exception:
                    pass
                self.loop_playhead_job = None
            info = self._loop_info(idx)
            if info is not None:
                _result, _y, _sr, _context_start, _loop_start, loop_duration, _bpm = info
                self.loop_play_duration = float(loop_duration)
            self.loop_play_start_time = time.monotonic()
            self.playing_braced_loop = True
            self.playing_preview = (idx, "braced_loop")
            self._schedule_loop_playhead()
'''
if old_play not in app:
    raise RuntimeError("Could not find braced-loop playback state block")
app = app.replace(old_play, new_play, 1)

old_stop = '''    def stop_braced_loop(self):
        self._stop_preview()
'''
new_stop = '''    def stop_braced_loop(self):
        if getattr(self, "loop_playhead_job", None) is not None:
            try:
                self.root.after_cancel(self.loop_playhead_job)
            except Exception:
                pass
            self.loop_playhead_job = None
        self._stop_preview()
        self.loop_play_start_time = 0.0
        self.loop_play_duration = 0.0
        self._draw_loop_selection()
'''
if old_stop not in app:
    raise RuntimeError("Could not find stop_braced_loop")
app = app.replace(old_stop, new_stop, 1)

# Closing/stopping should clear any scheduled playhead timer.
if "loop_playhead_job" in app and "after_cancel(self.loop_playhead_job)" not in app.split("def stop_braced_loop", 1)[0]:
    pass

# User-requested validation.
if "def _loop_source_family_markers" not in app:
    raise RuntimeError("Loop waveform color marker helper missing")
if "red=kick" not in app or "yellow=snare" not in app or "cyan=hat" not in app:
    raise RuntimeError("Kick/snare/hat color legend missing")
if "loop_play_start_time" not in app or "_schedule_loop_playhead" not in app:
    raise RuntimeError("Loop playhead support missing")
if "Set Bar 1" in app or "10 ms" in app or "_nudge_brace_seconds" in app:
    raise RuntimeError("Removed controls came back")

write("app.py", app)
print("v0.1.22 loop waveform colors / playhead patch applied")
