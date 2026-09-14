from __future__ import annotations

from pathlib import Path
import re

ROOT = Path.cwd()


def rw(rel: str):
    p = ROOT / rel
    return p, p.read_text(encoding='utf-8')


def write(rel: str, text: str):
    (ROOT / rel).write_text(text, encoding='utf-8')


def replace_top_func(text: str, name: str, new: str) -> str:
    pat = re.compile(rf'^def {re.escape(name)}\(.*?(?=^def |\Z)', re.S | re.M)
    m = pat.search(text)
    if not m:
        raise RuntimeError(f'Missing top-level function: {name}')
    return text[:m.start()] + new.rstrip() + '\n\n' + text[m.end():]


def insert_before_once(text: str, anchor: str, insert: str, label: str) -> str:
    if insert.strip() in text:
        return text
    if anchor not in text:
        raise RuntimeError(f'Missing insert anchor: {label}')
    return text.replace(anchor, insert.rstrip() + '\n\n' + anchor, 1)


def insert_after_once(text: str, anchor: str, insert: str, label: str) -> str:
    if insert.strip() in text:
        return text
    if anchor not in text:
        raise RuntimeError(f'Missing insert-after anchor: {label}')
    return text.replace(anchor, anchor + '\n' + insert.rstrip(), 1)

# Version strings.
for rel in ('app.py', 'launcher.py', 'installer/triaz_groove_builder.iss'):
    p, s = rw(rel)
    for old in ('0.1.18', '0.1.17', '0.1.16', '0.1.15', '0.1.14', '0.1.13', '0.1.12', '0.1.11'):
        s = s.replace(f'v{old}', 'v0.1.19')
        s = s.replace(f'APP_VERSION = "{old}"', 'APP_VERSION = "0.1.19"')
        s = s.replace(f'#define MyAppVersion "{old}"', '#define MyAppVersion "0.1.19"')
        s = s.replace(f'TRIAZ_Groove_Builder_Setup_{old}.exe', 'TRIAZ_Groove_Builder_Setup_0.1.19.exe')
    write(rel, s)
write('VERSION', '0.1.19\n')

# ---------------------------------------------------------------------------
# Engine: preserve the current transcription engine, but make the selected loop
# window an explicit input/output so the UI can move a 4-bar brace and rebuild.
# ---------------------------------------------------------------------------
p, engine = rw('triaz_groove_builder/engine.py')

# Add BuildResult fields used by the loop-brace UI.  They are appended so older
# code that constructs BuildResult positionally is not affected.
if 'source_context: np.ndarray | None = None' not in engine:
    engine = engine.replace(
        '    auto_align_summary: str = ""\n',
        '    auto_align_summary: str = ""\n'
        '    source_context: np.ndarray | None = None\n'
        '    context_start: float = 0.0\n'
        '    loop_start: float = 0.0\n'
        '    loop_duration: float = 0.0\n',
        1,
    )

build_func = r'''
def build_from_audio(
    source: Path,
    output_dir: Path,
    template_path: Path,
    sample_source: str = 'TRIAZ Libraries',
    progress: Callable[[str], None] | None = None,
    output_name: str | None = None,
    preview_root: Path | None = None,
    defer_export: bool = True,
    bpm_override: float | None = None,
    loop_start_override: float | None = None,
) -> BuildResult:
    """Analyze audio and build a preview-ready draft.

    v0.1.19 adds an explicit 4-bar loop brace.  The brace is not an export step;
    it only defines the source region used for preview/editor transcription.  The
    final native TRIAZ preset is still written only by ``approve_and_export``.
    """
    source = Path(source)
    named_bpm = _filename_bpm(source)
    if bpm_override is not None:
        try:
            named_bpm = float(int(round(float(bpm_override))))
        except Exception:
            named_bpm = None
    elif named_bpm is not None:
        named_bpm = float(int(round(named_bpm)))

    duration = get_audio_duration(source)
    explicit_stem = filename_looks_like_drum_stem(source)
    notes = [
        f'Sample source selector: {sample_source} (the selected template kit is used; full library indexing remains a later step).'
    ]
    if loop_start_override is not None:
        notes.append(f'Loop brace override: Bar 1 set to {float(loop_start_override):.3f}s from the source file.')

    context_drums: np.ndarray | None = None
    context_start = 0.0
    loop_start = 0.0
    loop_duration = 0.0

    def clamp_start(value: float, loop_len: float) -> float:
        value = max(0.0, float(value))
        if duration > loop_len:
            return min(value, max(0.0, duration - loop_len))
        return 0.0

    if duration <= 16.0:
        _notify(progress, 'Loading short audio…')
        y, sr = load_audio(source, sr=44100)
        bpm = named_bpm if named_bpm is not None else estimate_bpm(y, sr)
        bpm = float(int(round(bpm)))
        dur = len(y) / sr

        if not named_bpm and (explicit_stem or looks_like_drum_stem(source, y, sr)) and dur < 14.0:
            candidates = []
            for bars_guess in (2, 4):
                b = bars_guess * 4 * 60.0 / max(dur, 1e-9)
                if 65.0 <= b <= 180.0:
                    candidates.append((abs(b - round(b)), abs(b - 100.0), b))
            if candidates:
                candidates.sort()
                bpm = float(int(round(candidates[0][2])))

        bpm = float(int(round(bpm)))
        two_bar = 8 * 60.0 / bpm
        four_bar = 16 * 60.0 / bpm
        loop_duration = four_bar
        stem_like = explicit_stem or looks_like_drum_stem(source, y, sr)

        if loop_start_override is not None:
            start = clamp_start(float(loop_start_override), four_bar)
            clip_len = min(len(y) - int(round(start * sr)), int(round(four_bar * sr)))
            i0 = max(0, int(round(start * sr)))
            i1 = min(len(y), i0 + max(1, clip_len))
            clip = y[i0:i1]
            bars = 4
            if stem_like:
                drum = clip
                context_drums = y
                notes.append('Used manual Bar 1 override on the supplied short drum source.')
            else:
                _notify(progress, 'Separating selected 4 bars…')
                with tempfile.TemporaryDirectory(prefix='triaz_groove_') as td:
                    import soundfile as sf
                    candidate = Path(td) / 'candidate.wav'
                    sf.write(candidate, clip, sr)
                    drums_path = separate_drums_demucs(candidate, Path(td))
                    drum, _ = load_audio(drums_path, sr=sr)
                context_drums = drum
                context_start = start
                notes.append('Separated the manually braced short 4-bar clip with Demucs.')
            loop_start = start
        elif stem_like and abs(dur - two_bar) <= max(0.18, two_bar * 0.04):
            drum, bars, start = y, 2, 0.0
            loop_start, loop_duration = 0.0, len(drum) / sr
            context_drums = y
            notes.append('Used supplied 2-bar drum stem directly.')
        elif stem_like and abs(dur - four_bar) <= max(0.25, four_bar * 0.04):
            drum, bars, start = y, 4, 0.0
            loop_start, loop_duration = 0.0, len(drum) / sr
            context_drums = y
            notes.append('Used supplied 4-bar drum stem directly.')
        else:
            bars, start = 4, 0.0
            clip_len = min(len(y), int(round(four_bar * sr)))
            clip = y[:clip_len]
            loop_start, loop_duration = start, len(clip) / sr
            if stem_like:
                drum = clip
                context_drums = y
                notes.append('Used the first representative 4 bars from a short drum stem.')
            else:
                _notify(progress, 'Separating selected 4 bars…')
                with tempfile.TemporaryDirectory(prefix='triaz_groove_') as td:
                    import soundfile as sf
                    candidate = Path(td) / 'candidate.wav'
                    sf.write(candidate, clip, sr)
                    drums_path = separate_drums_demucs(candidate, Path(td))
                    drum, _ = load_audio(drums_path, sr=sr)
                context_drums = drum
                notes.append('Separated only the short 4-bar clip with Demucs.')
    else:
        _notify(progress, 'Fast song scan…')
        scan_sr = 11025
        scan, scan_sr = load_audio(source, sr=scan_sr)
        bpm = named_bpm if named_bpm is not None else estimate_bpm(scan, scan_sr)
        bpm = float(int(round(bpm)))

        four_bar = 16 * 60.0 / bpm
        bar_len = 4 * 60.0 / bpm
        loop_duration = four_bar

        if loop_start_override is None:
            _notify(progress, 'Choosing representative 4 bars…')
            _, start = _candidate_four_bar_clip(scan, scan_sr, bpm)
        else:
            _notify(progress, 'Using manual Bar 1 override…')
            start = clamp_start(float(loop_start_override), four_bar)
        loop_start = float(start)

        # Load a wider context so the UI can show a 4-bar brace inside nearby audio.
        # This is still bounded to the selected region, not a full-song Demucs pass.
        context_bars = 8.0
        context_len = min(float(duration), context_bars * bar_len)
        context_start = max(0.0, float(start) - 2.0 * bar_len)
        if context_start + context_len > duration:
            context_start = max(0.0, duration - context_len)
        context_rel = max(0.0, float(start) - context_start)

        _notify(progress, 'Loading loop context…')
        context_clip, sr = load_audio(source, sr=44100, offset=context_start, duration=context_len)
        bars = 4

        if explicit_stem:
            context_drums = context_clip
            notes.append('Low-resolution full-file scan; loaded an 8-bar drum context at 44.1 kHz for loop-brace selection.')
        else:
            _notify(progress, 'Separating loop context…')
            with tempfile.TemporaryDirectory(prefix='triaz_groove_') as td:
                import soundfile as sf
                candidate = Path(td) / 'candidate.wav'
                sf.write(candidate, context_clip, sr)
                drums_path = separate_drums_demucs(candidate, Path(td))
                context_drums, _ = load_audio(drums_path, sr=sr)
            notes.append('Scanned the whole song at 11.025 kHz, then separated only the braced context with Demucs.')

        i0 = max(0, min(len(context_drums) - 1, int(round(context_rel * sr))))
        i1 = min(len(context_drums), i0 + int(round(four_bar * sr)))
        drum = context_drums[i0:i1]
        if len(drum) < max(64, int(round(0.50 * four_bar * sr))):
            # Safety fallback near file edges.
            clip, sr = load_audio(source, sr=44100, offset=start, duration=four_bar)
            if explicit_stem:
                drum = clip
            else:
                with tempfile.TemporaryDirectory(prefix='triaz_groove_') as td:
                    import soundfile as sf
                    candidate = Path(td) / 'candidate.wav'
                    sf.write(candidate, clip, sr)
                    drums_path = separate_drums_demucs(candidate, Path(td))
                    drum, _ = load_audio(drums_path, sr=sr)
            context_drums = drum
            context_start = start

    _notify(progress, 'Transcribing groove…')
    pat = transcribe_drum_clip(drum, sr, bpm=bpm, bars=bars)
    pat.selected_start = float(loop_start)
    pat.selected_end = float(loop_start) + len(drum) / sr
    pat.notes.extend(notes)

    _notify(progress, 'Auto-aligning hits to source drums…')
    aligned_pat, align_stats = auto_align_pattern(drum, sr, pat, allow_add_remove=True)
    if align_stats.accepted:
        pat = aligned_pat
    auto_align_summary = (
        f'Auto Align: {align_stats.moved} moved, {align_stats.added} added, '
        f'{align_stats.removed} removed; timing {align_stats.before * 100.0:.0f}% → '
        f'{align_stats.after * 100.0:.0f}%.'
    )
    pat.notes.append(auto_align_summary)

    if bars == 4:
        pat = fold_four_bars_to_one_pattern(pat)

    _notify(progress, 'Verifying corrected timing…')
    alignment = timing_alignment_score(drum, sr, pat)
    pat.notes.append(f'Corrected timing-overlay alignment score: {alignment * 100.0:.1f}%.')

    safe = _safe_output_name(source, output_name, bpm)
    output_path = Path(output_dir) / f'{safe} [{round(bpm):d} BPM].preset'

    preview = None
    if preview_root is not None:
        _notify(progress, 'Creating audio preview…')
        preview = create_preview_assets(
            source=source,
            source_drums=drum,
            sr=sr,
            pattern=pat,
            preview_root=Path(preview_root),
            output_name=safe,
        )

    result = BuildResult(
        source=source,
        output_path=output_path,
        bpm=bpm,
        confidence=pat.confidence,
        alignment=alignment,
        notes=pat.notes,
        pattern=pat,
        preview=preview,
        preset=None,
        source_drums=np.asarray(drum, dtype=np.float32).copy(),
        sample_rate=int(sr),
        auto_align_summary=auto_align_summary,
        source_context=np.asarray(context_drums if context_drums is not None else drum, dtype=np.float32).copy(),
        context_start=float(context_start),
        loop_start=float(loop_start),
        loop_duration=float(loop_duration if loop_duration > 0 else len(drum) / sr),
    )

    if not defer_export:
        approve_and_export(result, template_path)
    return result
'''
engine = replace_top_func(engine, 'build_from_audio', build_func)
write('triaz_groove_builder/engine.py', engine)

# ---------------------------------------------------------------------------
# App: add the visible loop-selection/brace UI and one-file rebuild from brace.
# ---------------------------------------------------------------------------
p, app = rw('app.py')
if 'import math\n' not in app:
    app = app.replace('import json\n', 'import json\nimport math\n', 1)

# Remember per-file brace overrides.
if 'self.loop_brace_overrides' not in app:
    anchor = '        self.output_names: dict[int, str] = {}\n'
    app = app.replace(anchor, anchor + '        self.loop_brace_overrides: dict[int, float] = {}\n        self.loop_dragging = False\n', 1)

# Clear loop overrides.
if 'self.loop_brace_overrides.clear()' not in app:
    app = app.replace('        self.output_names.clear()\n', '        self.output_names.clear()\n        self.loop_brace_overrides.clear()\n', 1)

# Add Loop Selection panel right below Audio Preview note.
loop_ui = r'''
        loopbox = ttk.LabelFrame(outer, text="Loop Selection / Bar 1 Override", padding=10)
        loopbox.pack(fill="x", pady=(0, 10))
        self.loop_title = ttk.Label(
            loopbox,
            text="Analyze a row to view the suggested source context and 4-bar brace.",
            font=("Segoe UI", 9, "bold"),
        )
        self.loop_title.pack(anchor="w")
        self.loop_canvas = tk.Canvas(loopbox, height=105, background="#1d1f21", highlightthickness=1, highlightbackground="#414346")
        self.loop_canvas.pack(fill="x", pady=(8, 6))
        self.loop_canvas.bind("<ButtonPress-1>", self._loop_canvas_press)
        self.loop_canvas.bind("<B1-Motion>", self._loop_canvas_drag)
        self.loop_canvas.bind("<ButtonRelease-1>", self._loop_canvas_release)
        self.loop_canvas.bind("<Configure>", lambda _e: self._draw_loop_selection())
        loop_buttons = ttk.Frame(loopbox)
        loop_buttons.pack(fill="x")
        self.loop_left_btn = ttk.Button(loop_buttons, text="◀ Move 1 Bar", command=lambda: self._move_brace_bars(-1))
        self.loop_left_btn.pack(side="left")
        self.loop_right_btn = ttk.Button(loop_buttons, text="Move 1 Bar ▶", command=lambda: self._move_brace_bars(1))
        self.loop_right_btn.pack(side="left", padx=(8, 0))
        self.set_bar1_btn = ttk.Button(loop_buttons, text="Set Bar 1…", command=self._set_bar1_dialog)
        self.set_bar1_btn.pack(side="left", padx=(8, 0))
        self.rebuild_brace_btn = ttk.Button(loop_buttons, text="REBUILD PREVIEW FROM BRACE", command=self.rebuild_selected_from_brace)
        self.rebuild_brace_btn.pack(side="left", padx=(8, 0))
        self.loop_hint = ttk.Label(loop_buttons, text="Drag/click the brace, then rebuild. No preset is exported until Approve & Export.")
        self.loop_hint.pack(side="left", padx=(14, 0))
        self._set_loop_buttons(False)
'''
if 'self.loop_canvas = tk.Canvas' not in app:
    app = app.replace('        self.preview_note.pack(anchor="w")\n        self._set_preview_buttons(False)', '        self.preview_note.pack(anchor="w")\n' + loop_ui + '\n        self._set_preview_buttons(False)', 1)

# Add loop methods before _remember_source.
loop_methods = r'''
    def _set_loop_buttons(self, enabled: bool):
        state = ["!disabled"] if enabled else ["disabled"]
        for btn in (getattr(self, "loop_left_btn", None), getattr(self, "loop_right_btn", None), getattr(self, "set_bar1_btn", None), getattr(self, "rebuild_brace_btn", None)):
            if btn is not None:
                btn.state(state)

    def _selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _loop_info(self, idx: int):
        result = self.draft_by_index.get(idx)
        if result is None:
            return None
        y = getattr(result, "source_context", None)
        sr = int(getattr(result, "sample_rate", 0) or 0)
        if y is None or sr <= 0:
            y = getattr(result, "source_drums", None)
        if y is None or sr <= 0:
            return None
        import numpy as _np
        y = _np.asarray(y, dtype=float)
        if y.size < 2:
            return None
        context_start = float(getattr(result, "context_start", 0.0) or 0.0)
        loop_start = float(self.loop_brace_overrides.get(idx, getattr(result, "loop_start", getattr(result.pattern, "selected_start", 0.0)) or 0.0))
        loop_duration = float(getattr(result, "loop_duration", 0.0) or 0.0)
        if loop_duration <= 0.0:
            loop_duration = len(getattr(result, "source_drums", y)) / float(sr)
        bpm = float(getattr(result, "bpm", 120.0) or 120.0)
        return result, y, sr, context_start, loop_start, loop_duration, bpm

    def _loop_bounds(self):
        width = max(300, int(self.loop_canvas.winfo_width() or 980))
        return 16, width - 16

    def _loop_time_to_x(self, rel_t: float, duration: float) -> float:
        x0, x1 = self._loop_bounds()
        return x0 + (x1 - x0) * (float(rel_t) / max(float(duration), 1e-9))

    def _loop_x_to_rel_time(self, x: float, duration: float) -> float:
        x0, x1 = self._loop_bounds()
        f = (float(x) - x0) / max(x1 - x0, 1e-9)
        return min(max(float(duration), 0.0), max(0.0, f * float(duration)))

    def _set_brace_from_x(self, idx: int, x: float):
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

    def _loop_canvas_press(self, event):
        idx = self._selected_index()
        if idx is None or idx not in self.draft_by_index:
            return
        self.loop_dragging = True
        self._set_brace_from_x(idx, event.x)

    def _loop_canvas_drag(self, event):
        idx = self._selected_index()
        if not self.loop_dragging or idx is None:
            return
        self._set_brace_from_x(idx, event.x)

    def _loop_canvas_release(self, _event):
        self.loop_dragging = False

    def _move_brace_bars(self, bars_delta: int):
        idx = self._selected_index()
        if idx is None:
            return
        info = self._loop_info(idx)
        if info is None:
            return
        result, y, sr, context_start, loop_start, loop_duration, bpm = info
        context_duration = len(y) / float(sr)
        bar_len = 4.0 * 60.0 / max(bpm, 1e-6)
        rel = (loop_start - context_start) + float(bars_delta) * bar_len
        rel = min(max(0.0, rel), max(0.0, context_duration - loop_duration))
        self.loop_brace_overrides[idx] = context_start + rel
        self._draw_loop_selection()

    def _set_bar1_dialog(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo(APP_NAME, "Select an analyzed row first.")
            return
        info = self._loop_info(idx)
        if info is None:
            messagebox.showinfo(APP_NAME, "Analyze this row first so a loop brace is available.")
            return
        result, y, sr, context_start, loop_start, loop_duration, bpm = info
        value = simpledialog.askfloat(
            "Set Bar 1",
            "Bar 1 start time in seconds from the beginning of the source file:",
            initialvalue=round(float(loop_start), 3),
            minvalue=0.0,
            parent=self.root,
        )
        if value is None:
            return
        self.loop_brace_overrides[idx] = max(0.0, float(value))
        self._draw_loop_selection()

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
        context_duration = len(y) / float(sr)
        x0, x1 = self._loop_bounds()
        top, mid, bottom = 20, 52, 88
        c.create_rectangle(x0, top, x1, bottom, fill="#151719", outline="#414346")
        if y.size:
            bins = max(180, int(x1 - x0))
            import numpy as _np
            edges = _np.linspace(0, len(y), bins + 1, dtype=int)
            peak = max(float(_np.max(_np.abs(y))), 1e-9)
            for i in range(bins):
                seg = y[edges[i]:edges[i+1]]
                amp = float(_np.max(_np.abs(seg))) / peak if len(seg) else 0.0
                x = x0 + (x1 - x0) * (i / max(1, bins - 1))
                c.create_line(x, mid - amp * 31.0, x, mid + amp * 31.0, fill="#8f98a0")
        bar_len = 4.0 * 60.0 / max(bpm, 1e-6)
        bars = int(math.floor(context_duration / max(bar_len, 1e-9)))
        for b in range(bars + 1):
            t = b * bar_len
            x = self._loop_time_to_x(t, context_duration)
            c.create_line(x, top, x, bottom, fill="#34373a" if b % 4 else "#60656b", width=1 if b % 4 else 2)
            c.create_text(x + 3, top + 2, anchor="nw", fill="#9aa0a6", text=str(b + 1), font=("Segoe UI", 7))
        brace_rel = min(max(0.0, loop_start - context_start), max(0.0, context_duration - loop_duration))
        bx0 = self._loop_time_to_x(brace_rel, context_duration)
        bx1 = self._loop_time_to_x(min(context_duration, brace_rel + loop_duration), context_duration)
        c.create_rectangle(bx0, top - 1, bx1, bottom + 1, outline="#f0b84b", width=3)
        c.create_rectangle(bx0, top - 1, bx0 + 8, bottom + 1, fill="#f0b84b", outline="")
        c.create_rectangle(bx1 - 8, top - 1, bx1, bottom + 1, fill="#f0b84b", outline="")
        name = self.output_names.get(idx, self.files[idx].stem)
        pending = " pending rebuild" if idx in self.loop_brace_overrides and abs(float(self.loop_brace_overrides[idx]) - float(getattr(result, "loop_start", 0.0) or 0.0)) > 1e-4 else ""
        self.loop_title.config(
            text=f"{name} • context {context_start:.2f}s–{context_start + context_duration:.2f}s • 4-bar brace starts {loop_start:.3f}s{pending}"
        )

    def rebuild_selected_from_brace(self):
        if self.processing:
            return
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo(APP_NAME, "Select an analyzed row first.")
            return
        if idx not in self.loop_brace_overrides:
            info = self._loop_info(idx)
            if info is None:
                messagebox.showinfo(APP_NAME, "Analyze this row first so a loop brace is available.")
                return
            self.loop_brace_overrides[idx] = float(info[4])
        self._stop_preview()
        self.processing = True
        self.build_btn.state(["disabled"])
        self.rename_btn.state(["disabled"])
        self.approve_btn.state(["disabled"])
        if hasattr(self, "edit_btn"):
            self.edit_btn.state(["disabled"])
        self._set_preview_buttons(False)
        self._set_loop_buttons(False)
        self.eta_label.config(text="ETA: calculating…")
        sample_source = self.sample_source.get()
        template = TEMPLATES[sample_source]
        threading.Thread(target=self._single_worker, args=(idx, sample_source, template), daemon=True).start()

    def _single_worker(self, i: int, sample_source, template):
        source = self.files[i]
        started = time.monotonic()
        self.events.put(("file_start", i, started))
        self.events.put(("status", i, "Rebuilding from loop brace…", "", ""))
        try:
            outdir = self.output_override if self.output_override else source.parent / "TRIAZ Presets"
            def progress(stage):
                self.events.put(("status", i, stage, "", ""))
            result = build_from_audio(
                source, outdir, template, sample_source, progress=progress,
                output_name=self.output_names.get(i, source.stem),
                preview_root=PREVIEW_ROOT,
                defer_export=True,
                bpm_override=self.bpm_overrides.get(i) if hasattr(self, "bpm_overrides") else None,
                loop_start_override=self.loop_brace_overrides.get(i),
            )
            self.events.put(("draft", i, result, template))
            if result.preview is not None:
                self.events.put(("preview", i, result.preview.as_dict()))
            bpm_text = f"{int(round(result.bpm))}" + ("*" if hasattr(self, "bpm_overrides") and i in self.bpm_overrides else "")
            self.events.put(("status", i, "Preview Ready", bpm_text, f"Loop brace {float(result.loop_start):.3f}s — preview / edit / approve"))
        except Exception as exc:
            msg = str(exc).splitlines()[-1] if str(exc) else exc.__class__.__name__
            self.events.put(("status", i, "Needs Review", "", msg[:180]))
            with (ROOT / "triaz_groove_builder.log").open("a", encoding="utf-8") as fh:
                fh.write(f"\n\n=== {source} ===\n{traceback.format_exc()}")
        finally:
            self.events.put(("file_end", i, max(0.01, time.monotonic() - started)))
            self.events.put(("done", 1, 0))
'''
if 'def _draw_loop_selection(self):' not in app:
    app = insert_before_once(app, '    def _remember_source(self):', loop_methods, 'loop methods before _remember_source')

# Hook tree selection and clear to update the loop brace canvas.
if 'self._draw_loop_selection()\n            self.approve_btn.state(["disabled"])' not in app:
    app = app.replace('            self._set_preview_buttons(False)\n            self.approve_btn.state(["disabled"])', '            self._set_preview_buttons(False)\n            self._draw_loop_selection()\n            self.approve_btn.state(["disabled"])', 1)
if 'self._draw_loop_selection()\n\n    def _play_preview' not in app:
    app = app.replace('            self._set_preview_buttons(False)\n\n    def _play_preview', '            self._set_preview_buttons(False)\n        self._draw_loop_selection()\n\n    def _play_preview', 1)

# Keep loop buttons/canvas in sync on clear.
try:
    clear_block = app.split('def clear_files',1)[1].split('def _double_click_tree',1)[0]
except Exception:
    clear_block = ''
if 'self._draw_loop_selection()' not in clear_block:
    app = app.replace('        self._set_preview_buttons(False)\n\n    def _double_click_tree', '        self._set_preview_buttons(False)\n        self._draw_loop_selection()\n\n    def _double_click_tree', 1)

# Pass loop override through the normal bulk worker too.  Preserve v0.1.16 BPM override when present.
if 'loop_start_override=self.loop_brace_overrides.get(i)' not in app:
    if 'bpm_override=self.bpm_overrides.get(i)' in app:
        app = app.replace('                    bpm_override=self.bpm_overrides.get(i),\n', '                    bpm_override=self.bpm_overrides.get(i),\n                    loop_start_override=self.loop_brace_overrides.get(i),\n', 1)
    else:
        app = app.replace('                    defer_export=True,\n', '                    defer_export=True,\n                    loop_start_override=self.loop_brace_overrides.get(i),\n', 1)

# Stage fraction for context load/rebuild.
if '("Loading loop context", 0.30),' not in app:
    app = app.replace('            ("Loading selected", 0.27),\n', '            ("Loading selected", 0.27),\n            ("Loading loop context", 0.30),\n            ("Using manual Bar 1 override", 0.22),\n            ("Rebuilding from loop brace", 0.05),\n', 1)

write('app.py', app)

print('v0.1.19 loop brace / Set Bar 1 patch applied')
