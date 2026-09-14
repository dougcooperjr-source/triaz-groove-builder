from __future__ import annotations

from pathlib import Path
import re

ROOT = Path.cwd()


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_top_func(text: str, name: str, new: str) -> str:
    pat = re.compile(rf"^def {re.escape(name)}\(.*?(?=^def |\Z)", re.S | re.M)
    m = pat.search(text)
    if not m:
        raise RuntimeError(f"Missing top-level function: {name}")
    return text[:m.start()] + new.rstrip() + "\n\n" + text[m.end():]


# Version bump.
for rel in ("app.py", "launcher.py", "installer/triaz_groove_builder.iss"):
    s = read(rel)
    for old in ("0.1.22", "0.1.21", "0.1.20", "0.1.19"):
        s = s.replace(f"v{old}", "v0.1.23")
        s = s.replace(f'APP_VERSION = "{old}"', 'APP_VERSION = "0.1.23"')
        s = s.replace(f'#define MyAppVersion "{old}"', '#define MyAppVersion "0.1.23"')
        s = s.replace(f"TRIAZ_Groove_Builder_Setup_{old}.exe", "TRIAZ_Groove_Builder_Setup_0.1.23.exe")
    write(rel, s)
write("VERSION", "0.1.23\n")

# Main-page status should make clear this build is using Phase 2 grid conversion.
app = read("app.py")
app = app.replace("Preview / edit / approve", "Phase 2 band grid — preview / edit / approve")
app = app.replace("play/stop the loop to confirm it feels right, then rebuild", "play/stop the loop to confirm it feels right, then rebuild the Phase 2 grid")
write("app.py", app)

# Phase 2: selected brace -> band-filtered drum-family detection -> TRIAZ grid.
groove = read("triaz_groove_builder/groove.py")
if "def source_transient_layers" not in groove:
    raise RuntimeError("source_transient_layers is required for Phase 2 but is missing")
if "def _step_features" not in groove:
    raise RuntimeError("_step_features is required for Phase 2 but is missing")

phase2_helpers = r'''

def _phase2_keep_strongest(vel: np.ndarray, max_hits: int, protect_steps: set[int] | None = None) -> np.ndarray:
    """Limit over-dense lanes without changing timing of the retained hits."""
    vel = np.asarray(vel, dtype=float).copy()
    if max_hits <= 0:
        return np.zeros_like(vel)
    active = np.where(vel > 1e-7)[0]
    if len(active) <= max_hits:
        return vel
    protect_steps = protect_steps or set()
    protected = [int(i) for i in active if int(i) in protect_steps]
    remaining = [int(i) for i in active if int(i) not in protect_steps]
    keep_budget = max(0, int(max_hits) - len(protected))
    remaining.sort(key=lambda i: float(vel[i]), reverse=True)
    keep = set(protected + remaining[:keep_budget])
    out = np.zeros_like(vel)
    for i in keep:
        out[int(i)] = vel[int(i)]
    return out


def _phase2_band_authoritative_lanes(y: np.ndarray, sr: int, bpm: float, bars: int) -> tuple[Dict[int, LanePattern], str]:
    """Build TRIAZ lanes from filtered drum-family evidence inside the selected brace.

    This is the Phase 2 path: the colored kick/snare/hat source evidence is no
    longer just diagnostic.  It drives the writable TRIAZ grid, while density
    caps keep it from repeating the overly-aggressive v0.1.15 behavior.
    """
    y = np.asarray(y, dtype=float)
    n_steps = int(bars) * 16
    if n_steps <= 0 or y.size < 8 or sr <= 0:
        return {}, "Phase 2 band grid: no usable source audio."

    base_step = 60.0 / max(float(bpm), 1e-6) / 4.0
    times, strengths, layers = source_transient_layers(y, sr)
    kick = np.zeros(n_steps, dtype=float)
    snare = np.zeros(n_steps, dtype=float)
    clap = np.zeros(n_steps, dtype=float)
    hat = np.zeros(n_steps, dtype=float)

    # Dominant snare/clap phase. This preserves backbeat behavior while still
    # allowing strong off-beat snares/fills when the source clearly supports them.
    phase_score = np.zeros(8, dtype=float)
    for t, st, row in zip(times, strengths, layers):
        step = int(round(float(t) / max(base_step, 1e-9)))
        if 0 <= step < n_steps:
            phase_score[step % 8] += float(row[1]) * (0.35 + float(st))
    backbeat_phase = int(np.argmax(phase_score)) if float(np.max(phase_score)) > 0.0 else 4

    # Event-level family writing from the colored/filtered source markers.
    tolerance = max(0.052, min(0.090, base_step * 0.48))
    for t, st, row in zip(times, strengths, layers):
        step = int(round(float(t) / max(base_step, 1e-9)))
        if not (0 <= step < n_steps):
            continue
        if abs(float(t) - step * base_step) > tolerance:
            continue
        k, sc, h, _other = [float(x) for x in row]
        st = float(np.clip(st, 0.0, 1.0))
        base_vel = 0.42 + 0.52 * st

        kick_dominant = k >= 0.22 and k >= max(sc * 0.58, h * 0.70)
        if kick_dominant:
            kick[step] = max(float(kick[step]), float(np.clip(base_vel * (0.76 + 0.28 * k), 0.48, 1.0)))

        snare_supported = sc >= 0.23 and sc >= max(k * 0.42, h * 0.52)
        if snare_supported and ((step % 8) == backbeat_phase or sc >= 0.72):
            vel = float(np.clip(base_vel * (0.72 + 0.30 * sc), 0.46, 1.0))
            snare[step] = max(float(snare[step]), vel)
            # In TRIAZ, snare/clap layering often represents one mid-band source event.
            # Keep clap slightly under snare rather than inventing a separate pattern.
            clap[step] = max(float(clap[step]), min(1.0, vel * 0.94))

        hat_supported = h >= 0.18 and h >= max(k * 0.35, sc * 0.34)
        if hat_supported:
            hat[step] = max(float(hat[step]), float(np.clip(base_vel * (0.62 + 0.28 * h), 0.34, 0.92)))

    # Step-level high-band pass catches steady hats that do not produce a clearly
    # isolated source transient on every 16th.
    try:
        _grid, feats = _step_features(y, sr, bpm, bars)
        high = np.asarray(feats.get("high", np.zeros(n_steps)), dtype=float)
        vhigh = np.asarray(feats.get("vhigh", np.zeros(n_steps)), dtype=float)
        mid = np.asarray(feats.get("mid", np.zeros(n_steps)), dtype=float)
        low = np.asarray(feats.get("low", np.zeros(n_steps)), dtype=float)
        if len(high) == n_steps and len(vhigh) == n_steps:
            hat_score = 0.50 * high + 0.38 * vhigh + 0.10 * mid - 0.10 * low
            if float(np.max(hat_score)) > 0.0:
                floor = max(0.20, float(np.quantile(hat_score, 0.52)))
                for step, hs in enumerate(hat_score):
                    if float(hs) >= floor:
                        hat[step] = max(float(hat[step]), float(np.clip(0.34 + 0.48 * float(hs), 0.34, 0.88)))
    except Exception:
        pass

    # Protect clear downbeat kick, then cap density per family. These limits are
    # intentionally conservative so filtered evidence drives the grid without
    # bringing back v0.1.15-style clutter.
    protect_kick: set[int] = set()
    if kick.size and float(kick[0]) > 1e-7:
        protect_kick.add(0)
    kick = _phase2_keep_strongest(kick, max(6 * int(bars), 1), protect_kick)
    snare = _phase2_keep_strongest(snare, max(4 * int(bars), 1), set())
    clap = _phase2_keep_strongest(clap, max(4 * int(bars), 1), set())

    lanes: Dict[int, LanePattern] = {}
    if int(np.count_nonzero(kick)):
        lanes[0] = LanePattern(0, "KICK", kick)
    if int(np.count_nonzero(snare)):
        lanes[2] = LanePattern(2, "SNARE", snare)
    if int(np.count_nonzero(clap)):
        lanes[3] = LanePattern(3, "CLAP", clap)
    if int(np.count_nonzero(hat)):
        lanes[6] = LanePattern(6, "HIHAT CL", hat)

    summary = (
        "Phase 2 band-filtered transcription drove the TRIAZ grid: "
        f"Kick {int(np.count_nonzero(kick))}, "
        f"Snare {int(np.count_nonzero(snare))}, "
        f"Clap {int(np.count_nonzero(clap))}, "
        f"Hat {int(np.count_nonzero(hat))}."
    )
    return lanes, summary
'''
if "def _phase2_band_authoritative_lanes" not in groove:
    groove = groove.replace("\ndef transcribe_drum_clip(", phase2_helpers + "\ndef transcribe_drum_clip(", 1)

start = groove.index("def transcribe_drum_clip(")
next_def = re.search(r"\ndef [A-Za-z_]", groove[start + 1:])
end = len(groove) if next_def is None else start + 1 + next_def.start()
func = groove[start:end]
if "_phase2_band_authoritative_lanes" not in func:
    tail_start = func.index("    active = sum(int(np.count_nonzero(l.velocity)) for l in lanes.values())")
    new_tail = r'''    phase2_lanes, phase2_summary = _phase2_band_authoritative_lanes(y, sr, bpm, bars)
    phase2_hits = sum(int(np.count_nonzero(l.velocity)) for l in phase2_lanes.values())
    legacy_hits = sum(int(np.count_nonzero(l.velocity)) for l in lanes.values())
    if phase2_hits >= 3:
        # Phase 2 lanes are authoritative per family. Keep a legacy lane only if
        # Phase 2 has no confident evidence for that family, so previews do not go
        # completely empty on difficult material.
        for ch, legacy_lane in list(lanes.items()):
            if ch not in phase2_lanes and int(np.count_nonzero(legacy_lane.velocity)):
                phase2_lanes[ch] = legacy_lane
        lanes = phase2_lanes
    else:
        phase2_summary = phase2_summary + " Fallback used because filtered evidence was too sparse."

    active = sum(int(np.count_nonzero(l.velocity)) for l in lanes.values())
    conf = min(1.0, 0.45 + 0.01 * min(active, 50))
    notes = [phase2_summary]
    if phase2_hits >= 3:
        notes.append("Phase 2 source colors are now conversion evidence, not just waveform decoration.")
    else:
        notes.append(f"Phase 2 filtered evidence was sparse ({phase2_hits} hits), so legacy transcription preserved {legacy_hits} hits.")
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
    func = func[:tail_start] + new_tail
    groove = groove[:start] + func + groove[end:]

if "Phase 2 band-filtered transcription drove the TRIAZ grid" not in groove:
    raise RuntimeError("Phase 2 summary marker missing")
if "def _phase2_band_authoritative_lanes" not in groove:
    raise RuntimeError("Phase 2 helper missing")

write("triaz_groove_builder/groove.py", groove)
print("v0.1.23 Phase 2 band-filtered grid patch applied")
