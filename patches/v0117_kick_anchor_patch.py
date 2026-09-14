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
    if anchor not in text:
        raise RuntimeError(f'Missing insert anchor: {label}')
    return text.replace(anchor, insert.rstrip() + '\n\n' + anchor, 1)

# Version strings.
for rel in ('app.py', 'launcher.py', 'installer/triaz_groove_builder.iss'):
    p, s = rw(rel)
    s = s.replace('v0.1.16', 'v0.1.17')
    s = s.replace('APP_VERSION = "0.1.16"', 'APP_VERSION = "0.1.17"')
    s = s.replace('#define MyAppVersion "0.1.16"', '#define MyAppVersion "0.1.17"')
    s = s.replace('TRIAZ_Groove_Builder_Setup_0.1.16.exe', 'TRIAZ_Groove_Builder_Setup_0.1.17.exe')
    write(rel, s)
write('VERSION', '0.1.17\n')

p, s = rw('triaz_groove_builder/engine.py')
s = s.replace('Preview-ready groove', 'Kick-anchored groove')

kick_helper = r'''
def _kick_anchor_score(y: np.ndarray, sr: int, t: float) -> float:
    """Return a lightweight kick/low-drum likelihood for an exact timestamp."""
    y = np.asarray(y, dtype=float)
    if sr <= 0 or y.size < 64:
        return 0.0
    center = int(round(float(t) * sr))
    half = int(max(384, min(sr * 0.090, 4096)))
    a = max(0, center - half // 2)
    b = min(len(y), center + half)
    seg = y[a:b]
    if seg.size < 64:
        return 0.0
    seg = seg - float(np.mean(seg))
    win = np.hanning(seg.size)
    mag = np.abs(np.fft.rfft(seg * win))
    freqs = np.fft.rfftfreq(seg.size, 1.0 / float(sr))
    if mag.size == 0:
        return 0.0
    total = float(np.sum(mag)) + 1e-12
    sub = float(np.sum(mag[(freqs >= 35.0) & (freqs < 95.0)]))
    low = float(np.sum(mag[(freqs >= 45.0) & (freqs < 185.0)]))
    punch = float(np.sum(mag[(freqs >= 185.0) & (freqs < 430.0)]))
    high = float(np.sum(mag[(freqs >= 1800.0) & (freqs < min(9000.0, sr * 0.48))]))
    body = low + 0.45 * punch + 0.35 * sub
    low_share = body / total
    dominance = body / (high + 0.10 * total + 1e-12)
    peak = float(np.max(np.abs(seg)))
    rms = float(np.sqrt(np.mean(seg * seg))) + 1e-12
    transient = min(1.0, peak / (rms * 8.0))
    score = 0.62 * min(1.0, low_share * 5.0) + 0.28 * min(1.0, dominance / 1.35) + 0.10 * transient
    return float(np.clip(score, 0.0, 1.0))


def _kick_floor(scores: list[float] | np.ndarray) -> float:
    arr = np.asarray(scores, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0.30
    mx = float(np.max(arr))
    q75 = float(np.quantile(arr, 0.75)) if arr.size >= 4 else mx
    return max(0.26, min(0.56, max(q75 * 0.58, mx * 0.42)))
'''
if 'def _kick_anchor_score' not in s:
    s = insert_before_once(s, 'def _candidate_four_bar_clip', kick_helper, 'kick helper before candidate selector')

candidate_func = r'''
def _candidate_four_bar_clip(y: np.ndarray, sr: int, bpm: float):
    """Pick a representative four-bar region whose bar 1 starts on a kick anchor.

    Groove fidelity is the top goal.  A loop that begins on snare/clap/hat energy
    is musically wrong even if it contains drum activity, so the candidate search
    scores every possible bar start for a low-drum/kick anchor first, then uses
    repetition and energy as secondary tie-breakers.
    """
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
    kick_scores = np.asarray([_kick_anchor_score(y, sr, float(t)) for t in bt], dtype=float)
    kick_floor = _kick_floor(kick_scores)

    phase_scores = []
    for p in range(4):
        d = beat_strength[p::4]
        b2 = beat_strength[(p + 1) % 4::4]
        b4 = beat_strength[(p + 3) % 4::4]
        kd = kick_scores[p::4]
        def m(v):
            return float(np.mean(v)) if len(v) else 0.0
        phase_scores.append(0.72 * m(kd) + 0.18 * m(d) + 0.08 * m(b2) + 0.08 * m(b4))
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
        no_kick_penalty = 1.20 if kscore < kick_floor else 0.0
        scores.append(0.55 * repeat + 0.95 * kscore + 0.10 * math.log1p(max(0.0, energy)) - edge_penalty - no_kick_penalty)

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
    """Rotate a selected loop so Step 1 starts on a kick/low-drum anchor.

    This intentionally rejects snare/clap/hat-only starts.  If the detector cannot
    find a low anchor near the beginning, the safer behavior is to leave the clip
    unchanged and let manual BPM/downbeat correction expose the problem instead of
    fabricating a start point.
    """
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
    search_window = min(len(drum) / float(sr) - 0.010, max(beat * 2.0, 1.15))
    if search_window <= start_tol:
        search_window = min(len(drum) / float(sr), max(start_tol * 2.0, beat))

    idx_all = np.where((times >= 0.0) & (times <= search_window))[0]
    if len(idx_all) == 0:
        return drum, 0.0
    low_scores = np.asarray([_kick_anchor_score(drum, sr, float(times[i])) for i in idx_all], dtype=float)
    floor = _kick_floor(low_scores)

    at_start = [j for j, i in enumerate(idx_all) if float(times[i]) <= start_tol]
    if at_start:
        best_start = max(at_start, key=lambda j: low_scores[j] * (0.65 + float(strengths[idx_all[j]])))
        if float(low_scores[best_start]) >= floor:
            return drum, 0.0

    candidates = [j for j, i in enumerate(idx_all) if float(times[i]) > start_tol and float(low_scores[j]) >= floor]
    if not candidates:
        return drum, 0.0

    def score_candidate(j: int) -> float:
        i = int(idx_all[j])
        t = float(times[i])
        # Prefer the first strong kick anchor, but allow a much stronger nearby kick
        # to beat a weak one.
        proximity = max(0.0, 1.0 - min(1.0, t / max(search_window, 1e-9)))
        return 0.78 * float(low_scores[j]) + 0.17 * float(strengths[i]) + 0.05 * proximity

    best_j = max(candidates, key=score_candidate)
    shift_seconds = float(times[int(idx_all[best_j])])
    shift_samples = int(round(shift_seconds * sr))
    if shift_samples <= 0 or shift_samples >= len(drum):
        return drum, 0.0
    return np.concatenate([drum[shift_samples:], drum[:shift_samples]]), shift_seconds
'''
s = replace_top_func(s, '_anchor_drum_loop_start', anchor_func)
write('triaz_groove_builder/engine.py', s)

print('v0.1.17 kick-anchored loop start patch applied')
