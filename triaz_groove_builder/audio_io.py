from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
from typing import Tuple
import numpy as np

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".aif", ".aiff"}


def load_audio(path: Path, sr: int = 44100, offset: float = 0.0, duration: float | None = None) -> Tuple[np.ndarray, int]:
    """Load mono audio, optionally only a time slice.

    For full songs the engine first loads a low-resolution scan, then seeks back to
    the selected 4-bar region and loads only that region at full quality.
    """
    import librosa
    y, actual_sr = librosa.load(
        str(path), sr=sr, mono=True, offset=max(0.0, float(offset)), duration=duration
    )
    return np.asarray(y, dtype=np.float32), int(actual_sr)


def get_audio_duration(path: Path) -> float:
    """Return duration without intentionally decoding/resampling the entire file."""
    try:
        import soundfile as sf
        return float(sf.info(str(path)).duration)
    except Exception:
        import librosa
        return float(librosa.get_duration(path=str(path)))


def filename_looks_like_drum_stem(path: Path) -> bool:
    name = path.stem.lower()
    return any(k in name for k in ("drum stem", "drums", "drum_stem", "- drum"))


def looks_like_drum_stem(path: Path, y: np.ndarray, sr: int) -> bool:
    if filename_looks_like_drum_stem(path):
        return True
    import librosa
    harm, perc = librosa.effects.hpss(y)
    pr = float(np.mean(perc * perc))
    hr = float(np.mean(harm * harm))
    return pr > 1.8 * max(hr, 1e-12)


def _local_python() -> str:
    """Use the persistent TRIAZ Groove Builder runtime for Demucs."""
    app_root = Path(__file__).resolve().parents[1]
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TRIAZ Groove Builder" / "runtime" / ".venv"
    candidates = [
        local / "Scripts" / "python.exe",
        app_root / ".venv" / "Scripts" / "python.exe",
        app_root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def separate_drums_demucs(input_path: Path, work_dir: Path) -> Path:
    """Separate only the selected short candidate clip using Demucs."""
    out = Path(work_dir) / "demucs"
    out.mkdir(parents=True, exist_ok=True)
    py = _local_python()
    check = subprocess.run(
        [py, "-c", "import demucs"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if check.returncode != 0:
        raise RuntimeError(
            "Demucs is not installed in the TRIAZ Groove Builder environment. "
            "Close and reopen TRIAZ Groove Builder. The installed launcher will repair the shared audio runtime automatically."
        )
    cmd = [
        py, "-m", "demucs", "--two-stems", "drums", "-n", "htdemucs",
        "--shifts", "0", "-o", str(out), str(input_path),
    ]
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if cp.returncode != 0:
        raise RuntimeError("Demucs separation failed:\n" + cp.stdout[-3000:])
    candidates = list(out.rglob("drums.wav"))
    if not candidates:
        raise RuntimeError("Demucs completed but no drums.wav was produced.")
    return candidates[0]
