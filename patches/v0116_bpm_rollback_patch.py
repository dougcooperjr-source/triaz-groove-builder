from __future__ import annotations
from pathlib import Path

ROOT = Path.cwd()

def rw(rel: str):
    p = ROOT / rel
    return p, p.read_text(encoding='utf-8')

def write(rel: str, text: str):
    (ROOT / rel).write_text(text, encoding='utf-8')

def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f'Missing patch anchor: {label}')
    return text.replace(old, new, 1)

# Version strings.
for rel in ('app.py', 'launcher.py', 'installer/triaz_groove_builder.iss'):
    p, s = rw(rel)
    s = s.replace('v0.1.15', 'v0.1.16')
    s = s.replace('APP_VERSION = "0.1.15"', 'APP_VERSION = "0.1.16"')
    s = s.replace('#define MyAppVersion "0.1.15"', '#define MyAppVersion "0.1.16"')
    s = s.replace('TRIAZ_Groove_Builder_Setup_0.1.15.exe', 'TRIAZ_Groove_Builder_Setup_0.1.16.exe')
    write(rel, s)
write('VERSION', '0.1.16\n')

# App UI: manual BPM override with selected-row reanalysis.
p, s = rw('app.py')
s = replace_once(s, '        self.output_names: dict[int, str] = {}\n', '        self.output_names: dict[int, str] = {}\n        self.bpm_overrides: dict[int, int] = {}\n', 'bpm_overrides attr')
s = replace_once(s, '''        self.rename_btn = ttk.Button(action_buttons, text="Rename Output", command=self.rename_selected)
        self.rename_btn.pack(side="left")
        self.build_btn = ttk.Button(action_buttons, text="ANALYZE / CREATE PREVIEWS", command=self.start_build)
''', '''        self.rename_btn = ttk.Button(action_buttons, text="Rename Output", command=self.rename_selected)
        self.rename_btn.pack(side="left")
        self.bpm_btn = ttk.Button(action_buttons, text="Set BPM", command=self.set_bpm_override)
        self.bpm_btn.pack(side="left", padx=(8, 0))
        self.build_btn = ttk.Button(action_buttons, text="ANALYZE / CREATE PREVIEWS", command=self.start_build)
''', 'set bpm button')
s = replace_once(s, '        self.output_names.clear()\n', '        self.output_names.clear()\n        self.bpm_overrides.clear()\n', 'clear bpm overrides')
s = replace_once(s, '        self.rename_btn.state(["disabled"])\n        self.approve_btn.state(["disabled"])', '        self.rename_btn.state(["disabled"])\n        self.bpm_btn.state(["disabled"])\n        self.approve_btn.state(["disabled"])', 'disable bpm full build')
s = replace_once(s, '                    self.rename_btn.state(["!disabled"])\n                    self.eta_label.config(text="ETA: 0:00")', '                    self.rename_btn.state(["!disabled"])\n                    self.bpm_btn.state(["!disabled"])\n                    self.eta_label.config(text="ETA: 0:00")', 'enable bpm done')
rename_block = '''    def rename_selected(self):
        if self.processing:
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select a queued file first.")
            return
        iid = sel[0]
        idx = int(iid)
        draft = self.draft_by_index.get(idx)
        if draft is not None and getattr(draft, "preset", None) is not None:
            messagebox.showinfo(APP_NAME, "This preset has already been exported. Rename before approval, or re-analyze it for a new export name.")
            return
        current = self.output_names.get(idx, self.files[idx].stem)
        value = simpledialog.askstring(
            "Output Name",
            "Preset name (BPM will be appended automatically):",
            initialvalue=current,
            parent=self.root,
        )
        if value is None:
            return
        value = value.strip()
        if not value:
            value = self.files[idx].stem
        self.output_names[idx] = value
        vals = list(self.tree.item(iid, "values"))
        while len(vals) < 5:
            vals.append("")
        vals[3] = value
        self.tree.item(iid, values=vals)

'''
bpm_methods = rename_block + '''    def set_bpm_override(self):
        if self.processing:
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select a queued file first.")
            return
        idx = int(sel[0])
        current = self.bpm_overrides.get(idx)
        if current is None:
            try:
                row_bpm = str(self.tree.set(sel[0], "bpm")).strip().rstrip("*")
                current = int(round(float(row_bpm))) if row_bpm else None
            except Exception:
                current = None
        value = simpledialog.askstring(
            "BPM Override",
            "Enter the correct whole-number BPM. Leave blank to clear the override.",
            initialvalue="" if current is None else str(current),
            parent=self.root,
        )
        if value is None:
            return
        value = value.strip()
        if not value:
            self.bpm_overrides.pop(idx, None)
            vals = list(self.tree.item(sel[0], "values"))
            while len(vals) < 5:
                vals.append("")
            if vals[0] == "Ready":
                vals[1] = ""
                vals[4] = "BPM override cleared"
            self.tree.item(sel[0], values=vals)
            return
        try:
            bpm = int(round(float(value)))
        except Exception:
            messagebox.showerror(APP_NAME, "Enter a valid whole-number BPM, for example 95.")
            return
        if bpm < 40 or bpm > 220:
            messagebox.showerror(APP_NAME, "BPM override must be between 40 and 220.")
            return
        self.bpm_overrides[idx] = bpm
        vals = list(self.tree.item(sel[0], "values"))
        while len(vals) < 5:
            vals.append("")
        vals[1] = f"{bpm}*"
        vals[4] = f"Manual BPM override set to {bpm}. Re-analyze to rebuild timing."
        self.tree.item(sel[0], values=vals)
        draft = self.draft_by_index.get(idx)
        if draft is not None and getattr(draft, "preset", None) is None:
            self._reanalyze_index(idx)

    def _reanalyze_index(self, idx: int):
        if self.processing:
            return
        self._stop_preview()
        self.processing = True
        self.current_index = None
        self.current_started = None
        self.current_progress = 0.0
        self.draft_by_index.pop(idx, None)
        self.template_by_index.pop(idx, None)
        self.preview_by_index.pop(idx, None)
        if self.editor.index == idx:
            self.editor.reset()
        self.build_btn.state(["disabled"])
        self.rename_btn.state(["disabled"])
        self.bpm_btn.state(["disabled"])
        self.approve_btn.state(["disabled"])
        self.eta_label.config(text="ETA: recalculating…")
        sample_source = self.sample_source.get()
        template = TEMPLATES[sample_source]
        threading.Thread(target=self._worker, args=(sample_source, template, [idx]), daemon=True).start()

'''
s = replace_once(s, rename_block, bpm_methods, 'insert BPM methods')
s = replace_once(s, '        threading.Thread(target=self._worker, args=(sample_source, template), daemon=True).start()', '        threading.Thread(target=self._worker, args=(sample_source, template, list(range(len(self.files)))), daemon=True).start()', 'start_build worker args')
s = replace_once(s, '    def _worker(self, sample_source, template):\n        ok = 0\n        failed = 0\n        for i, source in enumerate(self.files):', '    def _worker(self, sample_source, template, indices):\n        ok = 0\n        failed = 0\n        for i in indices:\n            source = self.files[i]', 'worker signature')
s = replace_once(s, '''                    preview_root=PREVIEW_ROOT,
                    defer_export=True,
                )''', '''                    preview_root=PREVIEW_ROOT,
                    defer_export=True,
                    bpm_override=self.bpm_overrides.get(i),
                )''', 'pass bpm override')
s = replace_once(s, '''                align_text = "Source-matched groove — preview / edit / approve"
                self.events.put(("status", i, "Preview Ready", f"{int(round(result.bpm))}", align_text))''', '''                bpm_text = f"{int(round(result.bpm))}" + ("*" if i in self.bpm_overrides else "")
                align_text = "BPM override applied — preview / edit / approve" if i in self.bpm_overrides else "Preview / edit / approve"
                self.events.put(("status", i, "Preview Ready", bpm_text, align_text))''', 'preview ready bpm')
s = replace_once(s, '''            vals[1] = f"{int(round(float(result.bpm)))}"
            prefix = "Edited • " if edited else ""
            vals[4] = f"{prefix}Source-matched groove — preview / edit / approve"''', '''            vals[1] = f"{int(round(float(result.bpm)))}" + ("*" if idx in self.bpm_overrides else "")
            prefix = "Edited • " if edited else ""
            vals[4] = f"{prefix}Preview / edit / approve"''', 'row message')
s = replace_once(s, '''        vals[1] = f"{int(round(float(result.bpm)))}"''', '''        vals[1] = f"{int(round(float(result.bpm)))}" + ("*" if idx in self.bpm_overrides else "")''', 'approved bpm star')
write('app.py', s)

# Engine: manual BPM override + loop-start anchoring.
p, s = rw('triaz_groove_builder/engine.py')
s = replace_once(s, '    fold_four_bars_to_one_pattern, timing_alignment_score, auto_align_pattern,\n', '    fold_four_bars_to_one_pattern, timing_alignment_score, auto_align_pattern,\n    source_transient_times,\n', 'import source_transient_times')
s = replace_once(s, '''    defer_export: bool = True,
) -> BuildResult:''', '''    defer_export: bool = True,
    bpm_override: float | None = None,
) -> BuildResult:''', 'build signature')
s = replace_once(s, '''    source = Path(source)
    named_bpm = _filename_bpm(source)
    if named_bpm is not None:
        named_bpm = float(int(round(named_bpm)))
    duration = get_audio_duration(source)
''', '''    source = Path(source)
    override_bpm = float(int(round(float(bpm_override)))) if bpm_override is not None else None
    named_bpm = override_bpm if override_bpm is not None else _filename_bpm(source)
    if named_bpm is not None:
        named_bpm = float(int(round(named_bpm)))
    duration = get_audio_duration(source)
''', 'bpm override init')
s = replace_once(s, '''    notes = [
        f'Sample source selector: {sample_source} (the selected template kit is used; full library indexing remains a later step).'
    ]

''', '''    notes = [
        f'Sample source selector: {sample_source} (the selected template kit is used; full library indexing remains a later step).'
    ]
    if override_bpm is not None:
        notes.append(f'Manual BPM override used: {int(round(override_bpm))} BPM.')

''', 'bpm override note')
anchor = '''

def _anchor_drum_loop_start(drum: np.ndarray, sr: int, bpm: float) -> tuple[np.ndarray, float]:
    """Rotate the selected drum loop so step 1 starts on a real source hit."""
    drum = np.asarray(drum, dtype=float)
    if drum.size < 2 or sr <= 0:
        return drum, 0.0
    try:
        times, strengths = source_transient_times(drum, sr)
    except Exception:
        return drum, 0.0
    if len(times) == 0:
        return drum, 0.0
    beat = 60.0 / max(float(bpm), 1e-6)
    start_tol = max(0.035, min(0.085, beat * 0.10))
    if np.any(times <= start_tol):
        return drum, 0.0
    window = max(start_tol * 2.0, min(beat, 0.85))
    idx = np.where((times > start_tol) & (times <= window))[0]
    if len(idx) == 0:
        return drum, 0.0
    strongest = int(idx[np.argmax(strengths[idx])])
    shift_seconds = float(times[strongest])
    shift_samples = int(round(shift_seconds * sr))
    if shift_samples <= 0 or shift_samples >= len(drum):
        return drum, 0.0
    return np.concatenate([drum[shift_samples:], drum[:shift_samples]]), shift_seconds
'''
s = s.replace('\n\ndef _filename_bpm', anchor + '\n\ndef _filename_bpm', 1)
s = replace_once(s, '''    _notify(progress, 'Transcribing groove…')
    pat = transcribe_drum_clip(drum, sr, bpm=bpm, bars=bars)
''', '''    drum, loop_shift = _anchor_drum_loop_start(drum, sr, bpm)
    if loop_shift > 1e-6:
        notes.append(f'Adjusted selected source loop start by {loop_shift * 1000.0:.0f} ms so the loop begins on a real drum hit.')

    _notify(progress, 'Transcribing groove…')
    pat = transcribe_drum_clip(drum, sr, bpm=bpm, bars=bars)
''', 'anchor before transcribe')
s = s.replace('Source-matched groove', 'Preview-ready groove')
write('triaz_groove_builder/engine.py', s)

# Groove: keep multi-label visual metadata, but rollback direct high-recall source-event writing.
p, s = rw('triaz_groove_builder/groove.py')
s = replace_once(s, '''    event_lanes, event_hits = _source_events_to_lanes(y, sr, bpm, bars)
    if event_hits:
        for ch, event_lane in event_lanes.items():
            if ch in lanes and len(lanes[ch].velocity) == len(event_lane.velocity):
                lanes[ch].velocity = np.maximum(np.asarray(lanes[ch].velocity, dtype=float), np.asarray(event_lane.velocity, dtype=float))
            else:
                lanes[ch] = event_lane

    active = sum(int(np.count_nonzero(l.velocity)) for l in lanes.values())
    conf = min(1.0, 0.45 + 0.01 * min(active, 50))
    notes = []
    if event_hits:
        notes.append('Source-event transcription: multi-label Kick/Snare-Clap/Hat evidence was merged into the writable TRIAZ grid.')
    return GroovePattern(
''', '''    active = sum(int(np.count_nonzero(l.velocity)) for l in lanes.values())
    conf = min(1.0, 0.45 + 0.01 * min(active, 50))
    notes = []
    return GroovePattern(
''', 'disable direct source-event grid merge')
s = replace_once(s, '''        a, b = v[:32], v[32:]
        # High-recall folding: groove fidelity is the top priority, so preserve the
        # union of both halves instead of requiring recurrence. The one-Pattern-Bank
        # limit means unique bar-3/4 events may also repeat in bars 1/2, but missing
        # a real source hit is worse for this project than an editable extra hit.
        nv = np.maximum(a, b)
        note = "4-bar lane folded with high-recall source-event union (one-pattern constraint)."
        out_lanes[ch] = LanePattern(ch, lane.name, nv, speed=1.0, notes=[note])
        notes.append(f"{lane.name}: {note}")
''', '''        a, b = v[:32], v[32:]
        ah, bh = a > 1e-7, b > 1e-7
        both = ah & bh
        only_a = ah & ~bh
        only_b = bh & ~ah
        nv = np.zeros(32, dtype=float)
        nv[both] = (a[both] + b[both]) * 0.5

        # Preserve a unique event only when it is strong; this avoids turning every
        # four-bar fill into a permanent two-bar hit.
        unique_floor = 0.72 if ch in (0, 2, 3) else 0.82
        nv[only_a & (a >= unique_floor)] = a[only_a & (a >= unique_floor)]
        nv[only_b & (b >= unique_floor)] = np.maximum(nv[only_b & (b >= unique_floor)], b[only_b & (b >= unique_floor)])

        # If folding became implausibly sparse, retain the more active half instead.
        if np.count_nonzero(nv) < max(1, int(0.45 * max(np.count_nonzero(a), np.count_nonzero(b)))):
            nv = a.copy() if np.count_nonzero(a) >= np.count_nonzero(b) else b.copy()
            note = "4-bar lane could not fit exactly; retained the stronger 2-bar half."
        else:
            note = "4-bar lane folded to a representative 2-bar lane (one-pattern constraint)."
        out_lanes[ch] = LanePattern(ch, lane.name, nv, speed=1.0, notes=[note])
        notes.append(f"{lane.name}: {note}")
''', 'restore cautious four-bar fold')
write('triaz_groove_builder/groove.py', s)

print('v0.1.16 BPM override / conversion rollback patch applied')
