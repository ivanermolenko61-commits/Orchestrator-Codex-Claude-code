#!/usr/bin/env python3
"""Правила для внешних исполнителей (Gemini, OpenCode): запреты, конфиг OpenCode, проверка диффа.

Вся логика правил лежит только здесь. Конфиг OpenCode и проверка готовой ветки считаются
по одним и тем же спискам, поэтому расходиться не могут.

Как работают правила: они идут по порядку, побеждает ПОСЛЕДНЕЕ подошедшее (так устроен OpenCode).
Порядок для правки файлов:
    1. «* — можно»
    2. правила проекта из .agents/protected.json
    3. базовые запреты (проект их ослабить не может, они стоят последними)

Использование из командной строки:
    python agentsrules.py opencode-config --project C:\\Projects\\my-app
    python agentsrules.py check-diff --worktree C:\\Projects\\agent-worktrees\\my-app-t1 --project C:\\Projects\\my-app
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_RULES_FILE = Path(".agents") / "protected.json"


class RulesError(Exception):
    """Понятная ошибка в правилах или в проверке."""


# ---------------------------------------------------------------- базовые правила

# Чтение: всё можно, кроме секретов. .example разрешён после запретов на .env.
BASE_READ_RULES = [
    ("*", "allow"),
    ("*.env", "deny"),
    ("*.env.*", "deny"),
    ("*.pem", "deny"),
    ("*.key", "deny"),
    ("*.example", "allow"),
]

# Правка: эти запреты стоят в конце списка, поэтому проект их не отменит.
BASE_EDIT_RULES = [
    # секреты
    ("*.env", "deny"),
    ("*.env.*", "deny"),
    ("*.pem", "deny"),
    ("*.key", "deny"),
    # файлы правил и конфиги самих исполнителей
    ("*AGENTS.md", "deny"),
    ("*CLAUDE.md", "deny"),
    ("*GEMINI.md", "deny"),
    ("*opencode.json*", "deny"),
    ("*.opencode*", "deny"),
    ("*.gemini/*", "deny"),
    ("*.claude/*", "deny"),
    ("*.git/*", "deny"),
    # зависимости (установка новых пакетов — только по согласованию)
    ("*package.json", "deny"),
    ("*package-lock.json", "deny"),
    ("*yarn.lock", "deny"),
    ("*pnpm-lock.yaml", "deny"),
    ("*requirements*.txt", "deny"),
    ("*pyproject.toml", "deny"),
    ("*poetry.lock", "deny"),
    ("*Pipfile*", "deny"),
    ("*go.mod", "deny"),
    ("*go.sum", "deny"),
    ("*Cargo.toml", "deny"),
    ("*Cargo.lock", "deny"),
    ("*composer.json", "deny"),
    # деплой, контейнеры, CI
    ("*Dockerfile*", "deny"),
    ("*docker-compose*", "deny"),
    ("*.github/*", "deny"),
    ("*.gitlab-ci*", "deny"),
    ("*deploy/*", "deny"),
    ("*nginx*", "deny"),
    ("*certbot*", "deny"),
    # миграции и схема БД
    ("*alembic*", "deny"),
    ("*migrations/*", "deny"),
    # конфиги тестов и линтеров
    ("*conftest.py", "deny"),
    ("*pytest.ini", "deny"),
    ("*ruff.toml", "deny"),
    ("*eslint.config*", "deny"),
    ("*.eslintrc*", "deny"),
    ("*vite.config*", "deny"),
    ("*tsconfig*.json", "deny"),
    # память проекта: писать можно только отчёты
    ("*.agents/*", "deny"),
    ("*.agents/reports/*", "allow"),
]


# ---------------------------------------------------------------- маски и решения

def normalize(path):
    """Путь с прямыми слэшами, без ./ в начале."""
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def glob_match(pattern, path):
    """Маска, где * — любые символы (в том числе /). Регистр не важен (Windows)."""
    pieces = [re.escape(part) for part in normalize(pattern).split("*")]
    regex = ".*".join(pieces)
    return re.fullmatch(regex, normalize(path), re.IGNORECASE | re.DOTALL) is not None


def decide(rules, path):
    """Решение по пути: побеждает последнее подошедшее правило."""
    action = None
    for pattern, rule_action in rules:
        if glob_match(pattern, path):
            action = rule_action
    return action


# ---------------------------------------------------------------- правила проекта

def parse_rule(text, kind):
    match = re.fullmatch(r"\s*(allow|deny)\s+(\S.*?)\s*", str(text))
    if not match:
        raise RulesError(f"Неверное правило {text!r}: нужен вид 'allow <маска>' или 'deny <маска>'")
    action, pattern = match.groups()
    if kind == "read" and action == "allow":
        raise RulesError(f"В разделе read разрешены только deny, а не {text!r}")
    return normalize(pattern), action


def load_project_rules(project_dir):
    """Читает .agents/protected.json проекта. Нет файла — только базовые правила."""
    rules_file = Path(project_dir) / PROJECT_RULES_FILE
    result = {"read": [], "edit": []}
    if not rules_file.exists():
        return result
    try:
        data = json.loads(rules_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RulesError(f"{rules_file}: не разобрать JSON ({error})") from error
    unknown = set(data) - {"read", "edit"}
    if unknown:
        raise RulesError(f"{rules_file}: неизвестные ключи {sorted(unknown)}, разрешены read и edit")
    for kind in ("read", "edit"):
        for text in data.get(kind, []):
            result[kind].append(parse_rule(text, kind))
    return result


def build_rules(project_dir):
    project = load_project_rules(project_dir)
    return {
        "read": BASE_READ_RULES + project["read"],
        "edit": [("*", "allow")] + project["edit"] + BASE_EDIT_RULES,
    }


# ---------------------------------------------------------------- конфиг OpenCode

def _variants(pattern):
    """Маска в двух видах: со слэшем и с обратным слэшем (OpenCode на Windows видит оба)."""
    forward = normalize(pattern)
    backward = forward.replace("/", "\\")
    return [forward] if forward == backward else [forward, backward]


def _to_permission_object(rules):
    """Упорядоченный словарь для OpenCode. Повтор маски переносит правило в конец (позднее главнее)."""
    result = {}
    for pattern, action in rules:
        for variant in _variants(pattern):
            result.pop(variant, None)
            result[variant] = action
    return result


def opencode_config(rules):
    return {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "*": "deny",
            "read": _to_permission_object(rules["read"]),
            "glob": "allow",
            "grep": "deny",  # grep с include=".env" отдаёт содержимое .env даже из .gitignore (опыт 05.10.2026)
            "list": "allow",
            "todowrite": "allow",
            "edit": _to_permission_object(rules["edit"]),
            "bash": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "external_directory": {"*": "deny"},
            "task": "deny",
            "skill": "deny",
            "doom_loop": "deny",
        },
    }


# ---------------------------------------------------------------- проверка диффа

def _git(worktree, *args):
    result = subprocess.run(
        ["git", "-C", str(worktree), "-c", "core.quotepath=false", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RulesError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def collect_changes(worktree, base):
    """Что изменено в ветке относительно base и в рабочей папке: {путь: {статусы}}.

    Статусы: A (добавлен), M (изменён), D (удалён), R (переименован: старый путь считается удалённым).
    """
    changes = {}

    def add(path, status):
        changes.setdefault(normalize(path), set()).add(status)

    # 1. Закоммиченное: git diff --name-status -z base...HEAD
    tokens = _git(worktree, "diff", "--name-status", "-M", "-z", f"{base}...HEAD").split("\0")
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index][0]
        if status in "RC":
            old, new = tokens[index + 1], tokens[index + 2]
            index += 3
            add(old, "D" if status == "R" else "M")
            add(new, "A")
        else:
            add(tokens[index + 1], status)
            index += 2

    # 2. Незакоммиченное и новые файлы: git status --porcelain -z
    entries = _git(worktree, "status", "--porcelain=v1", "--untracked-files=all", "-z").split("\0")
    index = 0
    while index < len(entries) and entries[index]:
        entry = entries[index]
        flags, path = entry[:2], entry[3:]
        index += 1
        if "R" in flags or "C" in flags:
            old_path = entries[index]
            index += 1
            add(old_path, "D" if "R" in flags else "M")
            add(path, "A")
        elif "D" in flags:
            add(path, "D")
        elif flags == "??" or "A" in flags:
            add(path, "A")
        else:
            add(path, "M")
    return changes


def path_in_allowed(path, allowed):
    """Файл разрешён задачей: точное совпадение, папка (заканчивается на /) или маска с *."""
    path = normalize(path)
    for item in allowed:
        item = normalize(item)
        if "*" in item:
            if glob_match(item, path):
                return True
        elif item.endswith("/"):
            if path.startswith(item):
                return True
        elif path == item:
            return True
    return False


def check_diff(worktree, rules, base="main", allowed=None):
    """Проверка готовой работы исполнителя. Возвращает {'changes': [...], 'violations': [...]}."""
    changes = collect_changes(worktree, base)
    report = []
    violations = []
    for path in sorted(changes):
        statuses = changes[path]
        problems = []
        if "D" in statuses:
            problems.append("удаление или переименование файла запрещено")
        if "D" not in statuses or statuses - {"D"}:
            # файл существует в итоге: проверяем зону
            if decide(rules["edit"], path) == "deny":
                problems.append("защищённая зона")
            is_report = glob_match("*.agents/reports/*", path)
            if allowed is not None and not is_report and not path_in_allowed(path, allowed):
                problems.append("файл не из списка задачи")
        report.append({"path": path, "status": "".join(sorted(statuses)), "problems": problems})
        for problem in problems:
            violations.append(f"{path}: {problem}")
    return {"changes": report, "violations": violations}


# ---------------------------------------------------------------- командная строка

def _cmd_opencode_config(args):
    rules = build_rules(args.project)
    print(json.dumps(opencode_config(rules), ensure_ascii=False, indent=2))
    return 0


def _cmd_check_diff(args):
    rules = build_rules(args.project)
    result = check_diff(args.worktree, rules, base=args.base, allowed=args.allowed or None)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if not result["changes"]:
            print("Изменений нет.")
        for item in result["changes"]:
            mark = "НАРУШЕНИЕ" if item["problems"] else "ок"
            extra = f" ({'; '.join(item['problems'])})" if item["problems"] else ""
            print(f"[{item['status']}] {item['path']} — {mark}{extra}")
        print("Итог:", "нарушений нет" if not result["violations"] else f"нарушений: {len(result['violations'])}")
    return 3 if result["violations"] else 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("opencode-config", help="напечатать конфиг прав OpenCode для проекта")
    p_config.add_argument("--project", required=True, help="папка основного проекта")
    p_config.set_defaults(func=_cmd_opencode_config)

    p_diff = sub.add_parser("check-diff", help="проверить изменения в рабочей копии исполнителя")
    p_diff.add_argument("--worktree", required=True)
    p_diff.add_argument("--project", required=True, help="папка основного проекта (откуда берутся правила)")
    p_diff.add_argument("--base", default="main")
    p_diff.add_argument("--allowed", action="append", help="файл или папка из задачи (можно несколько раз)")
    p_diff.add_argument("--json", action="store_true")
    p_diff.set_defaults(func=_cmd_check_diff)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RulesError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
