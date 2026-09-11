from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
import tempfile
from typing import Callable
import numpy as np

from .audio_io import (
    load_audio, get_audio_duration, filename_looks_like_drum_stem,
    looks_like_drum_stem, separate_drums_demucs,
)
from .groove import (
    GroovePattern, estimate_bpm, transcribe_drum_clip,
    fold_four_bars_to_one_pattern, timing_alignment_score, auto_align_pattern,
)
from .triaz_preset import write_preset
from .preview import PreviewAssets, create_preview_assets


@dataclass
class BuildResult:
    source: Path
    output_path: Path
    bpm: float
    confidence: float
    alignment: float
    notes: list[str]
    pattern: GroovePattern
    preview: PreviewAssets | None = None
    preset: Path | None = None
    source_drums: np.ndarray | None = None
    sample_rate: int = 44100
    auto_align_summary: str = ""


def _notify(cb: Callable[[str], None] | None, text: str):
    if cb:
        cb(text)


def _candidate_four_bar_clip(y: np.ndarray, sr: int, bpm: float):
    import librosa
    hop = 256 if sr <= 12000 else 512
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop, bpm=bpm)
    bt = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    if len(bt) < 20:
        length = int(round(16 * 60.0 / bpm * sr))
        return y[:length], 0.0
    frame_times = librosa.frames_to_time(np.arange(len(onset)), sr=sr, hop_length=hop)
    beat_strength = np.interp(bt, frame_times, onset)
    phase_scores = []
    for p in range(4):
        d = beat_strength[p::4]
        b2 = beat_strength[(p + 1) % 4::4]
        b4 = beat_strength[(p + 3) % 4::4]
        def m(v):
            return float(np.mean(v)) if len(v) else 0.0
        phase_scores.append(0.55 * m(d) + 0.30 * m(b2) + 0.30 * m(b4))
    phase = int(np.argmax(phase_scores))
    starts = list(range(phase, len(bt) - 16, 4))
    if not starts:
        length = int(round(16 * 60.0 / bpm * sr))
        return y[:length], 0.0
    duration = len(y) / sr
    vectors = []
    valid_starts = []
    for bi in starts:
        t0 = float(bt[bi])
        t1 = float(bt[bi + 16]) if bi + 16 < len(bt) else t0 + 16 * 60.0 / bpm
        if t1 > duration:
            continue
        center = 0.5 * (t0 + t1)
        edge_penalty = 0.0 if duration * 0.10 <= center <= duration * 0.90 else 0.25
        ts = t0 + np.arange(64) * ((t1 - t0) / 64.0)
        vec = np.interp(ts, frame_times, onset)
        energy = float(np.mean(vec))
        vec = vec / max(float(np.linalg.norm(vec)), 1e-9)
        vectors.append((vec, edge_penalty, energy))
        valid_starts.append((bi, t0, t1))
    if not vectors:
        length = int(round(16 * 60.0 / bpm * sr))
        return y[:length], 0.0
    M = np.vstack([v[0] for v in vectors])
    sims = M @ M.T
    scores = []
    for i, (_, edge_penalty, energy) in enumerate(vectors):
        others = np.sort(sims[i][np.arange(len(vectors)) != i])
        repeat = float(np.mean(others[-min(4, len(others)):])) if len(others) else 0.0
        scores.append(repeat + 0.12 * math.log1p(max(0.0, energy)) - edge_penalty)
    best = int(np.argmax(scores))
    _, t0, t1 = valid_starts[best]
    i0, i1 = int(round(t0 * sr)), int(round(t1 * sr))
    return y[i0:i1], t0


def _filename_bpm(source: Path) -> float | None:
    m = re.search(r'(?i)(?:^|[^0-9])(\d{2,3}(?:\.\d+)?)\s*[-_ ]?bpm(?:[^a-z]|$)', source.stem)
    if not m:
        m = re.search(r'(?i)\[(\d{2,3}(?:\.\d+)?)\s*bpm\]', source.stem)
    return float(m.group(1)) if m else None


def _safe_output_name(source: Path, output_name: str | None, bpm: float) -> str:
    base = (output_name or source.stem).strip()
    if base.lower().endswith('.preset'):
        base = base[:-7].rstrip()
    base = re.sub(r'(?i)\s*\[\s*\d{2,3}(?:\.\d+)?\s*BPM\s*\]\s*$', '', base).strip()
    safe = ''.join(c for c in base if c not in '<>:"/\\|?*').strip().rstrip('. ')
    return safe or 'TRIAZ Groove'


def build_from_audio(
    source: Path,
    output_dir: Path,
    template_path: Path,
    sample_source: str = 'TRIAZ Libraries',
    progress: Callable[[str], None] | None = None,
    output_name: str | None = None,
    preview_root: Path | None = None,
    defer_export: bool = True,
) -> BuildResult:
    source = Path(source)
    named_bpm = _filename_bpm(source)
    if named_bpm is not None:
        named_bpm = float(int(round(named_bpm)))
    duration = get_audio_duration(source)
    explicit_stem = filename_looks_like_drum_stem(source)
    notes = [f'Sample source selector: {sample_source} (v0.1.9 uses the selected template kit; full library indexing comes after groove validation).']

    if duration <= 16.0:
        _notify(progress, 'Loading short audio…')
        y, sr = load_audio(source, sr=44100)
        bpm = named_bpm if named_bpm is not None else estimate_bpm(y, sr)
        bpm = float(int(round(bpm)))
        dur = len(y) / sr
        if not named_bpm and (explicit_stem or looks_like_drum_stem(source, y, sr)) and dur < 14.0:
            candidates = []
            for bars_guess in (2, 4):
                b = bars_guess * 4 * 60.0 / dur
                if 65.0 <= b <= 180.0:
                    candidates.append((abs(b - round(b)), abs(b - 100.0), b))
            if candidates:
                candidates.sort()
                bpm = float(int(round(candidates[0][2])))
        bpm = float(int(round(bpm)))
        two_bar = 8 * 60.0 / bpm
        four_bar = 16 * 60.0 / bpm
        stem_like = explicit_stem or looks_like_drum_stem(source, y, sr)
        if stem_like and abs(dur - two_bar) <= max(0.18, two_bar * 0.04):
            drum, bars, start = y, 2, 0.0
            notes.append('Used supplied 2-bar drum stem directly.')
        elif stem_like and abs(dur - four_bar) <= max(0.25, four_bar * 0.04):
            drum, bars, start = y, 4, 0.0
            notes.append('Used supplied 4-bar drum stem directly.')
        else:
            bars, start = 4, 0.0
            clip_len = min(len(y), int(round(four_bar * sr)))
            clip = y[:clip_len]
            if stem_like:
                drum = clip
                notes.append('Used the first representative 4 bars from a short drum stem.')
            else:
                _notify(progress, 'Separating selected 4 bars…')
                with tempfile.TemporaryDirectory(prefix='triaz_groove_') as td:
                    import soundfile as sf
                    candidate = Path(td) / 'candidate.wav'
                    sf.write(candidate, clip, sr)
                    drums_path = separate_drums_demucs(candidate, Path(td))
                    drum, _ = load_audio(drums_path, sr=sr)
                notes.append('Separated only the short 4-bar clip with Demucs.')
    else:
        _notify(progress, 'Fast song scan…')
        scan_sr = 11025
        scan, scan_sr = load_audio(source, sr=scan_sr)
        bpm = named_bpm if named_bpm is not None else estimate_bpm(scan, scan_sr)
        bpm = float(int(round(bpm)))
        _notify(progress, 'Choosing representative 4 bars…')
        _, start = _candidate_four_bar_clip(scan, scan_sr, bpm)
        four_bar = 16 * 60.0 / bpm
        _notify(progress, 'Loading selected 4 bars…')
        clip, sr = load_audio(source, sr=44100, offset=start, duration=four_bar)
        bars = 4
        if explicit_stem:
            drum = clip
            notes.append('Low-resolution full-file scan; loaded only the selected 4 bars at 44.1 kHz.')
        else:
            _notify(progress, 'Separating selected 4 bars…')
            with tempfile.TemporaryDirectory(prefix='triaz_groove_') as td:
                import soundfile as sf
                candidate = Path(td) / 'candidate.wav'
                sf.write(candidate, clip, sr)
                drums_path = separate_drums_demucs(candidate, Path(td))
                drum, _ = load_audio(drums_path, sr=sr)
            notes.append('Scanned the whole song at 11.025 kHz, then separated only the selected 4 bars with Demucs.')

    _notify(progress, 'Transcribing groove…')
    pat = transcribe_drum_clip(drum, sr, bpm=bpm, bars=bars)
    pat.selected_start = start
    pat.selected_end = start + len(drum) / sr
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
        preview = create_preview_assets(source=source, source_drums=drum, sr=sr, pattern=pat, preview_root=Path(preview_root), output_name=safe)
    result = BuildResult(
        source=source, output_path=output_path, bpm=bpm, confidence=pat.confidence,
        alignment=alignment, notes=pat.notes, pattern=pat, preview=preview, preset=None,
        source_drums=np.asarray(drum, dtype=np.float32).copy(), sample_rate=int(sr),
        auto_align_summary=auto_align_summary,
    )
    if not defer_export:
        approve_and_export(result, template_path)
    return result


def refresh_preview(result: BuildResult, preview_root: Path, output_name: str | None = None) -> PreviewAssets:
    if result.source_drums is None or result.sample_rate <= 0:
        raise RuntimeError('The separated source drums are not available for this draft. Re-analyze the file.')
    safe = _safe_output_name(result.source, output_name, result.bpm)
    result.alignment = timing_alignment_score(result.source_drums, result.sample_rate, result.pattern)
    result.preview = create_preview_assets(
        source=result.source, source_drums=result.source_drums, sr=result.sample_rate,
        pattern=result.pattern, preview_root=Path(preview_root), output_name=safe,
    )
    return result.preview


def auto_align_result(result: BuildResult, preview_root: Path, output_name: str | None = None):
    if result.source_drums is None or result.sample_rate <= 0:
        raise RuntimeError('The separated source drums are not available for this draft. Re-analyze the file.')
    corrected, stats = auto_align_pattern(result.source_drums, result.sample_rate, result.pattern, allow_add_remove=True)
    result.pattern = corrected
    result.alignment = stats.after
    result.auto_align_summary = (
        f'Auto Align: {stats.moved} moved, {stats.added} added, {stats.removed} removed; '
        f'timing {stats.before * 100.0:.0f}% → {stats.after * 100.0:.0f}%.'
    )
    refresh_preview(result, preview_root, output_name=output_name)
    return stats


def approve_and_export(result: BuildResult, template_path: Path) -> Path:
    preset = write_preset(Path(template_path), Path(result.output_path), result.pattern)
    result.preset = preset
    return preset
