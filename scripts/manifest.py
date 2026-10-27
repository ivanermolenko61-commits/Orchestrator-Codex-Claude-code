#!/usr/bin/env python3
"""Манифест пакета: контрольные суммы файлов репозитория.

  python scripts/manifest.py write   — пересчитать files в package-manifest.json (остальные поля сохраняются)
  python scripts/manifest.py check   — сверить файлы с манифестом; код 1 при расхождении

Хэши считаются по содержимому с LF (CRLF после клонирования на Windows не мешает).
Поле provenance фиксирует файлы, взятые из принятого источника: для них check сверяет git blob id.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'package-manifest.json'
SKIP_DIRS = {'.git', '.ai', '__pycache__'}


def tracked_files():
    out = []
    for p in sorted(ROOT.rglob('*')):
        rel = p.relative_to(ROOT)
        if not p.is_file() or SKIP_DIRS & set(rel.parts) or p.suffix == '.pyc' or p == MANIFEST:
            continue
        out.append(rel.as_posix())
    return out


def digest(rel):
    data = (ROOT / rel).read_bytes().replace(b'\r\n', b'\n')
    return {'sha256': hashlib.sha256(data).hexdigest(),
            'git_blob': hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()}


def load():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def problems(manifest):
    bad = []
    have = tracked_files()
    listed = manifest.get('files', {})
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if manifest.get('version') != version:
        bad.append(f"версия манифеста {manifest.get('version')} != VERSION {version}")
    for rel in sorted(set(have) - set(listed)):
        bad.append(f'нет в манифесте: {rel}')
    for rel in sorted(set(listed) - set(have)):
        bad.append(f'нет файла: {rel}')
    for rel in sorted(set(have) & set(listed)):
        if digest(rel) != listed[rel]:
            bad.append(f'не совпадает: {rel}')
    for rel, src in manifest.get('provenance', {}).items():
        if src.get('unchanged_from_source') and rel in have and digest(rel)['git_blob'] != src.get('source_blob'):
            bad.append(f'отличается от принятого источника: {rel}')
    return bad


def main(argv):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if len(argv) != 2 or argv[1] not in ('write', 'check'):
        print(__doc__)
        return 2
    manifest = load()
    if argv[1] == 'write':
        manifest['version'] = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        manifest['files'] = {rel: digest(rel) for rel in tracked_files()}
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        print(f"манифест обновлён: {len(manifest['files'])} файлов")
    bad = problems(load())
    for line in bad:
        print(line)
    print('манифест: OK' if not bad else f'манифест: расхождений {len(bad)}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
