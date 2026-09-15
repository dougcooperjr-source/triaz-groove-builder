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
    s = s.replace('v0.1.24', 'v0.1.25')
    s = s.replace('APP_VERSION = "0.1.24"', 'APP_VERSION = "0.1.25"')
    s = s.replace('#define MyAppVersion "0.1.24"', '#define MyAppVersion "0.1.25"')
    s = s.replace('TRIAZ_Groove_Builder_Setup_0.1.24.exe', 'TRIAZ_Groove_Builder_Setup_0.1.25.exe')
    write(rel, s)
write('VERSION', '0.1.25\n')

# Overlay slider range: extend to -60 dB, with the very bottom as a true mute.
editor = read('triaz_groove_builder/editor.py')
editor = editor.replace('audition, from_=-24.0, to=6.0, variable=self.overlay_source_db', 'audition, from_=-60.0, to=6.0, variable=self.overlay_source_db')
editor = editor.replace('audition, from_=-24.0, to=6.0, variable=self.overlay_groove_db', 'audition, from_=-60.0, to=6.0, variable=self.overlay_groove_db')

old_labels = '''    def _update_overlay_level_labels(self, _value=None):\n        if hasattr(self, "source_db_label"):\n            self.source_db_label.config(text=f"{float(self.overlay_source_db.get()):+.1f} dB")\n        if hasattr(self, "groove_db_label"):\n            self.groove_db_label.config(text=f"{float(self.overlay_groove_db.get()):+.1f} dB")\n'''
new_labels = '''    def _update_overlay_level_labels(self, _value=None):\n        if hasattr(self, "source_db_label"):\n            value = float(self.overlay_source_db.get())\n            self.source_db_label.config(text="MUTE" if value <= -59.5 else f"{value:+.1f} dB")\n        if hasattr(self, "groove_db_label"):\n            value = float(self.overlay_groove_db.get())\n            self.groove_db_label.config(text="MUTE" if value <= -59.5 else f"{value:+.1f} dB")\n'''
editor = replace_once(editor, old_labels, new_labels, 'overlay level label method')
write('triaz_groove_builder/editor.py', editor)

preview = read('triaz_groove_builder/preview.py')
old_gain = '''    src_gain = float(10.0 ** (float(source_db) / 20.0))\n    groove_gain = float(10.0 ** (float(groove_db) / 20.0))\n'''
new_gain = '''    # Bottom of either overlay fader is a true mute, not merely a very low gain.\n    src_gain = 0.0 if float(source_db) <= -59.5 else float(10.0 ** (float(source_db) / 20.0))\n    groove_gain = 0.0 if float(groove_db) <= -59.5 else float(10.0 ** (float(groove_db) / 20.0))\n'''
preview = replace_once(preview, old_gain, new_gain, 'overlay gain floor')
write('triaz_groove_builder/preview.py', preview)

combined = editor + preview
for marker in ('from_=-60.0', 'text="MUTE" if value <= -59.5', 'src_gain = 0.0 if float(source_db) <= -59.5'):
    if marker not in combined:
        raise RuntimeError(f'Missing v0.1.25 marker: {marker}')

print('v0.1.25 overlay mute floor patch applied')
