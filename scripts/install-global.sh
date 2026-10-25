#!/usr/bin/env bash
# Глобальная установка Claude Code: агенты, процесс, общие правила зон, инструкции и навык agentq из этого репозитория.
#   bash scripts/install-global.sh          — только сверка (ничего не меняет), код выхода 1 при расхождении
#   bash scripts/install-global.sh --apply  — копирует расходящиеся файлы; прежние версии сохраняются в ~/.claude/backups/install-<время>/
# Ничего не удаляет (лишние файлы в целевых папках остаются). Источник истины — репозиторий; установленные копии обязаны совпадать.
# Что требует НОВОЙ сессии Claude Code: agents/*.md (список субагентов читается при старте), CLAUDE.md, обновлённый SKILL.md.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}"
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
STAMP="$(date +%Y%m%d-%H%M%S)"; BK="$DEST/backups/install-$STAMP"
diffs=0
sync_file() {   # источник, назначение
  local src="$1" dst="$2"
  if [ -f "$dst" ] && diff -q --strip-trailing-cr "$src" "$dst" >/dev/null 2>&1; then return; fi
  diffs=$((diffs+1))
  if [ -f "$dst" ]; then echo "отличается: $dst"; else echo "нет:         $dst"; fi
  if [ "$APPLY" = 1 ]; then
    if [ -f "$dst" ]; then mkdir -p "$BK/$(dirname "${dst#$DEST/}")"; cp -p "$dst" "$BK/${dst#$DEST/}"; fi
    mkdir -p "$(dirname "$dst")"; cp "$src" "$dst"
  fi
}
sync_tree() {   # папка источника, папка назначения
  local s="$1" d="$2" f rel
  while IFS= read -r -d '' f; do
    rel="${f#$s/}"; sync_file "$f" "$d/$rel"
  done < <(find "$s" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0)
}
sync_tree "$REPO/global/agents"   "$DEST/agents"
sync_tree "$REPO/global/workflow" "$DEST/workflow"
sync_tree "$REPO/global/scripts"  "$DEST/scripts"
sync_file "$REPO/global/CLAUDE.md" "$DEST/CLAUDE.md"
for f in SKILL.md agentq_global.py codex-review.sh selftest.py; do sync_file "$REPO/skills/agentq/$f" "$DEST/skills/agentq/$f"; done
if [ "$diffs" -eq 0 ]; then echo "Установленные копии совпадают с репозиторием."; exit 0; fi
if [ "$APPLY" = 1 ]; then echo "Обновлено файлов: $diffs (резервные копии: $BK). Новая сессия Claude Code нужна для agents/*.md, CLAUDE.md и SKILL.md."; exit 0; fi
echo "Расхождений: $diffs. Применить: bash scripts/install-global.sh --apply"; exit 1
