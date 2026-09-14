from __future__ import annotations

from pathlib import Path
import re

ROOT = Path.cwd()


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding='utf-8')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f'Missing patch anchor: {label}')
    return text.replace(old, new, 1)


# Version bump.
for rel in ('app.py', 'launcher.py', 'installer/triaz_groove_builder.iss'):
    s = read(rel)
    s = s.replace('v0.1.23', 'v0.1.24')
    s = s.replace('APP_VERSION = "0.1.23"', 'APP_VERSION = "0.1.24"')
    s = s.replace('#define MyAppVersion "0.1.23"', '#define MyAppVersion "0.1.24"')
    s = s.replace('TRIAZ_Groove_Builder_Setup_0.1.23.exe', 'TRIAZ_Groove_Builder_Setup_0.1.24.exe')
    write(rel, s)
write('VERSION', '0.1.24\n')

# ---------------------------------------------------------------------------
# Preview audio: fuller kick + source audition filters + true source/groove mix.
# ---------------------------------------------------------------------------
preview = read('triaz_groove_builder/preview.py')

kick_pat = re.compile(r"def _kick\(sr: int, velocity: float\) -> np\.ndarray:\n.*?(?=\n\ndef _snare)", re.S)
new_kick = r'''def _kick(sr: int, velocity: float) -> np.ndarray:
    """Fuller preview kick: low/body-forward with minimal metronome-like click."""
    dur = 0.34
    n = max(1, int(sr * dur))
    t = np.arange(n, dtype=np.float32) / sr

    # Fast pitch drop into a sustained low body.  The previous preview kick had a
    # strong 1.2 kHz transient, which read more like a metronome than a drum.
    freq = 46.0 + 62.0 * np.exp(-t * 24.0)
    phase = 2.0 * np.pi * np.cumsum(freq) / sr
    body = np.sin(phase) * np.exp(-t * 11.5)
    punch = np.sin(2.0 * np.pi * 92.0 * t) * np.exp(-t * 24.0)
    knock = np.sin(2.0 * np.pi * 175.0 * t) * np.exp(-t * 46.0)
    attack = 1.0 - np.exp(-t * 170.0)
    y = attack * (0.98 * body + 0.20 * punch + 0.07 * knock)
    # Mild soft clipping adds body without adding a bright click.
    y = np.tanh(1.35 * y) / np.tanh(1.35)
    return (0.92 * y * velocity).astype(np.float32)
'''
preview, n = kick_pat.subn(new_kick.rstrip(), preview, count=1)
if n != 1:
    raise RuntimeError('Could not replace preview kick')

helpers = r'''

def filter_source_audition(y: np.ndarray, sr: int, mode: str = "OFF") -> np.ndarray:
    """Zero-phase listening filter for Source Drums in the Groove Editor.

    This is audition-only and never changes transcription or exported pattern data.
    OFF = full range, LP = kick/low focus, BP = snare/clap/percussion focus,
    HP = hat/high-percussion focus.
    """
    data = np.asarray(y, dtype=np.float32)
    if data.ndim > 1:
        data = np.mean(data, axis=1).astype(np.float32)
    mode = str(mode or "OFF").upper()
    if mode == "OFF" or data.size < 16 or int(sr) <= 0:
        return data.copy()
    try:
        from scipy.signal import butter, sosfiltfilt
        nyq = max(float(sr) * 0.5, 1.0)
        if mode == "LP":
            wn = min(0.95, 220.0 / nyq)
            sos = butter(4, wn, btype="lowpass", output="sos")
        elif mode == "BP":
            lo = max(0.001, 180.0 / nyq)
            hi = min(0.95, 3200.0 / nyq)
            if hi <= lo:
                return data.copy()
            sos = butter(4, [lo, hi], btype="bandpass", output="sos")
        elif mode == "HP":
            wn = min(0.95, 3500.0 / nyq)
            sos = butter(4, wn, btype="highpass", output="sos")
        else:
            return data.copy()
        # sosfiltfilt avoids adding timing/phase shift while judging groove alignment.
        return np.asarray(sosfiltfilt(sos, data, axis=0), dtype=np.float32)
    except Exception:
        # Filtering is a listening aid; never break preview playback because of it.
        return data.copy()


def render_editor_overlay(
    source_drums: np.ndarray,
    generated_groove: np.ndarray,
    sr: int,
    source_db: float = 0.0,
    groove_db: float = 0.0,
    source_filter: str = "OFF",
) -> np.ndarray:
    """Mix Source Drums + Generated Groove for editor A/B alignment listening."""
    src = filter_source_audition(source_drums, sr, source_filter)
    gen = np.asarray(generated_groove, dtype=np.float32)
    if gen.ndim > 1:
        gen = np.mean(gen, axis=1).astype(np.float32)
    n = max(len(src), len(gen))
    if n <= 0:
        return np.zeros(1, dtype=np.float32)
    out = np.zeros(n, dtype=np.float32)
    src_gain = float(10.0 ** (float(source_db) / 20.0))
    groove_gain = float(10.0 ** (float(groove_db) / 20.0))
    out[:len(src)] += src * src_gain
    out[:len(gen)] += gen * groove_gain
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.98:
        out *= 0.98 / peak
    return out.astype(np.float32)
'''
if 'def filter_source_audition' not in preview:
    preview = replace_once(preview, '\ndef render_generated_groove(', helpers + '\ndef render_generated_groove(', 'preview helper insertion')

write('triaz_groove_builder/preview.py', preview)

# ---------------------------------------------------------------------------
# App: dynamic editor playback paths for filters and independent overlay levels.
# ---------------------------------------------------------------------------
app = read('app.py')
app = replace_once(
    app,
    'from triaz_groove_builder.editor import GrooveEditorPage\n',
    'from triaz_groove_builder.editor import GrooveEditorPage\nfrom triaz_groove_builder.preview import filter_source_audition, render_editor_overlay\n',
    'preview helper imports',
)

editor_play_methods = r'''
    def _write_editor_source_preview(self, idx: int, source_filter: str = "OFF") -> Path:
        import soundfile as sf
        assets = self.preview_by_index.get(idx) or {}
        source_path = Path(assets.get("source_drums", ""))
        if not source_path.exists():
            raise RuntimeError("Source Drums preview is not available.")
        y, sr = sf.read(source_path, dtype="float32", always_2d=False)
        y = filter_source_audition(y, int(sr), source_filter)
        out = source_path.with_name(f"_Editor Source {str(source_filter).upper()}.wav")
        sf.write(out, y, int(sr), subtype="PCM_16")
        return out

    def _write_editor_overlay_preview(
        self,
        idx: int,
        source_filter: str = "OFF",
        source_db: float = 0.0,
        groove_db: float = 0.0,
    ) -> Path:
        import soundfile as sf
        assets = self.preview_by_index.get(idx) or {}
        source_path = Path(assets.get("source_drums", ""))
        groove_path = Path(assets.get("generated_groove", ""))
        if not source_path.exists() or not groove_path.exists():
            raise RuntimeError("Source/Groove previews are not available.")
        src, sr = sf.read(source_path, dtype="float32", always_2d=False)
        groove, gsr = sf.read(groove_path, dtype="float32", always_2d=False)
        if int(gsr) != int(sr):
            raise RuntimeError("Source and Groove preview sample rates do not match.")
        mix = render_editor_overlay(
            src,
            groove,
            int(sr),
            source_db=float(source_db),
            groove_db=float(groove_db),
            source_filter=source_filter,
        )
        mode = str(source_filter).upper()
        s_tag = f"{float(source_db):+.1f}".replace("+", "p").replace("-", "m").replace(".", "_")
        g_tag = f"{float(groove_db):+.1f}".replace("+", "p").replace("-", "m").replace(".", "_")
        out = source_path.with_name(f"_Editor Overlay {mode} S{s_tag} G{g_tag}.wav")
        sf.write(out, mix, int(sr), subtype="PCM_16")
        return out

    def play_editor_preview_for_index(
        self,
        idx: int,
        kind: str,
        start_seconds: float = 0.0,
        source_filter: str = "OFF",
        source_db: float = 0.0,
        groove_db: float = 0.0,
    ):
        """Editor-only playback with source filtering and true Source+Groove overlay."""
        if os.name != "nt":
            messagebox.showinfo(APP_NAME, "Built-in preview playback is currently enabled on Windows only.")
            return
        assets = self.preview_by_index.get(idx) or {}
        if kind == "timing_overlay":
            path = self._write_editor_overlay_preview(idx, source_filter, source_db, groove_db)
        elif kind == "source_drums" and str(source_filter).upper() != "OFF":
            path = self._write_editor_source_preview(idx, source_filter)
        else:
            raw = assets.get(kind)
            if not raw:
                return
            path = Path(raw)
        if not path.exists():
            messagebox.showerror(APP_NAME, f"Preview file was not found:\n{path}")
            return
        try:
            import winsound
            self._stop_preview()
            play_path = self._preview_from_offset(path, start_seconds)
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC
            if self.preview_loop.get():
                flags |= winsound.SND_LOOP
            winsound.PlaySound(str(play_path), flags)
            self.playing_preview = (idx, kind)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not play editor preview:\n{exc}")

'''
if 'def play_editor_preview_for_index' not in app:
    app = replace_once(app, '    def play_preview_for_index(self, idx: int, kind: str, start_seconds: float = 0.0):\n', editor_play_methods + '    def play_preview_for_index(self, idx: int, kind: str, start_seconds: float = 0.0):\n', 'editor playback insertion')

write('app.py', app)

# ---------------------------------------------------------------------------
# Groove Editor UI: Source LP/BP/HP audition and independent overlay volumes.
# ---------------------------------------------------------------------------
editor = read('triaz_groove_builder/editor.py')

state_anchor = '''        self.play_after_id: str | None = None\n\n        root = ttk.Frame(parent, padding=12)\n'''
state_new = '''        self.play_after_id: str | None = None\n        self.source_filter = tk.StringVar(value="OFF")\n        self.overlay_source_db = tk.DoubleVar(value=0.0)\n        self.overlay_groove_db = tk.DoubleVar(value=0.0)\n\n        root = ttk.Frame(parent, padding=12)\n'''
editor = replace_once(editor, state_anchor, state_new, 'editor preview state')

transport_old = '''        self.overlay_btn = ttk.Button(transport, text="▶ PLAY OVERLAY", command=lambda: self.play("timing_overlay"))\n        self.overlay_btn.pack(side="left")\n        ttk.Button(transport, text="▶ Play Source", command=lambda: self.play("source_drums")).pack(side="left", padx=(8, 0))\n        ttk.Button(transport, text="▶ Play Groove", command=lambda: self.play("generated_groove")).pack(side="left", padx=(8, 0))\n        ttk.Button(transport, text="■ Stop", command=self.stop_playback).pack(side="left", padx=(8, 0))\n        ttk.Checkbutton(transport, text="Loop", variable=self.app.preview_loop).pack(side="left", padx=(16, 0))\n        ttk.Label(\n            transport,\n            text="Drag-select multiple hits • Shift-click toggles selection • drag a selected hit to move the group.",\n        ).pack(side="left", padx=(18, 0))\n\n        self.link_notice = ttk.Label(root, text="")\n'''
transport_new = '''        self.overlay_btn = ttk.Button(transport, text="▶ PLAY OVERLAY", command=lambda: self.play("timing_overlay"))\n        self.overlay_btn.pack(side="left")\n        ttk.Button(transport, text="▶ Play Source", command=lambda: self.play("source_drums")).pack(side="left", padx=(8, 0))\n        ttk.Button(transport, text="▶ Play Groove", command=lambda: self.play("generated_groove")).pack(side="left", padx=(8, 0))\n        ttk.Button(transport, text="■ Stop", command=self.stop_playback).pack(side="left", padx=(8, 0))\n        ttk.Checkbutton(transport, text="Loop", variable=self.app.preview_loop).pack(side="left", padx=(16, 0))\n        ttk.Label(\n            transport,\n            text="Drag-select multiple hits • Shift-click toggles selection • drag a selected hit to move the group.",\n        ).pack(side="left", padx=(18, 0))\n\n        audition = ttk.Frame(root)\n        audition.pack(fill="x", pady=(0, 6))\n        ttk.Label(audition, text="SOURCE FILTER", font=("Segoe UI", 8, "bold")).pack(side="left")\n        for mode in ("OFF", "LP", "BP", "HP"):\n            ttk.Radiobutton(\n                audition,\n                text=mode,\n                value=mode,\n                variable=self.source_filter,\n                command=self._audition_controls_changed,\n            ).pack(side="left", padx=(6 if mode == "OFF" else 2, 0))\n\n        ttk.Label(audition, text="OVERLAY", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(20, 5))\n        ttk.Label(audition, text="Source").pack(side="left")\n        self.source_db_scale = ttk.Scale(\n            audition, from_=-24.0, to=6.0, variable=self.overlay_source_db, command=self._update_overlay_level_labels, length=130\n        )\n        self.source_db_scale.pack(side="left", padx=(4, 3))\n        self.source_db_label = ttk.Label(audition, text="0.0 dB", width=7)\n        self.source_db_label.pack(side="left")\n        ttk.Label(audition, text="Groove").pack(side="left", padx=(10, 0))\n        self.groove_db_scale = ttk.Scale(\n            audition, from_=-24.0, to=6.0, variable=self.overlay_groove_db, command=self._update_overlay_level_labels, length=130\n        )\n        self.groove_db_scale.pack(side="left", padx=(4, 3))\n        self.groove_db_label = ttk.Label(audition, text="0.0 dB", width=7)\n        self.groove_db_label.pack(side="left")\n        self.source_db_scale.bind("<ButtonRelease-1>", self._overlay_slider_release)\n        self.groove_db_scale.bind("<ButtonRelease-1>", self._overlay_slider_release)\n        self._update_overlay_level_labels()\n\n        self.link_notice = ttk.Label(root, text="")\n'''
editor = replace_once(editor, transport_old, transport_new, 'editor audition controls')

play_old = '''    def play(self, kind: str):\n        if self.index is None:\n            return\n        duration = self._duration()\n        if duration <= 0:\n            return\n        if self.play_kind is not None:\n            self.stop_playback(reset=True)\n            start = 0.0\n        else:\n            start = min(max(self.play_position, 0.0), max(0.0, duration - 1e-6))\n        try:\n            self.app.play_preview_for_index(self.index, kind, start_seconds=start)\n        except Exception as exc:\n            messagebox.showerror("TRIAZ Groove Builder", f"Could not play preview:\\n{exc}")\n            return\n        self.play_kind = kind\n        self.play_started_mono = time.monotonic()\n        self.play_started_position = start\n        self.play_position = start\n        self._draw_playhead_only()\n        self._schedule_playhead()\n\n\n'''
play_new = '''    def _update_overlay_level_labels(self, _value=None):\n        if hasattr(self, "source_db_label"):\n            self.source_db_label.config(text=f"{float(self.overlay_source_db.get()):+.1f} dB")\n        if hasattr(self, "groove_db_label"):\n            self.groove_db_label.config(text=f"{float(self.overlay_groove_db.get()):+.1f} dB")\n\n    def _restart_current_audition(self):\n        kind = self.play_kind\n        if kind not in ("timing_overlay", "source_drums"):\n            return\n        self.play_position = self._current_playhead()\n        self.stop_playback(reset=False)\n        self.play(kind)\n\n    def _audition_controls_changed(self):\n        self._restart_current_audition()\n\n    def _overlay_slider_release(self, _event=None):\n        self._update_overlay_level_labels()\n        if self.play_kind == "timing_overlay":\n            self._restart_current_audition()\n\n    def play(self, kind: str):\n        if self.index is None:\n            return\n        duration = self._duration()\n        if duration <= 0:\n            return\n        if self.play_kind is not None:\n            self.stop_playback(reset=True)\n            start = 0.0\n        else:\n            start = min(max(self.play_position, 0.0), max(0.0, duration - 1e-6))\n        try:\n            self.app.play_editor_preview_for_index(\n                self.index,\n                kind,\n                start_seconds=start,\n                source_filter=self.source_filter.get(),\n                source_db=float(self.overlay_source_db.get()),\n                groove_db=float(self.overlay_groove_db.get()),\n            )\n        except Exception as exc:\n            messagebox.showerror("TRIAZ Groove Builder", f"Could not play preview:\\n{exc}")\n            return\n        self.play_kind = kind\n        self.play_started_mono = time.monotonic()\n        self.play_started_position = start\n        self.play_position = start\n        self._draw_playhead_only()\n        self._schedule_playhead()\n\n\n'''
editor = replace_once(editor, play_old, play_new, 'editor play method')

# Clarify in the editor itself that the filter is audition-only.
editor = editor.replace(
    'self.editor_status.config(text="Editor pattern loaded")',
    'self.editor_status.config(text="Editor pattern loaded • LP/BP/HP are audition-only")',
)

write('triaz_groove_builder/editor.py', editor)

# Sanity markers for CI.
combined = app + preview + editor
for marker in (
    'Fuller preview kick',
    'def filter_source_audition',
    'def render_editor_overlay',
    'SOURCE FILTER',
    'overlay_source_db',
    'play_editor_preview_for_index',
):
    if marker not in combined:
        raise RuntimeError(f'Missing v0.1.24 marker: {marker}')

print('v0.1.24 editor preview tools patch applied')
