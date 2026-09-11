from __future__ import annotations

from pathlib import Path
import re
import struct
import gzip
from typing import Iterable
import numpy as np

from .groove import GroovePattern

JUCE_TABLE = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"


def juce_encode_memory_block(data: bytes) -> str:
    """JUCE MemoryBlock::toBase64Encoding compatible encoder."""
    nbits = len(data) * 8
    nchars = (nbits + 5) // 6
    chars = []
    for i in range(nchars):
        bit0 = i * 6
        value = 0
        for j in range(6):
            bit = bit0 + j
            if bit >= nbits:
                break
            if data[bit // 8] & (1 << (bit % 8)):
                value |= 1 << j
        chars.append(JUCE_TABLE[value])
    return f"{len(data)}." + "".join(chars)


def encode_float32(values: Iterable[float]) -> str:
    vals = [float(v) for v in values]
    data = struct.pack("<" + "f" * len(vals), *vals)
    return juce_encode_memory_block(data)


def _step_data_xml(pattern: GroovePattern) -> str:
    rows = []
    for ch in sorted(pattern.lanes):
        lane = pattern.lanes[ch]
        v = np.asarray(lane.velocity, dtype=float)
        if len(v) != 32:
            raise ValueError(f"Lane {ch} has {len(v)} steps; TRIAZ output requires 32.")
        if not np.any(v > 1e-8):
            continue
        rows.append(
            f'      <StepData PatternIndex="0" ChannelIndex="{ch}" PatternLength="32" '
            f'Velocity="{encode_float32(v)}"\n'
            f'                Repeat="EMPTY" Offset="EMPTY" Chance="EMPTY" Motion="EMPTY" Pitch="EMPTY"/>'
        )
    if not rows:
        return "<StepData/>"
    return "<StepData>\n" + "\n".join(rows) + "\n    </StepData>"


def _replace_seq_speeds(text: str, pattern: GroovePattern) -> str:
    speeds = [1.0] * 12
    for ch, lane in pattern.lanes.items():
        if 0 <= ch < 12:
            speeds[ch] = float(lane.speed)
    seq = "<SeqData>\n" + "\n".join(
        f'      <SeqData Length="1.0" Speed="{s}"/>' for s in speeds
    ) + "\n    </SeqData>"
    text, n = re.subn(r"<SeqData>.*?</SeqData>", seq, text, count=1, flags=re.S)
    if n != 1:
        raise ValueError("Could not locate TRIAZ SeqData section in template.")
    return text


def write_preset(template_path: Path, output_path: Path, pattern: GroovePattern) -> Path:
    template_path = Path(template_path)
    if template_path.suffix.lower() == ".gz":
        with gzip.open(template_path, "rt", encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = template_path.read_text(encoding="utf-8")
    text = _replace_seq_speeds(text, pattern)
    step_xml = _step_data_xml(pattern)
    text, n = re.subn(r"<StepData(?:\s*/>|>.*?</StepData>)", step_xml, text, count=1, flags=re.S)
    if n != 1:
        raise ValueError("Could not locate TRIAZ StepData section in template.")
    text = re.sub(r'HostSync="[^"]+"', 'HostSync="1.0"', text, count=1)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8", newline="\n")
    return output_path
