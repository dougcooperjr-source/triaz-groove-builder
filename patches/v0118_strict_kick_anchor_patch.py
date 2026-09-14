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


# Version strings.
for rel in ('app.py', 'launcher.py', 'installer/triaz_groove_builder.iss'):
    p, s = rw(rel)
    s = s.replace('v0.1.17', 'v0.1.18')
    s = s.replace('APP_VERSION = "0.1.17"', 'APP_VERSION = "0.1.18"')
    s = s.replace('#define MyAppVersion "0.1.17"', '#define MyAppVersion "0.1.18"')
    s = s.replace('TRIAZ_Groove_Builder_Setup_0.1.17.exe', 'TRIAZ_Groove_Builder_Setup_0.1.18.exe')
    write(rel, s)
write('VERSION', '0.1.18\n')

p, s = rw('triaz_groove_builder/engine.py')
s = s.replace('    source_transient_times,\n', '    source_transient_times, source_transient_layers,\n')
s = s.replace('Kick-anchored groove', 'Strict kick-anchored groove')

strict_helper = r'''
def _kick_dominant_event_indices(times: np.ndarray, strengths: np.ndarray, layers: np.ndarray, sr: int, y: np.ndarray, bpm: float, max_time: float) -> list[int]:
    """Return transient indices that are credible kick/low anchors, not snare/clap/hat-only hits."""
    if len(times) == 0:
        return []
    beat = 60.0 / max(float(bpm), 1e-6)
    max_time = max(0.0, float(max_time))
    candidates: list[int] = []
    lows = []
    for i, t in enumerate(times):
        if float(t) > max_time:
            continue
        k = float(layers[i, 0]) if layers.ndim == 2 and layers.shape[1] >= 1 else 0.0
        sn = float(layers[i, 1]) if layers.ndim == 2 and layers.shape[1] >= 2 else 0.0
        hat = float(layers[i, 2]) if layers.ndim == 2 and layers.shape[1] >= 3 else 0.0
        low_score = _kick_anchor_score(y, sr, float(t))
        lows.append(low_score)
        # Must be kick/low-dominant. A snare/clap/hat-only start cannot pass just
        # because it has some low energy in the FFT window.
        dominant = k >= max(sn * 1.08, hat * 1.18, 0.24)
        spectral_ok = low_score >= 0.24 or k >= 0.48
        if dominant and spectral_ok:
            candidates.append(i)
    if candidates:
        return candidates

    # Conservative fallback: only use a very strong low spectral transient when the
    # multi-label classifier failed to mark anything as kick. This still rejects
    # ordinary yellow/cyan snare-hat starts.
    if not lows:
        return []
    floor = max(0.38, _kick_floor(lows))
    for i, t in enumerate(times):
        if float(t) > max_time:
            continue
        low_score = _kick_anchor_score(y, sr, float(t))
        k = float(layers[i, 0]) if layers.ndim == 2 and layers.shape[1] >= 1 else 0.0
        sn = float(layers[i, 1]) if layers.ndim == 2 and layers.shape[1] >= 2 else 0.0
        hat = float(layers[i, 2]) if layers.ndim == 2 and layers.shape[1] >= 3 else 0.0
        if low_score >= floor and k >= (sn * 0.82) and k >= (hat * 0.82):
            candidates.append(i)
    return candidates


def _kick_event_score_at(times: np.ndarray, strengths: np.ndarray, layers: np.ndarray, y: np.ndarray, sr: int, bpm: float, t: float, tol: float) -> float:
    """Score whether a candidate beat time starts on an actual kick-dominant event."""
    if len(times) == 0:
        return 0.0
    idx = np.where(np.abs(times - float(t)) <= float(tol))[0]
    if len(idx) == 0:
        return 0.0
    kick_idx = _kick_dominant_event_indices(times[idx], strengths[idx], layers[idx], sr, y, bpm, max_time=10_000.0)
    if not kick_idx:
        return 0.0
    best = 0.0
    for local_j in kick_idx:
        i = int(idx[int(local_j)])
        proximity = max(0.0, 1.0 - abs(float(times[i]) - float(t)) / max(float(tol), 1e-9))
        k = float(layers[i, 0]) if layers.ndim == 2 and layers.shape[1] >= 1 else 0.0
        low = _kick_anchor_score(y, sr, float(times[i]))
        best = max(best, 0.55 * k + 0.30 * low + 0.15 * float(strengths[i]) + 0.10 * proximity)
    return float(best)
'''
if 'def _kick_dominant_event_indices' not in s:
    s = s.replace('\n\ndef _candidate_four_bar_clip', strict_helper + '\n\ndef _candidate_four_bar_clip', 1)

candidate_func = r'''
def _candidate_four_bar_clip(y: np.ndarray, sr: int, bpm: float):
    """Pick a representative four-bar region whose bar 1 starts on a kick anchor."""
    import librosa

    hop = 256 if sr <= 12000 else 512
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop, bpm=bpm)
    bt = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    length = int(round(16 * 60.0 / max(float(bpm), 1e-6) * sr))
    if len(bt) < 20:
        clip = y[:length]
        anchored, shift = _anchor_drum_loop_start(clip, sr, bpm)
        return anchored, float(shift)

    frame_times = librosa.frames_to_time(np.arange(len(onset)), sr=sr, hop_length=hop)
    beat_strength = np.interp(bt, frame_times, onset)
    try:
        ev_times, ev_strengths, ev_layers = source_transient_layers(y, sr)
    except Exception:
        ev_times, ev_strengths, ev_layers = source_transient_times(y, sr)[0], source_transient_times(y, sr)[1], np.zeros((0, 4), dtype=float)
    beat = 60.0 / max(float(bpm), 1e-6)
    kick_tol = max(0.045, min(0.110, beat * 0.16))
    kick_scores = np.asarray([_kick_event_score_at(ev_times, ev_strengths, ev_layers, y, sr, bpm, float(t), kick_tol) for t in bt], dtype=float)

    phase_scores = []
    for p in range(4):
        kd = kick_scores[p::4]
        d = beat_strength[p::4]
        def m(v):
            return float(np.mean(v)) if len(v) else 0.0
        # Downbeat phase is driven primarily by actual kick-dominant source events.
        phase_scores.append(0.86 * m(kd) + 0.14 * m(d))
    phase = int(np.argmax(phase_scores))

    starts = list(range(phase, len(bt) - 16, 4))
    if not starts:
        clip = y[:length]
        anchored, shift = _anchor_drum_loop_start(clip, sr, bpm)
        return anchored, float(shift)

    duration = len(y) / sr
    vectors = []
    valid_starts = []
    for bi in starts:
        t0 = float(bt[bi])
        t1 = float(bt[bi + 16]) if bi + 16 < len(bt) else t0 + 16 * 60.0 / max(float(bpm), 1e-6)
        if t1 > duration:
            continue
        center = 0.5 * (t0 + t1)
        edge_penalty = 0.0 if duration * 0.10 <= center <= duration * 0.90 else 0.25
        ts = t0 + np.arange(64) * ((t1 - t0) / 64.0)
        vec = np.interp(ts, frame_times, onset)
        energy = float(np.mean(vec))
        vec = vec / max(float(np.linalg.norm(vec)), 1e-9)
        kscore = float(kick_scores[bi]) if bi < len(kick_scores) else 0.0
        vectors.append((vec, edge_penalty, energy, kscore))
        valid_starts.append((bi, t0, t1))

    if not vectors:
        clip = y[:length]
        anchored, shift = _anchor_drum_loop_start(clip, sr, bpm)
        return anchored, float(shift)

    M = np.vstack([v[0] for v in vectors])
    sims = M @ M.T
    scores = []
    for i, (_, edge_penalty, energy, kscore) in enumerate(vectors):
        others = np.sort(sims[i][np.arange(len(vectors)) != i])
        repeat = float(np.mean(others[-min(4, len(others)):])) if len(others) else 0.0
        # This is now a hard musical preference: a candidate with no real kick at
        # its first beat cannot win over a kick-starting candidate.
        no_kick_penalty = 2.50 if kscore <= 0.0 else 0.0
        scores.append(0.52 * repeat + 1.65 * kscore + 0.08 * math.log1p(max(0.0, energy)) - edge_penalty - no_kick_penalty)

    best = int(np.argmax(scores))
    _, t0, t1 = valid_starts[best]
    i0, i1 = int(round(t0 * sr)), int(round(t1 * sr))
    clip = y[i0:i1]

    anchored, shift = _anchor_drum_loop_start(clip, sr, bpm)
    if shift > 1e-6:
        t0 += shift
    return anchored, t0
'''
s = replace_top_func(s, '_candidate_four_bar_clip', candidate_func)

anchor_func = r'''
def _anchor_drum_loop_start(drum: np.ndarray, sr: int, bpm: float) -> tuple[np.ndarray, float]:
    """Rotate a selected loop so Step 1 starts on a kick-dominant source event.

    v0.1.17 still allowed snare/clap/hat-only starts when they had enough low FFT
    energy. This version uses the same colored source-event family classifier as the
    editor and only accepts a real kick/low-drum label as the loop start.
    """
    drum = np.asarray(drum, dtype=float)
    if drum.size < 2 or sr <= 0:
        return drum, 0.0
    try:
        times, strengths, layers = source_transient_layers(drum, sr)
    except Exception:
        try:
            times, strengths = source_transient_times(drum, sr)
            layers = np.zeros((len(times), 4), dtype=float)
        except Exception:
            return drum, 0.0
    if len(times) == 0:
        return drum, 0.0

    beat = 60.0 / max(float(bpm), 1e-6)
    start_tol = max(0.030, min(0.060, beat * 0.075))
    search_window = min(len(drum) / float(sr) - 0.010, max(beat * 4.0, 1.25))
    if search_window <= start_tol:
        search_window = min(len(drum) / float(sr), max(start_tol * 2.0, beat))

    kick_idxs = _kick_dominant_event_indices(times, strengths, layers, sr, drum, bpm, search_window)
    if not kick_idxs:
        return drum, 0.0

    # Only keep the current start when a kick-dominant event is already at the loop
    # boundary. A yellow/cyan event at the boundary followed by a red kick must be
    # rotated to the red kick.
    start_kicks = [i for i in kick_idxs if float(times[i]) <= start_tol]
    if start_kicks:
        return drum, 0.0

    def score_candidate(i: int) -> float:
        t = float(times[i])
        k = float(layers[i, 0]) if layers.ndim == 2 and layers.shape[1] >= 1 else 0.0
        low = _kick_anchor_score(drum, sr, t)
        # Earliest credible kick is usually the correct musical loop start. A much
        # stronger kick can still beat a weak early one, but proximity dominates.
        proximity = max(0.0, 1.0 - min(1.0, t / max(search_window, 1e-9)))
        return 0.56 * proximity + 0.24 * k + 0.15 * low + 0.05 * float(strengths[i])

    best_i = max(kick_idxs, key=score_candidate)
    shift_seconds = float(times[int(best_i)])
    shift_samples = int(round(shift_seconds * sr))
    if shift_samples <= 0 or shift_samples >= len(drum):
        return drum, 0.0
    return np.concatenate([drum[shift_samples:], drum[:shift_samples]]), shift_seconds
'''
s = replace_top_func(s, '_anchor_drum_loop_start', anchor_func)
write('triaz_groove_builder/engine.py', s)

print('v0.1.18 strict kick-dominant loop start patch applied')
