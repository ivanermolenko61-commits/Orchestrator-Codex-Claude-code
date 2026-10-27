#!/usr/bin/env bash
# Точка входа глобального /agentq (CR). Вся логика — в agentq_global.py (один движок, одно состояние в .ai/).
#
#   codex-review.sh route                      — какой маршрут действует и чего не хватает (без вызовов моделей)
#   codex-review.sh start "<цель>" [--kind backend|mixed] [--id ID] [--contract-file F] [--plan-approved]
#   codex-review.sh unit <uid> --kind backend|frontend|protected --files F... [--context F...] [--after U...]
#   codex-review.sh cycle <uid> [low|medium|high]    — frontend: автор Codex → проверки → ревью Claude → исправления
#   codex-review.sh review [uid] [low|medium|high] [--full]   — независимое ревью другим сервисом
#   codex-review.sh integrate <uid> | status | finish
#   codex-review.sh rethink <uid> "<причина и более простой подход>"
#                                            — после остановки за 2 круга без прогресса: пересмотр подхода (один раз на единицу)
#   codex-review.sh start … --max-calls N    — жёсткий предел вызовов цепочки (только по решению Ивана)
#
# Коды выхода: 0 PASS/ok, 1 FIX, 2 ERROR/блокер, 3 LIMIT, 4 BUSY; route: 0 готово, 10/12 проектный координатор, 11 не хватает настроек.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for py in "py -3" python3 python; do
  if $py -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    exec $py "$HERE/agentq_global.py" "$@"
  fi
done
echo "ERROR: нужен Python 3.9+ (py -3, python3 или python)" >&2
exit 2
