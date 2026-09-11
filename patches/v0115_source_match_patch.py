from __future__ import annotations

from pathlib import Path
import re

ROOT = Path.cwd()

def rw(rel: str):
    p = ROOT / rel
    return p, p.read_text(encoding="utf-8")

def write(rel: str, text: str):
    (ROOT / rel).write_text(text, encoding="utf-8")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Missing patch anchor: {label}")
    return text.replace(old, new, 1)

def replace_func(text: str, name: str, new: str) -> str:
    start = text.index(f"    def {name}")
    m = re.search(r"\n    def [A-Za-z_]", text[start + 1:])
    end = len(text) if not m else start + 1 + m.start()
    return text[:start] + new + text[end:]

p, s = rw("app.py")
s = s.replace('root.title(f"{APP_NAME} v0.1.14")', 'root.title(f"{APP_NAME} v0.1.15")')
s = s.replace('vals[4] = f"{prefix}Timing alignment {float(result.alignment) * 100.0:.0f}% — preview / edit / approve"', 'vals[4] = f"{prefix}Source-matched groove — preview / edit / approve"')
s = s.replace('align_text = f"{result.auto_align_summary} Final alignment {result.alignment * 100.0:.0f}% — preview / edit / approve"', 'align_text = "Source-matched groove — preview / edit / approve"')
write("app.py", s)

p, s = rw("launcher.py")
s = s.replace('APP_VERSION = "0.1.14"', 'APP_VERSION = "0.1.15"')
write("launcher.py", s)

p, s = rw("installer/triaz_groove_builder.iss")
s = s.replace('#define MyAppVersion "0.1.14"', '#define MyAppVersion "0.1.15"')
s = s.replace('TRIAZ_Groove_Builder_Setup_0.1.14.exe', 'TRIAZ_Groove_Builder_Setup_0.1.15.exe')
write("installer/triaz_groove_builder.iss", s)
write("VERSION", "0.1.15\n")

p, s = rw("triaz_groove_builder/groove.py")
insert = r'''

def source_transient_layers(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return source transient times, strengths, and multi-label family weights.

    Family columns are ordered as: kick, snare/clap, hat/cymbal, other/percussion.
    The pass reuses the same selected drum audio/transients rather than running a
    separate slow model per drum type. Multiple families may be active at one
    timestamp so layered hits can drive both display and transcription.
    """
    y = np.asarray(y, dtype=float)
    times, strengths = source_transient_times(y, sr)
    if len(times) == 0:
        return times, strengths, np.zeros((0, 4), dtype=float)

    ft, envs = _band_flux(y, sr, hop=128 if sr >= 32000 else 64, n_fft=2048)
    low, lowmid, mid, high, vhigh = (envs[k] for k in ('low', 'lowmid', 'mid', 'high', 'vhigh'))
    raw = np.zeros((len(times), 4), dtype=float)
    for i, t in enumerate(times):
        if not len(ft):
            continue
        j = int(np.argmin(np.abs(ft - float(t))))
        lo = float(low[j]) if len(low) else 0.0
        lm = float(lowmid[j]) if len(lowmid) else 0.0
        md = float(mid[j]) if len(mid) else 0.0
        hi = float(high[j]) if len(high) else 0.0
        vh = float(vhigh[j]) if len(vhigh) else 0.0
        raw[i] = [
            0.70 * lo + 0.24 * lm + 0.08 * md - 0.12 * hi,
            0.42 * md + 0.34 * hi + 0.18 * lm + 0.06 * vh - 0.18 * lo,
            0.48 * hi + 0.42 * vh + 0.10 * md - 0.06 * lo,
            0.35 * lm + 0.32 * md + 0.22 * hi + 0.11 * vh,
        ]

    layers = np.zeros_like(raw)
    for col in range(raw.shape[1]):
        layers[:, col] = _robust_scale(raw[:, col], 0.03, 0.96)
    active = np.zeros_like(layers)
    for i in range(len(times)):
        k, sc, h, o = layers[i]
        st = float(strengths[i]) if i < len(strengths) else 0.0
        if k >= 0.26 and (raw[i, 0] >= 0.06 or k >= max(sc, h) * 0.72):
            active[i, 0] = k
        if sc >= 0.22 and raw[i, 1] >= 0.04:
            active[i, 1] = sc
        if h >= 0.16 and raw[i, 2] >= 0.035:
            active[i, 2] = h
        if o >= 0.55 and st >= 0.35 and not np.any(active[i, :3] > 0):
            active[i, 3] = o
        if not np.any(active[i] > 0):
            active[i, int(np.argmax(layers[i]))] = max(float(np.max(layers[i])), st, 0.15)
    return times, strengths, active


def _source_events_to_lanes(y: np.ndarray, sr: int, bpm: float, bars: int) -> tuple[Dict[int, LanePattern], int]:
    """High-recall on-grid transcription from classified source events."""
    times, strengths, layers = source_transient_layers(y, sr)
    n_steps = int(bars) * 16
    if n_steps <= 0 or len(times) == 0:
        return {}, 0
    base_step = 60.0 / max(float(bpm), 1e-6) / 4.0
    duration = len(y) / float(sr) if sr else n_steps * base_step
    lanes = {
        0: LanePattern(0, "KICK", np.zeros(n_steps, dtype=float)),
        2: LanePattern(2, "SNARE", np.zeros(n_steps, dtype=float)),
        3: LanePattern(3, "CLAP", np.zeros(n_steps, dtype=float)),
        6: LanePattern(6, "HIHAT CL", np.zeros(n_steps, dtype=float)),
    }

    sc_phase_scores = np.zeros(8, dtype=float)
    for t, st, row in zip(times, strengths, layers):
        step = int(round(float(t) / max(base_step, 1e-9)))
        if 0 <= step < n_steps:
            sc_phase_scores[step % 8] += float(row[1]) * (0.40 + float(st))
    backbeat_phase = int(np.argmax(sc_phase_scores)) if float(np.max(sc_phase_scores)) > 0 else 4

    written = 0
    for t, st, row in zip(times, strengths, layers):
        if t < -0.020 or t > duration + 0.020:
            continue
        step = int(round(float(t) / max(base_step, 1e-9)))
        if not (0 <= step < n_steps):
            continue
        grid_t = step * base_step
        if abs(float(t) - grid_t) > max(0.050, base_step * 0.46):
            continue
        k, sc, h, _o = [float(x) for x in row]
        base_vel = 0.45 + 0.50 * float(np.clip(st, 0.0, 1.0))
        if k > 0:
            lanes[0].velocity[step] = max(float(lanes[0].velocity[step]), np.clip(base_vel * (0.72 + 0.35 * k), 0.50, 1.0))
            written += 1
        on_backbeat = (step % 8) == backbeat_phase
        if sc > 0 and (on_backbeat or sc >= 0.76):
            vel = float(np.clip(base_vel * (0.70 + 0.34 * sc), 0.48, 1.0))
            lanes[2].velocity[step] = max(float(lanes[2].velocity[step]), vel)
            if on_backbeat or sc >= 0.76:
                lanes[3].velocity[step] = max(float(lanes[3].velocity[step]), min(1.0, vel * 0.96))
            written += 1
        if h > 0:
            lanes[6].velocity[step] = max(float(lanes[6].velocity[step]), np.clip(base_vel * (0.66 + 0.28 * h), 0.42, 0.92))
            written += 1

    grid, f = _step_features(y, sr, bpm, bars)
    high, vhigh, mid, low = (f[k] for k in ("high", "vhigh", "mid", "low"))
    hat_score = 0.50 * high + 0.40 * vhigh + 0.10 * mid - 0.06 * low
    if len(hat_score) == n_steps and float(np.max(hat_score)) > 0:
        floor = max(0.16, float(np.quantile(hat_score, 0.35)))
        for step, hs in enumerate(hat_score):
            if hs >= floor:
                lanes[6].velocity[step] = max(float(lanes[6].velocity[step]), float(np.clip(0.42 + 0.45 * hs, 0.42, 0.90)))

    if not any(float(l.velocity[0]) > 1e-7 for l in lanes.values()):
        near = np.where(np.abs(times - 0.0) <= max(0.075, base_step * 0.55))[0]
        if len(near):
            idx = int(near[np.argmax(strengths[near])])
            fam = int(np.argmax(layers[idx, :3]))
            vel = float(np.clip(0.55 + 0.40 * strengths[idx], 0.55, 0.98))
            if fam == 0:
                lanes[0].velocity[0] = vel
            elif fam == 1:
                lanes[2].velocity[0] = vel
            else:
                lanes[6].velocity[0] = min(0.90, vel)
            written += 1

    lanes = {ch: lane for ch, lane in lanes.items() if np.count_nonzero(lane.velocity) > 0}
    return lanes, written
'''
s = replace_once(s, '\ndef transcribe_drum_clip(', insert + '\ndef transcribe_drum_clip(', 'insert source-event functions')
old = '''    lanes = {
        0: LanePattern(0, "KICK", kick_vel),
        2: LanePattern(2, "SNARE", snare_vel),
        3: LanePattern(3, "CLAP", clap_vel),
        6: LanePattern(6, "HIHAT CL", hat_vel),
    }

    active = sum(int(np.count_nonzero(l.velocity)) for l in lanes.values())
    conf = min(1.0, 0.45 + 0.01 * min(active, 50))
    return GroovePattern(
        bpm=float(bpm),
        source_bars=bars,
        selected_start=0.0,
        selected_end=float(bars * 4 * 60.0 / bpm),
        lanes=lanes,
        confidence=conf,
    )
'''
new = '''    lanes = {
        0: LanePattern(0, "KICK", kick_vel),
        2: LanePattern(2, "SNARE", snare_vel),
        3: LanePattern(3, "CLAP", clap_vel),
        6: LanePattern(6, "HIHAT CL", hat_vel),
    }

    event_lanes, event_hits = _source_events_to_lanes(y, sr, bpm, bars)
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
        bpm=float(bpm),
        source_bars=bars,
        selected_start=0.0,
        selected_end=float(bars * 4 * 60.0 / bpm),
        lanes=lanes,
        confidence=conf,
        notes=notes,
    )
'''
s = replace_once(s, old, new, 'merge source-event lanes')
old = '''        a, b = v[:32], v[32:]
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
'''
new = '''        a, b = v[:32], v[32:]
        nv = np.maximum(a, b)
        note = "4-bar lane folded with high-recall source-event union (one-pattern constraint)."
'''
s = replace_once(s, old, new, 'high-recall fold')
s = s.replace('''        bpm=pattern.bpm,
        source_bars=4,
        source_bars=4,
''', '''        bpm=pattern.bpm,
        source_bars=4,
''')
start = s.index('def source_transient_families(')
end = s.index('\ndef _lane_transient_candidates', start)
wrapper = '''def source_transient_families(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return dominant source family labels for legacy visual callers."""
    times, strengths, layers = source_transient_layers(y, sr)
    if len(times) == 0:
        return times, strengths, np.zeros(0, dtype=int)
    family_ids = np.array([0, 2, 6, 8], dtype=int)
    fam = family_ids[np.argmax(layers, axis=1)]
    return times, strengths, fam

'''
s = s[:start] + wrapper + s[end+1:]
write("triaz_groove_builder/groove.py", s)

p, s = rw("triaz_groove_builder/editor.py")
s = s.replace('source_transient_families,', 'source_transient_layers,')
start = s.index('        toolbar = ttk.Frame(root)')
end = s.index('        transport = ttk.Frame(root)', start)
s = s[:start] + '''        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", pady=(0, 7))
        self.undo_btn = ttk.Button(toolbar, text="Undo", command=self.undo)
        self.undo_btn.pack(side="left")
        self.accept_btn = ttk.Button(toolbar, text="ACCEPT CHANGES", command=self.accept_changes)
        self.accept_btn.pack(side="left", padx=(8, 0))
        self.editor_status = ttk.Label(toolbar, text="")
        self.editor_status.pack(side="right")

''' + s[end:]
s = s.replace('''        self.canvas.bind("<Delete>", self._delete_selected_event)
        self.canvas.bind("<BackSpace>", self._delete_selected_event)
''', '''        self.canvas.bind("<Delete>", self._delete_selected_event)
        self.canvas.bind("<BackSpace>", self._delete_selected_event)
        self.canvas.bind("<Up>", lambda e: self._nudge_velocity(+0.04))
        self.canvas.bind("<Down>", lambda e: self._nudge_velocity(-0.04))
''')
s = replace_func(s, '_set_enabled', '''    def _set_enabled(self, enabled: bool):
        state = ["!disabled"] if enabled else ["disabled"]
        self.undo_btn.state(state if self.undo_stack else ["disabled"])
        self.accept_btn.state(state)
        self.overlay_btn.state(state)

''')
s = s.replace('''        if stats.accepted:
            self.alignment.config(
                text=f"Auto Align: {stats.moved} moved • {stats.added} added • {stats.removed} removed • {stats.after*100:.0f}%"
            )
        else:
            self.alignment.config(text=f"Auto Align kept the existing groove • {stats.after*100:.0f}%")
''', '''        self.editor_status.config(text=f"Auto Align refreshed: {stats.moved} moved • {stats.added} added • {stats.removed} removed")
''')
s = replace_func(s, 'play', '''    def play(self, kind: str):
        if self.index is None:
            return
        duration = self._duration()
        if duration <= 0:
            return
        if self.play_kind is not None:
            self.stop_playback(reset=True)
            start = 0.0
        else:
            start = min(max(self.play_position, 0.0), max(0.0, duration - 1e-6))
        try:
            self.app.play_preview_for_index(self.index, kind, start_seconds=start)
        except Exception as exc:
            messagebox.showerror("TRIAZ Groove Builder", f"Could not play preview:\\n{exc}")
            return
        self.play_kind = kind
        self.play_started_mono = time.monotonic()
        self.play_started_position = start
        self.play_position = start
        self._draw_playhead_only()
        self._schedule_playhead()

''')
s = s.replace('''        if hit:
            _item, (ch, step, occ_t) = hit
            key = (ch, step)
            if shift:
''', '''        if hit:
            item, (ch, step, occ_t) = hit
            key = (ch, step)
            coords = self.canvas.coords(item)
            if len(coords) >= 4 and float(event.y) <= float(coords[1]) + 7.0:
                self.selected_hits = {key}
                self.selection_occurrence = {key: occ_t}
                self.drag = {"mode": "velocity", "sx": float(event.x), "sy": float(event.y), "ch": ch, "step": step, "occ_t": occ_t}
                self.redraw()
                return
            if shift:
''')
s = replace_func(s, '_mouse_move', '''    def _mouse_move(self, event):
        if not self.drag:
            return
        if self.drag.get("mode") == "marquee":
            if self.marquee_item is not None:
                self.canvas.coords(self.marquee_item, self.drag["sx"], self.drag["sy"], event.x, event.y)
            return
        if self.drag.get("mode") == "velocity":
            return

''')
helpers = '''    def _velocity_from_y(self, channel: int, y: float) -> float:
        row_bottom = self.lane_top + (int(channel) + 1) * self.lane_h - 3.0
        usable = max(8.0, self.lane_h - 7.0)
        return float(np.clip((row_bottom - float(y)) / usable, 0.05, 1.0))

    def _resize_selected_velocity(self, channel: int, step: int, y: float):
        result = self._result()
        if result is None:
            return
        lane = result.pattern.lanes.get(channel)
        if lane is None or not (0 <= int(step) < len(lane.velocity)):
            return
        self._push_undo()
        lane.velocity[int(step)] = self._velocity_from_y(channel, y)
        self.selected_hits = {(int(channel), int(step))}
        self.selection_occurrence = {(int(channel), int(step)): float(step) * self._base_step()}
        self._after_edit()

    def _nudge_velocity(self, delta: float):
        result = self._result()
        if result is None or not self.selected_hits:
            return "break"
        self._push_undo()
        for ch, step in list(self.selected_hits):
            lane = result.pattern.lanes.get(ch)
            if lane is not None and 0 <= step < len(lane.velocity) and lane.velocity[step] > 1e-7:
                lane.velocity[step] = float(np.clip(float(lane.velocity[step]) + float(delta), 0.05, 1.0))
        self._after_edit()
        return "break"

'''
s = s[:s.index('    def _mouse_up')] + helpers + s[s.index('    def _mouse_up'):]
s = s.replace('''        if mode == "move":
            if abs(event.x - drag["sx"]) >= 4 or abs(event.y - drag["sy"]) >= 4:
                self._move_selected(event, drag)
            return

        if mode != "marquee":
            return
''', '''        if mode == "velocity":
            self._resize_selected_velocity(int(drag["ch"]), int(drag["step"]), float(event.y))
            return

        if mode == "move":
            if abs(event.x - drag["sx"]) >= 4 or abs(event.y - drag["sy"]) >= 4:
                self._move_selected(event, drag)
            return

        if mode != "marquee":
            return
''')
s = s.replace('self.transient_cache[self.index] = source_transient_families(y, sr)', 'self.transient_cache[self.index] = source_transient_layers(y, sr)')
s = s.replace('trans_t, trans_s, trans_family = self.transient_cache[self.index]', 'trans_t, trans_s, trans_layers = self.transient_cache[self.index]')
start = s.index('        # Filled, colour-coded waveform.')
end = s.index('        # Clean TRIAZ-style musical grid.', start)
s = s[:start] + '''        # Filled, colour-coded waveform. The full audible waveform stays visible;
        # multi-label source events colour the waveform interior only.
        if y.size:
            bins = max(260, int(x1 - x0))
            edges = np.linspace(0, len(y), bins + 1, dtype=int)
            peak = max(float(np.max(np.abs(y))), 1e-9)
            mid = self.wave_top + self.wave_h / 2.0
            xs = np.empty(bins, dtype=float)
            amps = np.empty(bins, dtype=float)
            for i in range(bins):
                seg = y[edges[i]:edges[i + 1]]
                amps[i] = float(np.max(np.abs(seg))) / peak if len(seg) else 0.0
                xs[i] = x0 + (x1 - x0) * (i / max(1, bins - 1))
            layer_for_bin = np.zeros((bins, 4), dtype=float)
            if len(trans_t):
                center_samples = (edges[:-1] + edges[1:]) * 0.5
                bin_times = center_samples / float(sr)
                pos = np.searchsorted(trans_t, bin_times)
                prev_idx = np.clip(pos - 1, 0, len(trans_t) - 1)
                next_idx = np.clip(pos, 0, len(trans_t) - 1)
                prev_d = np.abs(bin_times - trans_t[prev_idx])
                next_d = np.abs(bin_times - trans_t[next_idx])
                nearest = np.where(next_d < prev_d, next_idx, prev_idx)
                nearest_d = np.minimum(prev_d, next_d)
                nearest_layers = np.asarray(trans_layers, dtype=float)[nearest]
                dominant = np.argmax(nearest_layers, axis=1)
                windows = np.where(dominant == 0, 0.34, np.where(dominant == 1, 0.24, np.where(dominant == 2, 0.14, 0.20)))
                active = (nearest_d <= windows) | (amps >= 0.030)
                layer_for_bin[active] = nearest_layers[active]
            half_h = self.wave_h * 0.42
            fam_ids = [0, 2, 6, 8]
            for i in range(bins):
                amp = float(amps[i])
                if amp <= 1e-6:
                    continue
                top = mid - amp * half_h
                bot = mid + amp * half_h
                layers = layer_for_bin[i]
                active_cols = [j for j, val in enumerate(layers) if val > 0.05]
                if not active_cols:
                    c.create_line(xs[i], top, xs[i], bot, fill="#7c858d", width=2)
                    continue
                active_cols = sorted(active_cols, key=lambda j: float(layers[j]), reverse=True)
                seg_h = (bot - top) / max(1, len(active_cols))
                for n, col in enumerate(active_cols):
                    y0 = top + n * seg_h
                    y1 = bot if n == len(active_cols) - 1 else top + (n + 1) * seg_h
                    c.create_line(xs[i], y0, xs[i], y1, fill=SOURCE_FAMILY_COLORS.get(fam_ids[col], "#7c858d"), width=2)

''' + s[end:]
s = s.replace('''        self.alignment.config(text=f"Timing alignment: {float(result.alignment) * 100.0:.0f}%")
        self._update_link_notice()
''', '''        self.editor_status.config(text="Editor pattern loaded")
        self._update_link_notice()
''')
write("triaz_groove_builder/editor.py", s)

print("v0.1.15 source-event matching patch applied")
