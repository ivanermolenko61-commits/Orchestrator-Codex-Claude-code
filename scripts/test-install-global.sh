#!/usr/bin/env bash
# Регрессия scripts/install-global.sh на временном CLAUDE_HOME (реальная домашняя папка не затрагивается):
# сверка видит отсутствие файлов, --apply ставит и делает резервные копии, лишние файлы не удаляются, различие только в CRLF допускается.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)" || { echo "FAIL  не удалось создать временный каталог"; exit 1; }
[ -n "$T" ] && [ -d "$T" ] || exit 1
trap 'rm -rf "$T"' EXIT
export CLAUDE_HOME="$T/claude"
fail=0
ok() { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fail=1; }
run() { bash "$REPO/scripts/install-global.sh" "$@" >"$T/out.txt" 2>&1; echo $?; }

mkdir -p "$CLAUDE_HOME/agents"
echo "собственный агент пользователя" > "$CLAUDE_HOME/agents/extra.md"

[ "$(run)" = 1 ] && grep -q '^нет:' "$T/out.txt" && ok "сверка пустой установки: код 1 и «нет:»" || bad "сверка пустой установки"
[ ! -e "$CLAUDE_HOME/agents/coder.md" ] && ok "сверка ничего не меняет" || bad "сверка изменила файлы"

[ "$(run --apply)" = 0 ] && ok "--apply: код 0" || bad "--apply: код"
diff -q "$REPO/global/agents/coder.md" "$CLAUDE_HOME/agents/coder.md" >/dev/null && ok "--apply: агент установлен" || bad "--apply: агент не совпадает"
[ -f "$CLAUDE_HOME/skills/agentq/agentq_global.py" ] && ok "--apply: навык agentq установлен" || bad "--apply: нет навыка agentq"
[ -f "$CLAUDE_HOME/agents/extra.md" ] && ok "лишний файл пользователя не удалён" || bad "лишний файл удалён"

[ "$(run)" = 0 ] && grep -q 'совпадают' "$T/out.txt" && ok "повторная сверка: совпадает" || bad "повторная сверка"

echo "правка пользователя" >> "$CLAUDE_HOME/agents/coder.md"
[ "$(run)" = 1 ] && grep -q '^отличается:' "$T/out.txt" && ok "изменённая копия обнаружена" || bad "изменённая копия не обнаружена"
[ "$(run --apply)" = 0 ] && ok "--apply восстанавливает" || bad "--apply не восстановил"
diff -q "$REPO/global/agents/coder.md" "$CLAUDE_HOME/agents/coder.md" >/dev/null && ok "копия снова равна источнику" || bad "копия не равна источнику"
bk="$(find "$CLAUDE_HOME/backups" -path '*agents/coder.md' 2>/dev/null | head -n 1)"
{ [ -n "$bk" ] && grep -q 'правка пользователя' "$bk"; } && ok "прежняя версия сохранена в backups" || bad "резервная копия не создана"

sed -i 's/$/\r/' "$CLAUDE_HOME/agents/coder.md"
[ "$(run)" = 0 ] && ok "различие только в CRLF допускается" || bad "CRLF считается расхождением"

exit "$fail"
