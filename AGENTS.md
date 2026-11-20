# AGENTS.md — Orchestrator-Codex-Claude-code

Единственный источник оркестратора agentq (движок, навык, роли, установщик). Установка и дальнейшие изменения идут отсюда; база знаний ai-knowledge не хранит и не устанавливает эти файлы.

- Режим проекта: рабочий

## Роли и зоны

- Тимлид — текущий чат Codex или Qwen. Оба сервиса пишут любой код; reviewer — другой сервис, назначения закрепляются за единицей.
- Claude Code на паузе: не запускать, настройки и историю сохранять.
- Принятый движок `skills/agentq/agentq_global.py`, `codex-review.sh`, `selftest.py` не менять без отдельной приёмки (независимая процедура вне самого движка). Неизменённый принятый движок повторно на модельное ревью не отправлять.

## Защищённые зоны

- `skills/agentq/*`, установщики, `package-manifest.json`, `VERSION`, `.agents/agentq.json`: правит тимлид; исполняемую логику независимо принимает другой сервис вне изменяемого движка. Для этой интеграции автор Codex, reviewer Qwen.
- Базовые запреты новой пары задаёт `~/.agents/skills/agentq/agentsrules.py` (источник `global/scripts/agentsrules.py`); `~/.claude/scripts/agentsrules.py` относится к исторической установке.

## Проверки

- `bash -n scripts/install-global.sh`
- `bash scripts/test-install-global.sh` (установка во временный `CLAUDE_HOME`, сверка манифеста)
- `python scripts/test-install-pair.py` и `python skills/agentq/selftest.py` (офлайн).
- Выпуск: установка в изолированный каталог и `selftest.py` (офлайн), см. README.

## Публикация

Push, слияние в main и обновление рабочей установки `~/.claude` — только по подтверждению владельца.
