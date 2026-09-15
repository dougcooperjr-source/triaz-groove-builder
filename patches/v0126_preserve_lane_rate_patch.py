from __future__ import annotations

from pathlib import Path

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
    s = s.replace('v0.1.25', 'v0.1.26')
    s = s.replace('APP_VERSION = "0.1.25"', 'APP_VERSION = "0.1.26"')
    s = s.replace('#define MyAppVersion "0.1.25"', '#define MyAppVersion "0.1.26"')
    s = s.replace('TRIAZ_Groove_Builder_Setup_0.1.25.exe', 'TRIAZ_Groove_Builder_Setup_0.1.26.exe')
    write(rel, s)
write('VERSION', '0.1.26\n')

editor = read('triaz_groove_builder/editor.py')

# Editing must never silently change the lane's TRIAZ rate. The old helper could
# automatically convert a repeated 1/16 lane (speed 1.0) into 1/8 (speed 0.5)
# when adding/moving/deleting hits. Preserve whatever rate the pattern currently
# has and let repeated lanes remain linked by native TRIAZ semantics.
editor = replace_once(
    editor,
    '''    def _add_hit_at(self, ch: int, t: float):\n        self._push_undo()\n        self._promote_lane_if_exact(ch)\n        lane = self._ensure_lane(ch)\n''',
    '''    def _add_hit_at(self, ch: int, t: float):\n        self._push_undo()\n        lane = self._ensure_lane(ch)\n''',
    'add-hit auto promotion',
)

old_move = '''        # First losslessly expand any selected linked lane that can represent the\n        # full source at 1/8.  Occurrence times then address the two halves separately.\n        promoted_channels = set()\n        for ch, _step in drag["keys"]:\n            if ch not in promoted_channels and self._promote_lane_if_exact(ch):\n                promoted_channels.add(ch)\n\n        moving = []\n'''
new_move = '''        # Preserve each lane's current TRIAZ rate during editing. A repeated\n        # 1/16 lane stays 1/16; visible repeats remain linked by design.\n        promoted_channels = set()\n\n        moving = []\n'''
editor = replace_once(editor, old_move, new_move, 'move auto promotion')

old_delete = '''        targets = []\n        promoted = set()\n        for ch, step in list(self.selected_hits):\n            occ_t = float(self.selection_occurrence.get((ch, step), step * self._base_step()))\n            if ch not in promoted and self._promote_lane_if_exact(ch):\n                promoted.add(ch)\n            if ch in promoted:\n                step = self._step_from_time(ch, occ_t)\n            targets.append((ch, step))\n'''
new_delete = '''        targets = []\n        promoted = set()\n        for ch, step in list(self.selected_hits):\n            targets.append((ch, step))\n'''
editor = replace_once(editor, old_delete, new_delete, 'delete auto promotion')

editor = editor.replace(
    'Linked 2-bar lane: {names}. Visible repeats are the same TRIAZ step. Exact 1/8 lanes are expanded automatically when possible.',
    'Linked 2-bar lane: {names}. Visible repeats are the same TRIAZ step. Editing preserves the current lane rate.',
)
write('triaz_groove_builder/editor.py', editor)

# CI sanity: no editing call site may invoke automatic promotion anymore.
body_after_method = editor.split('def _promote_lane_if_exact', 1)[1]
call_count = body_after_method.count('self._promote_lane_if_exact(')
if call_count != 0:
    raise RuntimeError(f'v0.1.26 still has {call_count} automatic lane-rate promotion call(s)')
if 'Editing preserves the current lane rate.' not in editor:
    raise RuntimeError('v0.1.26 lane-rate notice missing')

print('v0.1.26 lane rate preservation patch applied')
