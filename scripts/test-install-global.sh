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

# версия пакета устанавливается рядом с навыком
diff -q --strip-trailing-cr "$REPO/VERSION" "$CLAUDE_HOME/skills/agentq/VERSION" >/dev/null && ok "VERSION установлена" || bad "VERSION не установлена"

# защита от отката: более новая установленная версия не перезаписывается
echo "9.0.0" > "$CLAUDE_HOME/skills/agentq/VERSION"
echo "правка при откате" >> "$CLAUDE_HOME/agents/coder.md"
[ "$(run --apply)" = 2 ] && ok "откат версии отклонён (код 2)" || bad "откат версии не отклонён"
grep -q 'правка при откате' "$CLAUDE_HOME/agents/coder.md" && ok "при отказе файлы не менялись" || bad "при отказе файлы изменены"
[ "$(AQ_ALLOW_DOWNGRADE=1 run --apply)" = 0 ] && ok "AQ_ALLOW_DOWNGRADE=1 разрешает установку" || bad "AQ_ALLOW_DOWNGRADE не сработал"
diff -q --strip-trailing-cr "$REPO/VERSION" "$CLAUDE_HOME/skills/agentq/VERSION" >/dev/null && ok "версия восстановлена" || bad "версия не восстановлена"

# окончания строк CRLF в установленном VERSION не должны влиять на сравнение версий
printf '%s\r\n' "$(tr -d ' \r\n' < "$REPO/VERSION")" > "$CLAUDE_HOME/skills/agentq/VERSION"
[ "$(run)" = 0 ] && ok "VERSION с CRLF: та же версия не считается откатом" || bad "VERSION с CRLF: ложный отказ"
printf '9.0.0\r\n' > "$CLAUDE_HOME/skills/agentq/VERSION"
[ "$(run)" = 2 ] && ok "VERSION с CRLF: более новая версия отклоняется" || bad "VERSION с CRLF: откат не отклонён"
[ "$(AQ_ALLOW_DOWNGRADE=1 run --apply)" = 0 ] && ok "после CRLF-проверок версия восстановлена" || bad "восстановление версии после CRLF-проверок"

# неизвестный параметр не должен молча превращаться в сверку
[ "$(run --aply)" = 2 ] && ok "неизвестный параметр: код 2" || bad "неизвестный параметр принят"

# --core-only: личные инструкции владельца не трогаются, оркестратор ставится
H2="$T/claude-core"
mkdir -p "$H2/workflow"
echo "личные правила" > "$H2/CLAUDE.md"
echo "личный шаблон" > "$H2/workflow/project-template.md"
[ "$(CLAUDE_HOME="$H2" run --apply --core-only)" = 0 ] && ok "--core-only: код 0" || bad "--core-only: код"
grep -qx 'личные правила' "$H2/CLAUDE.md" && grep -qx 'личный шаблон' "$H2/workflow/project-template.md" && ok "--core-only: личные файлы не изменены" || bad "--core-only: личные файлы изменены"
{ [ -f "$H2/skills/agentq/agentq_global.py" ] && [ -f "$H2/agents/coder.md" ] && [ -f "$H2/scripts/agentsrules.py" ]; } && ok "--core-only: оркестратор установлен" || bad "--core-only: оркестратор не установлен"
[ "$(CLAUDE_HOME="$H2" run --core-only)" = 0 ] && ok "--core-only: сверка совпадает" || bad "--core-only: сверка"
[ "$(CLAUDE_HOME="$H2" run)" = 1 ] && ok "без --core-only личные файлы считаются расхождением" || bad "без --core-only расхождения не видно"

# манифест пакета (нужен Python)
PY=""; for c in python3 python; do command -v "$c" >/dev/null && "$c" -c "import json" >/dev/null 2>&1 && { PY="$c"; break; }; done
if [ -n "$PY" ]; then
  "$PY" "$REPO/scripts/manifest.py" check >"$T/manifest.txt" 2>&1 && ok "манифест соответствует файлам" || { bad "манифест не соответствует файлам"; cat "$T/manifest.txt"; }
else
  echo "SKIP  манифест: нет Python"
fi

exit "$fail"
