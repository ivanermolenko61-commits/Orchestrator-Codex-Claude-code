#!/usr/bin/env python3
"""Check/install shared Codex + Qwen agentq; personal settings are never changed."""
import argparse
import datetime
import json
import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def linked(path):
    if path.is_symlink():
        return True
    if os.name == 'nt':
        try:
            return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except FileNotFoundError:
            return False
    return False


def planned_files(home):
    runtime = home / '.agents/skills/agentq'
    files = {runtime / p.name: p.read_bytes() for p in (ROOT / 'skills/agentq').glob('*') if p.is_file()}
    files.update({runtime / 'reference' / p.name: p.read_bytes() for p in (ROOT / 'skills/agentq/reference').glob('*.md')})
    files[runtime / 'agentsrules.py'] = (ROOT / 'global/scripts/agentsrules.py').read_bytes()
    files[runtime / 'VERSION'] = (ROOT / 'VERSION').read_bytes()
    text = (ROOT / 'skills/agentq/SKILL.md').read_text(encoding='utf-8')
    for key, value in {'{{PYTHON}}': Path(sys.executable).as_posix(), '{{ENGINE}}': (runtime / 'agentq_global.py').as_posix()}.items():
        text = text.replace(key, value)
    files[runtime / 'SKILL.md'] = text.encode('utf-8')
    files[home / '.qwen/skills/agentq/SKILL.md'] = text.encode('utf-8')
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--home', type=Path, default=Path.home())
    args = parser.parse_args(argv)
    home = args.home.resolve()
    files = planned_files(home)
    # Refuse redirected destinations before the first write or backup.
    for path in files:
        path.resolve().relative_to(home)
        for parent in (path, *path.parents):
            if parent == home:
                break
            if linked(parent):
                parser.error(f'linked installation path: {parent}')
    installed = home / '.agents/skills/agentq/VERSION'
    version = (ROOT / 'VERSION').read_text().strip()
    if installed.is_file() and tuple(map(int, installed.read_text().strip().split('.'))) > tuple(map(int, version.split('.'))):
        parser.error('installed agentq is newer than source')
    changed = [(p, data) for p, data in files.items() if not p.is_file() or p.read_bytes() != data]
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = home / 'agentq-backups' / stamp
    for path, data in changed:
        print(('UPDATE ' if path.exists() else 'ADD ') + str(path))
        if args.apply:
            if path.exists():
                saved = backup / path.relative_to(home)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if args.apply:
        backup.mkdir(parents=True, exist_ok=True)
        (backup / 'receipt.json').write_text(json.dumps({'version': version, 'files': [str(p.relative_to(home)) for p, _ in changed]}, indent=2), encoding='utf-8')
        assert all(p.read_bytes() == data for p, data in files.items())
    print(f'{len(changed)} differences; ' + (f'installed, backup {backup}' if args.apply else 'check only'))
    return 0 if args.apply or not changed else 1


if __name__ == '__main__':
    sys.exit(main())
