#!/usr/bin/env bash
# Глобальная установка Claude Code: агенты, процесс, общие правила зон, инструкции и навык agentq из этого репозитория.
#   bash scripts/install-global.sh          — только сверка (ничего не меняет), код выхода 1 при расхождении
#   bash scripts/install-global.sh --apply  — копирует расходящиеся файлы; прежние версии сохраняются в ~/.claude/backups/install-<время>/
#   --core-only — не трогать личные инструкции владельца (~/.claude/CLAUDE.md и workflow/project-template.md); ставится только оркестратор.
# Версия пакета (VERSION) копируется в ~/.claude/skills/agentq/VERSION. Если установленная версия новее, установка отказывается
# (код 2), чтобы устаревший клон не откатил оркестратор; обойти осознанно: AQ_ALLOW_DOWNGRADE=1.
# Ничего не удаляет (лишние файлы в целевых папках остаются). Источник истины — репозиторий; установленные копии обязаны совпадать.
# Что требует НОВОЙ сессии Claude Code: agents/*.md (список субагентов читается при старте), CLAUDE.md, обновлённый SKILL.md.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}"
APPLY=0; CORE_ONLY=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --core-only) CORE_ONLY=1 ;;
    *) echo "неизвестный параметр: $a (допустимо: --apply, --core-only)" >&2; exit 2 ;;
  esac
done
STAMP="$(date +%Y%m%d-%H%M%S)"; BK="$DEST/backups/install-$STAMP"
diffs=0
VER="$(tr -d ' \r\n' < "$REPO/VERSION" 2>/dev/null)"
[ -n "$VER" ] || { echo "нет файла VERSION в репозитории" >&2; exit 2; }
INST_VER_FILE="$DEST/skills/agentq/VERSION"
if [ -f "$INST_VER_FILE" ] && [ "${AQ_ALLOW_DOWNGRADE:-0}" != 1 ]; then
  cur="$(tr -d ' \r\n' < "$INST_VER_FILE")"
  if [ -n "$cur" ] && [ "$cur" != "$VER" ] && [ "$(printf '%s\n%s\n' "$cur" "$VER" | sort -V | tail -n 1)" = "$cur" ]; then
    echo "Установлена более новая версия agentq ($cur), в репозитории $VER. Обновите клон (git pull --ff-only); принудительно: AQ_ALLOW_DOWNGRADE=1" >&2
    exit 2
  fi
fi
personal_overlay() {   # файлы, которыми владеет личный слой владельца (при --core-only не трогаем)
  case "$1" in "$DEST/CLAUDE.md"|"$DEST/workflow/project-template.md") return 0 ;; esac
  return 1
}
sync_file() {   # источник, назначение
  local src="$1" dst="$2"
  if [ "$CORE_ONLY" = 1 ] && personal_overlay "$dst"; then return; fi
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
sync_file "$REPO/VERSION" "$INST_VER_FILE"
if [ "$diffs" -eq 0 ]; then echo "Установленные копии совпадают с репозиторием."; exit 0; fi
if [ "$APPLY" = 1 ]; then echo "Обновлено файлов: $diffs (резервные копии: $BK). Новая сессия Claude Code нужна для agents/*.md, CLAUDE.md и SKILL.md."; exit 0; fi
echo "Расхождений: $diffs. Применить: bash scripts/install-global.sh --apply"; exit 1
