#!/usr/bin/env python3
"""Офлайн-самопроверка глобального /agentq: поддельные Codex и Claude, временные git-проекты, ни одного живого вызова.

    py -3 selftest.py

Проверяет: оба направления, возврат FIX автору, лимиты, восстановление без повторного вызова, отсутствие двойного маршрута,
роли и владение файлами, защищённые зоны, учебный режим, недоступность сервиса без подмены автора.
Каталоги проверок создаются во временной папке и не удаляются (сообщение в конце).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE / 'agentq_global.py'

FAKE_CODEX = r'''
import json, os, sys, time
from pathlib import Path
d = Path(__file__).resolve().parent
a = sys.argv[1:]
if a[:2] == ['login', 'status']:
    print('Logged in using ChatGPT'); sys.exit(0 if not (d / 'nologin').exists() else 1)
if a[:2] == ['features', 'list']:
    for n in ['shell_tool', 'unified_exec', 'apps', 'hooks', 'goals']:
        print(n + '  stable  true')
    sys.exit(0)
prompt = sys.stdin.read()
author = '--output-schema' in a
out = a[a.index('-o') + 1] if '-o' in a else a[a.index('--output-last-message') + 1]
def claim(prefix):   # атомарный счётчик: вызовы могут идти одновременно
    i = 0
    while True:
        try:
            (d / f'{prefix}.claim-{i}').open('x').close(); return i
        except FileExistsError:
            i += 1
n = claim('codex')
role = 'author' if author else 'review'
with (d / 'calls.log').open('a') as f:
    f.write(('codex-author' if author else 'codex-review') + ' model=' + a[a.index('-m') + 1] + ' tools_off=' + str('shell_tool' in a) + '\n')
(d / f'codex-prompt-{n}.txt').write_text(prompt, encoding='utf-8')
items = json.loads((d / 'codex.json').read_text())
# сценарии с ключом role выбираются по роли (порядок старта параллельных вызовов не определён), остальные — по номеру вызова
sc = [e for e in items if e.get('role') == role][claim('codex-' + role)] if any('role' in e for e in items) else items[n]
with (d / 'timeline.log').open('a') as f:
    f.write(f'{time.time()} start codex-{role}\n')
try:
    if sc.get('sleep'): time.sleep(sc['sleep'])
finally:
    with (d / 'timeline.log').open('a') as f:
        f.write(f'{time.time()} end codex-{role}\n')
if sc.get('fail'): print('rate limit 429', file=sys.stderr); sys.exit(1)
if author:
    if sc.get('tool'):
        print(json.dumps({'item': {'type': 'command_execution'}}))
    print(json.dumps({'item': {'type': 'agent_message'}}))
    Path(out).write_text(json.dumps({'files': [{'path': p, 'content': c} for p, c in sc['files'].items()],
                                     'notes': sc.get('notes', 'ok'), 'unmet_requirements': sc.get('unmet', [])}))
else:
    Path(out).write_text(sc['text'], encoding='utf-8')
'''

FAKE_CLAUDE = r'''
import json, os, sys
from pathlib import Path
d = Path(__file__).resolve().parent
a = sys.argv[1:]
if a[:2] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai'})); sys.exit(0)
prompt = sys.stdin.read()
n = 0
while True:
    try:
        (d / f'claude.claim-{n}').open('x').close(); break
    except FileExistsError:
        n += 1
with (d / 'calls.log').open('a') as f:
    f.write('claude-review model=' + a[a.index('--model') + 1] + ' tools=' + repr(a[a.index('--tools') + 1]) + ' nosession=' + str('--no-session-persistence' in a) + '\n')
(d / f'claude-prompt-{n}.txt').write_text(prompt, encoding='utf-8')
sc = json.loads((d / 'claude.json').read_text())[n]
print(json.dumps({'is_error': False, 'subtype': 'success', 'structured_output': sc}))
'''

FAKE_DOCKER = r"""
import os, sys, time
from pathlib import Path
d = Path(__file__).resolve().parent
a = sys.argv[1:]
with (d / 'docker.log').open('a', encoding='utf-8') as f:
    f.write(' '.join(a) + '\n')
if a[:1] == ['info']:
    sys.exit(1 if (d / 'nodocker').exists() else 0)
if a[:2] == ['image', 'inspect']:
    if (d / 'noimage').exists():
        sys.exit(1)
    print('sha256:fakeimage'); sys.exit(0)
if a[:1] == ['rm']:
    sys.exit(0)
if a[:1] == ['run']:
    mount = next(x for x in a if x.startswith('type=bind'))
    src = Path(dict(kv.split('=', 1) for kv in mount.split(',') if '=' in kv)['source'])
    cmd = a[a.index('aqrun') + 1:]
    (d / 'last-src-list.txt').write_text('\n'.join(sorted(str(q.relative_to(src)).replace(os.sep, '/') for q in src.rglob('*'))), encoding='utf-8')
    for q in src.rglob('*'):
        if q.is_file() and ('FLOOD_' + cmd[0]) in q.read_text(encoding='utf-8', errors='replace'):
            for _ in range(400):
                sys.stdout.write('x' * 50000 + chr(10))
        if q.is_file() and ('FAIL_' + cmd[0]) in q.read_text(encoding='utf-8', errors='replace'):
            print('FAILED ' + cmd[0] + ': ' + q.name); sys.exit(1)
    print('ok ' + cmd[0]); sys.exit(0)
sys.exit(2)
"""

DEFAULT_CFG = {'checks': {'backend': [['test']], 'frontend': [['test']]}, 'check_image': 'aq-fake:1'}

APP_OLD = "export function App() {\n  const items = [1, 2, 3];\n  return (\n    <ul>\n      {items.map((i) => (\n        <li key={i}>{i}</li>\n      ))}\n    </ul>\n  );\n}\n"
APP_V1 = APP_OLD.replace("<li key={i}>{i}</li>", "<li key={i} className=\"item\">{i}</li>")
APP_V2 = APP_V1.replace("const items = [1, 2, 3];", "const items = [1, 2, 3, 4];")
TEST_OLD = "test('a', () => {});\ntest('b', () => {});\n"
TEST_V1 = TEST_OLD + "test('c', () => {});\n"
FILES_OK1 = {'frontend/src/App.tsx': APP_V1, 'frontend/src/App.test.tsx': TEST_V1}
FILES_OK2 = {'frontend/src/App.tsx': APP_V2, 'frontend/src/App.test.tsx': TEST_V1}
FE_FILES = ['frontend/src/App.tsx', 'frontend/src/App.test.tsx']
OK = {'verdict': 'PASS', 'summary': 'ok', 'findings': []}
FIXV = {'verdict': 'FIX', 'summary': 'нужно исправить',
        'findings': [{'severity': 'MAJOR', 'location': 'frontend/src/App.tsx:6', 'problem': 'нет обработки пустого списка.', 'fix': 'добавить заглушку'}]}
UNC = {'verdict': 'UNCERTAIN', 'summary': 'не могу подтвердить', 'findings': []}

results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else f'  <-- {detail}'), flush=True)


class Proj:
    def __init__(self, label, mode='рабочий', extra=None, config=DEFAULT_CFG):
        self.root = Path(tempfile.mkdtemp(prefix=f'aq-selftest-{label}-'))
        self.repo = self.root / 'proj'
        self.fake = self.root / 'fake'
        self.wt = self.root / 'wt'
        for d in (self.repo, self.fake, self.wt):
            d.mkdir(parents=True)
        (self.fake / 'fake_codex.py').write_text(FAKE_CODEX, encoding='utf-8')
        (self.fake / 'fake_claude.py').write_text(FAKE_CLAUDE, encoding='utf-8')
        (self.fake / 'fake_docker.py').write_text(FAKE_DOCKER, encoding='utf-8')
        files = {'AGENTS.md': f'# card\n- Режим проекта: {mode}\n', 'backend/app.py': 'def add(a, b):\n    return a + b\n',
                 'frontend/src/App.tsx': APP_OLD, 'frontend/src/App.test.tsx': TEST_OLD, 'package.json': '{}\n'}
        files.update(extra or {})
        if config:
            files['.agents/agentq.json'] = json.dumps(config)
        for p, c in files.items():
            f = self.repo / p
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open('w', encoding='utf-8', newline='\n') as fh:   # write_text(newline=) есть только с Python 3.10
                fh.write(c)
        self.git('init', '-q', '-b', 'aq/test')
        self.git('config', 'user.name', 't')
        self.git('config', 'user.email', 't@t')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'init')
        self.env = dict(os.environ)
        self.env.update(AQ_WORKTREES=str(self.wt), FAKE_DIR=str(self.fake),
                        CODEX_BIN=json.dumps([sys.executable, str(self.fake / 'fake_codex.py')]),
                        CLAUDE_BIN=json.dumps([sys.executable, str(self.fake / 'fake_claude.py')]),
                        DOCKER_BIN=json.dumps([sys.executable, str(self.fake / 'fake_docker.py')]))

    def git(self, *a):
        return subprocess.run(['git', '-C', str(self.repo), *a], capture_output=True, text=True, encoding='utf-8').stdout.strip()

    def commit(self, path, text, msg='change'):
        f = self.repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding='utf-8', newline='\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', msg)
        return self.git('rev-parse', 'HEAD')

    def cr(self, *args, env=None):
        e = dict(self.env)
        e.update(env or {})
        r = subprocess.run([sys.executable, str(ENGINE), *map(str, args)], cwd=self.repo, env=e, capture_output=True,
                           text=True, encoding='utf-8', timeout=180)
        return r.returncode, r.stdout, r.stderr

    def script(self, who, items):
        (self.fake / f'{who}.json').write_text(json.dumps(items), encoding='utf-8')

    def calls(self):
        p = self.fake / 'calls.log'
        return p.read_text(encoding='utf-8').splitlines() if p.exists() else []

    def count(self, prefix):
        return sum(1 for c in self.calls() if c.startswith(prefix))

    def contract(self):
        f = self.root / 'contract.txt'
        f.write_text('GET /items -> [{id:int}]; ошибки 404; права: любой пользователь.', encoding='utf-8')
        return f


def load_engine():
    spec = importlib.util.spec_from_file_location('aq_engine', ENGINE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['aq_engine'] = mod
    spec.loader.exec_module(mod)
    return mod


def t_route():
    p = Proj('route')
    rc, out, _ = p.cr('route')
    check('route: проект без координатора → глобальный маршрут (0)', rc == 0 and 'ROUTE=global' in out, out)
    q = Proj('route-project', extra={'scripts/agentq.py': "p.add_argument('action', choices=['submit', 'run', 'review', 'rereview'])\n",
                                     '.claude/commands/agentq-codegen.md': 'x\n'})
    rc, out, _ = q.cr('route')
    check('route: проект со своим координатором → 10, CR не запускается', rc == 10 and 'ROUTE=project' in out, out)
    rc, _, err = q.cr('start', 'задача под координатором')
    check('двойной маршрут: start поверх проектного координатора отказан', rc == 10 and not (q.repo / '.ai/task/state.json').exists(), err)
    o = Proj('route-old', extra={'scripts/agentq.py': "p.add_argument('action', choices=['submit', 'run'])\n",
                                 '.claude/commands/agentq-codegen.md': 'x\n'})
    rc, out, _ = o.cr('route')
    check('route: координатор без review/rereview → 12 и явное «MISSING», без молчаливого перехода', rc == 12 and 'MISSING' in out, out)
    rc, _, _ = o.cr('start', 'x задача')
    check('start при устаревшем координаторе отказан (12)', rc == 12)
    m = Proj('route-missing')
    rc, out, _ = m.cr('route', env={'CODEX_BIN': json.dumps(['nonexistent-codex-bin-xyz'])})
    check('route: Codex недоступен → 11 и перечень недостающего', rc == 11 and 'MISSING' in out, out)
    (m.fake / 'nologin').write_text('1')
    rc, out, _ = m.cr('route')
    check('route: Codex без входа → 11', rc == 11 and 'авторизован' in out, out)


def t_backend():
    p = Proj('backend')
    check('backend: start', p.cr('start', 'бэкенд: сложение', '--id', 'be-add')[0] == 0)
    p.commit('backend/app.py', 'def add(a, b):\n    return a + b + 0\n')
    p.script('codex', [{'text': '- [MAJOR] backend/app.py:2 — лишний ноль. Как исправить: убрать.\nVERDICT: FIX'},
                       {'text': 'Замечаний нет.\nVERDICT: PASS'}])
    rc, out, _ = p.cr('review')
    check('backend: автор Claude → ревью Codex, FIX (1)', rc == 1 and p.count('codex-review model=gpt-6.1-sol') == 1 and p.count('claude') == 0, p.calls())
    p.commit('backend/app.py', 'def add(a, b):\n    return a + b\n', 'fix')
    rc, out, _ = p.cr('review')
    prompt = (p.fake / 'codex-prompt-1.txt').read_text(encoding='utf-8')
    check('backend: исправление → повторное ревью PASS (0), замечания переданы в запрос', rc == 0 and 'лишний ноль' in prompt, out)
    rc, _, _ = p.cr('review')
    check('backend: повторное review того же SHA не вызывает модель', rc == 0 and p.count('codex-review') == 2)
    rc, out, _ = p.cr('finish')
    check('backend: finish подтверждает PASS на текущем SHA и выполненные в контейнере проверки', rc == 0 and 'ACCEPTANCE: VERIFIED' in out and 'проверки в контейнере выполнены' in out, out)
    rc, _, err = p.cr('start', 'бэкенд: сложение', '--id', 'be-add-2')
    check('после принятой (PASS) работы новая задача допускается', rc == 0, err)


def t_role_rules():
    p = Proj('roles')
    p.cr('start', 'роли', '--id', 'roles-1')
    p.commit('frontend/src/App.tsx', APP_V1)
    p.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, _, err = p.cr('review')
    check('Claude не пишет клиентский код: ревью отказано до вызова моделей', rc == 2 and 'РОЛЬ' in err and not p.calls(), err)
    q = Proj('roles2')
    q.cr('start', 'смешанная', '--id', 'mixed-1', '--kind', 'mixed', '--contract-file', q.contract())
    rc, _, err = q.cr('unit', 'be', '--kind', 'backend', '--files', 'frontend/src/App.tsx')
    check('клиентский файл нельзя отдать backend-единице', rc == 2, err)
    rc, _, err = q.cr('unit', 'fe', '--kind', 'frontend', '--files', 'package.json')
    check('защищённый файл нельзя передать Codex', rc == 2 and 'защищ' in err.lower(), err)
    rc, _, err = q.cr('unit', 'fe', '--kind', 'frontend', '--files', 'backend/app.py')
    check('серверный файл нельзя передать Codex-автору фронтенда', rc == 2, err)
    rc, _, err = q.cr('unit', 'pr', '--kind', 'protected', '--files', 'backend/app.py')
    check('protected допустим только для защищённых зон', rc == 2, err)
    check('protected: package.json принят (тимлид, ревью Codex)', q.cr('unit', 'pr', '--kind', 'protected', '--files', 'package.json')[0] == 0)
    check('frontend-единица создана', q.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)[0] == 0)
    rc, _, err = q.cr('unit', 'fe2', '--kind', 'frontend', '--files', FE_FILES[0])
    check('исключительное владение: пересечение файлов отказано', rc == 2 and 'принадлежат' in err, err)
    rc, _, err = Proj('roles3').cr('start', 'смешанная без контракта', '--kind', 'mixed')
    check('смешанная задача без контракта отказана', rc == 2 and 'контракт' in err, err)


def t_frontend_cycle():
    p = Proj('frontend')
    p.cr('start', 'фронтенд: список', '--id', 'fe-list', '--kind', 'mixed', '--contract-file', p.contract())
    p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES, '--context', 'backend/app.py')
    p.script('codex', [{'files': FILES_OK1}, {'files': FILES_OK2}])
    p.script('claude', [FIXV, OK])
    rc, out, err = p.cr('cycle', 'fe')
    check('frontend: цикл Codex→Claude: FIX возвращён автору автоматически, затем PASS (0)', rc == 0 and 'PASS' in out, out + err)
    check('frontend: 2 вызова автора Codex и 2 ревью Claude, Codex-ревью нет',
          p.count('codex-author') == 2 and p.count('claude-review') == 2 and p.count('codex-review') == 0, p.calls())
    check('frontend: Codex-автор с отключёнными инструментами и моделью gpt-6.1-sol',
          all('model=gpt-6.1-sol tools_off=True' in c for c in p.calls() if c.startswith('codex-author')), p.calls())
    check('frontend: reviewer Claude без инструментов и без сессии',
          all("tools='' nosession=True" in c for c in p.calls() if c.startswith('claude-review')), p.calls())
    second = (p.fake / 'codex-prompt-1.txt').read_text(encoding='utf-8')
    check('frontend: замечания reviewer дословно попали в запрос автору (без ручного переноса)', 'нет обработки пустого списка' in second)
    rc, _, _ = p.cr('cycle', 'fe')
    check('frontend: повторный cycle идемпотентен (0 новых вызовов)', rc == 0 and len(p.calls()) == 4, p.calls())
    head = p.git('rev-parse', 'HEAD')
    rc, out, err = p.cr('integrate', 'fe')
    new = p.git('rev-parse', 'HEAD')
    check('frontend: integrate влил кандидата ff-only после PASS', rc == 0 and new != head and (p.repo / 'frontend/src/App.tsx').read_text(encoding='utf-8').count('4') == 1, out + err)
    check('frontend: коммит автора сделан агентом, а не тимлидом', 'agent-codex' in p.git('log', '-1', '--format=%an'))
    rc, out, _ = p.cr('finish')
    check('frontend: finish → PASS по всем единицам', rc == 0, out)


def t_limits_recovery():
    p = Proj('limit')
    p.cr('start', 'лимит', '--id', 'lim-1', '--kind', 'mixed', '--contract-file', p.contract())
    p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    f3 = [{'files': {FE_FILES[0]: APP_OLD.replace('3]', f'{n}]'), FE_FILES[1]: TEST_V1}} for n in (5, 6, 7)]
    p.script('codex', f3)
    p.script('claude', [FIXV, FIXV, FIXV])
    rc, out, err = p.cr('cycle', 'fe')
    check('лимит: три FIX подряд → остановка по LIMIT (3)', rc == 3 and 'LIMIT' in err, out + err)
    n_calls = len(p.calls())
    check('лимит: 3 вызова автора и 3 ревью, не больше', p.count('codex-author') == 3 and p.count('claude-review') == 3, p.calls())
    rc, _, err = p.cr('cycle', 'fe')
    check('лимит: повторный запуск не делает новых вызовов и не сбрасывает счётчики', rc == 3 and len(p.calls()) == n_calls, err)
    rc, _, _ = p.cr('finish', '--abandon')
    check('лимит: finish без PASS → 1', rc == 1)
    rc, _, err = p.cr('start', 'лимит', '--id', 'lim-2', '--kind', 'mixed', '--contract-file', p.contract())
    check('лимит: новый id для той же цели не обходит бюджет', rc == 2 and err, err)
    rc, _, err = p.cr('start', 'Лимит!! (переформулировано иначе)', '--id', 'lim-3', '--kind', 'mixed', '--contract-file', p.contract())
    check('лимит: правка формулировки + новый id не обходят бюджет', rc == 2 and 'Бюджеты не сбрасываются' in err, err)
    p.commit('backend/app.py', 'def add(a, b):\n    return b + a\n', 'новый коммит')
    rc, _, err = p.cr('start', 'Совсем другая формулировка', '--id', 'lim-4', '--new-work', 'коротко')
    check('лимит: новый коммит + новая цель без решения/причины не обходят бюджет', rc == 2, err)
    rc, _, err = p.cr('start', 'Продолжение той же работы', '--id', 'lim-5', '--kind', 'mixed', '--contract-file', p.contract(), '--continues', 'lim-1')
    check('лимит: --continues стартует (общий расход 6 из 12), но единица fe исчерпана по перенесённому расходу', rc == 0, err)
    p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    rc, _, err = p.cr('cycle', 'fe')
    check('лимит: исчерпанная единица в продолжении отказана (LIMIT 3), новых вызовов нет', rc == 3 and 'LIMIT' in err and len(p.calls()) == n_calls, err)
    p.cr('finish', '--abandon')
    rc, _, err = p.cr('start', 'Продолжение ещё раз', '--id', 'lim-6', '--kind', 'mixed', '--contract-file', p.contract(), '--continues', 'lim-5')
    p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    rc2, _, err2 = p.cr('cycle', 'fe')
    check('лимит: повторное --continues не открывает бюджет заново', rc == 0 and rc2 == 3 and len(p.calls()) == n_calls, err + err2)

    c = Proj('chain')
    c.cr('start', 'цепочка', '--id', 'ch-1', '--kind', 'backend')
    c.commit('backend/app.py', 'def add(a, b):\n    return a+b\n')
    c.script('codex', [{'text': '- [MAJOR] backend/app.py:2 — плохо.\nVERDICT: FIX'}, {'text': 'VERDICT: FIX'}, {'text': 'VERDICT: PASS'}])
    c.cr('review')
    c.cr('finish', '--abandon')
    rc, _, err = c.cr('start', 'продолжение цепочки', '--id', 'ch-2', '--continues', 'ch-1')
    st = json.loads((c.repo / '.ai/task/state.json').read_text(encoding='utf-8')) if rc == 0 else {}
    check('цепочка: --continues переносит расход по единицам и общий (лимит единицы 3 остаётся 3)',
          rc == 0 and st['budget']['review'] == 3 and st['inherited']['units']['main']['review'] == 1 and st['inherited']['calls'] == 1, (st.get('inherited'), err))
    rc, _, err = c.cr('start', 'ещё раз', '--id', 'ch-3', '--continues', 'ch-1')
    check('цепочка: предшественник нельзя продолжить повторно', rc == 2, err)

    q = Proj('burn')
    q.cr('start', 'сбой модели', '--id', 'burn-1')
    q.commit('backend/app.py', 'def add(a, b):\n    return a+b\n')
    q.script('codex', [{'fail': 1}, {'text': 'VERDICT: PASS'}])
    rc, _, err = q.cr('review')
    check('сбой Codex-ревью: блокер (2), вызов израсходован', rc == 2 and q.count('codex-review') == 1, err)
    rc, _, err = q.cr('review')
    check('неопределённый вызов не повторяется автоматически', rc == 2 and q.count('codex-review') == 1, err)

    r = Proj('timeout')
    r.cr('start', 'таймаут', '--id', 'tmo-1')
    r.commit('backend/app.py', 'def add(a, b):\n    return a+b\n')
    r.script('codex', [{'sleep': 30}, {'text': 'VERDICT: PASS'}])
    rc, _, err = r.cr('review', env={'AQ_TIMEOUT': '2'})
    again = r.cr('review', env={'AQ_TIMEOUT': '2'})
    check('таймаут: блокер, повтор запрещён', rc == 2 and again[0] == 2 and r.count('codex-review') == 1, err)

    u = Proj('uncertain')
    u.cr('start', 'без вердикта', '--id', 'unc-1')
    u.commit('backend/app.py', 'def add(a, b):\n    return a+b\n')
    u.script('codex', [{'text': 'Всё выглядит нормально.'}])
    rc, _, err = u.cr('review')
    check('ответ без VERDICT = UNCERTAIN, не PASS (2)', rc == 2 and 'UNCERTAIN' in err, err)
    check('UNCERTAIN сохраняется: повтор без нового вызова', u.cr('review')[0] == 2 and u.count('codex-review') == 1)

    w = Proj('claude-unc')
    w.cr('start', 'uncertain fe', '--id', 'unc-fe', '--kind', 'mixed', '--contract-file', w.contract())
    w.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    w.script('codex', [{'files': FILES_OK1}, {'files': FILES_OK2}])
    w.script('claude', [UNC, OK])
    rc, out, err = w.cr('cycle', 'fe')
    check('UNCERTAIN у Claude-reviewer не принимается как PASS: возврат автору, затем PASS', rc == 0 and w.count('codex-author') == 2, out + err)


def t_engine_recovery():
    eng = load_engine()
    p = Proj('recovery')
    p.cr('start', 'восстановление', '--id', 'rec-1')
    os.chdir(p.repo)
    t = eng.Task(p.repo).load()
    calls = []

    def crash(d, prompt):
        (d / 'raw.txt').write_text('- [MINOR] x — y.\nVERDICT: PASS', encoding='utf-8')
        calls.append('crash')
        raise RuntimeError('процесс умер после сохранения ответа')

    try:
        eng.model_call(t, 'main', 'review', 1, 'codex-review', 'PROMPT', crash, eng.parse_codex_review)
    except RuntimeError:
        pass
    t = eng.Task(p.repo).load()

    def never(d, prompt):
        calls.append('RELAUNCH')

    res = eng.model_call(t, 'main', 'review', 1, 'codex-review', 'PROMPT', never, eng.parse_codex_review)
    check('восстановление: готовый ответ после сбоя читается без повторного вызова', res['verdict'] == 'PASS' and calls == ['crash'], calls)
    try:
        eng.model_call(t, 'main', 'review', 1, 'codex-review', 'PROMPT CHANGED', never, eng.parse_codex_review)
        ok = False
    except eng.Blocked as e:
        ok = 'binding' in str(e)
    check('receipt привязан к точному запросу (изменённый запрос не принимается)', ok and calls == ['crash'])

    def nothing(d, prompt):
        calls.append('launched')

    t2 = eng.Task(p.repo).load()
    try:
        eng.model_call(t2, 'main', 'review', 2, 'codex-review', 'P2', nothing, eng.parse_codex_review)
    except eng.Blocked:
        pass
    try:
        eng.model_call(t2, 'main', 'review', 2, 'codex-review', 'P2', never, eng.parse_codex_review)
        ok = False
    except eng.Blocked as e:
        ok = 'повтор' in str(e)
    check('резерв до запуска: вызов без ответа не повторяется', ok and 'RELAUNCH' not in calls, calls)


def t_author_validation():
    cases = {
        'новый eval': {FE_FILES[0]: APP_V1 + 'eval("x");\n', FE_FILES[1]: TEST_V1},
        'усечение': {FE_FILES[0]: 'x\n', FE_FILES[1]: TEST_V1},
        'удаление теста': {FE_FILES[0]: APP_V1, FE_FILES[1]: "test('a', () => {});\n"},
        'скрытый символ': {FE_FILES[0]: APP_V1 + '‮\n', FE_FILES[1]: TEST_V1},
        'секрет': {FE_FILES[0]: APP_V1 + '// ' + 'sk' + '-abcdefghijklmnopqrstuvwxyz123456\n', FE_FILES[1]: TEST_V1},
        'пустая правка': {FE_FILES[0]: APP_OLD, FE_FILES[1]: TEST_OLD},
    }
    for name, files in cases.items():
        p = Proj('val')
        p.cr('start', 'валидация', '--id', 'val-1', '--kind', 'mixed', '--contract-file', p.contract())
        p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
        p.script('codex', [{'files': files}])
        p.script('claude', [OK])
        rc, _, err = p.cr('cycle', 'fe')
        clean = not p.git('status', '--porcelain') and p.git('rev-parse', 'HEAD') == p.git('rev-parse', 'aq/test')
        wts = list(p.wt.glob('*'))
        wt_clean = all(subprocess.run(['git', '-C', str(w), 'status', '--porcelain'], capture_output=True, text=True).stdout.strip() == '' for w in wts)
        check(f'валидация автора отклоняет: {name}', rc == 2 and clean and wt_clean and p.count('claude') == 0, err)
    p = Proj('tool')
    p.cr('start', 'инструмент', '--id', 'tool-1', '--kind', 'mixed', '--contract-file', p.contract())
    p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    p.script('codex', [{'files': FILES_OK1, 'tool': True}])
    rc, _, err = p.cr('cycle', 'fe')
    check('автор, использовавший инструмент, отклонён', rc == 2 and 'инструмент' in err, err)

    t_checks()


ADD2 = 'def add(a, b):\n    return a + b + 1\n'


def t_hidden_paths_and_budget():
    # правка секретного/lock-пути в бэкенд-коммите не видна модели, поэтому ревью отказано (а не пройдено вслепую)
    p = Proj('hidden')
    p.cr('start', 'скрытые пути', '--id', 'hid-1', '--kind', 'backend')
    p.commit('backend/app.py', ADD2)
    p.commit('backend/poetry.lock', 'x = 1\n')
    p.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, _, err = p.cr('review')
    check('backend: коммит, затрагивающий lock/секретный путь, не отправляется на ревью и не принимается',
          rc == 2 and 'poetry.lock' in err and p.count('codex-review') == 0, (err, p.calls()))
    # правка после PASS в скрытом пути ломает приёмку
    q = Proj('hidden2')
    q.cr('start', 'после PASS', '--id', 'hid-2', '--kind', 'backend')
    q.commit('backend/app.py', ADD2)
    q.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, _, _ = q.cr('review')
    q.commit('backend/.env.example', 'A=1\n')
    rc2, out, _ = q.cr('finish')
    check('backend: правка скрытого пути после PASS делает приёмку не подтверждённой', rc == 0 and rc2 == 1 and 'НЕ ПОДТВЕРЖДЕНА' in out, out)
    # бюджет: после 3/3 ревью --continues не даёт нового ревью
    r = Proj('budget')
    r.cr('start', 'исчерпание', '--id', 'bud-1', '--kind', 'backend')
    r.script('codex', [{'text': 'VERDICT: FIX'}] * 3)
    for i in range(3):
        r.commit('backend/app.py', ADD2 + f'# {i}\n')
        r.cr('review')
    r.cr('finish', '--abandon')
    before = r.count('codex-review')
    rc, _, err = r.cr('start', 'то же самое', '--id', 'bud-2', '--kind', 'backend', '--continues', 'bud-1')
    r.commit('backend/app.py', ADD2 + '# 99\n')
    rc2, _, err2 = r.cr('review')
    check('единица 3/3: продолжение стартует, но ревью этой единицы отказано с LIMIT, вызовов нет',
          rc == 0 and rc2 == 3 and 'LIMIT' in err2 and r.count('codex-review') == before, err + err2)


def t_budget_units_chain():
    # лимит ревью — на единицу: 6 ревью по трём единицам (3+1+2) его не нарушают; продолжение вычитает расход по тем же единицам
    q = Proj('budunits', extra={'backend/b.py': 'B = 1\n', 'backend/c.py': 'C = 1\n'})
    q.cr('start', 'три единицы', '--id', 'bu-1', '--kind', 'mixed', '--contract-file', q.contract())
    units = (('u1', 'backend/app.py'), ('u2', 'backend/b.py'), ('u3', 'backend/c.py'))
    for uid, f in units:
        q.cr('unit', uid, '--kind', 'backend', '--files', f)
    q.script('codex', [{'text': 'VERDICT: FIX'}] * 12)
    rcs = []
    for i, (uid, f) in enumerate([units[0]] * 3 + [units[1]] + [units[2]] * 2):
        q.commit(f, f'X = {i}\n')
        rcs.append(q.cr('review', uid)[0])
    check('6 ревью по трём единицам (3+1+2) в одной задаче: лимит единицы не нарушен, все вызовы состоялись',
          rcs == [1] * 6 and q.count('codex-review') == 6, rcs)
    q.commit('backend/app.py', 'X = 100\n')
    rc, _, err = q.cr('review', 'u1')
    check('u1 исчерпана в той же задаче: LIMIT, вызова нет', rc == 3 and q.count('codex-review') == 6, err)
    q.cr('finish', '--abandon')
    entry = json.loads((q.repo / '.ai/ledger.json').read_text(encoding='utf-8'))[0]
    check('журнал хранит расход по единицам', entry['roles']['review'] == 6 and
          [entry['units'][u]['review'] for u in ('u1', 'u2', 'u3')] == [3, 1, 2], entry)
    rc, _, err = q.cr('start', 'продолжение', '--id', 'bu-2', '--kind', 'mixed', '--contract-file', q.contract(), '--continues', 'bu-1')
    check('продолжение стартует (общий расход 6 из 12)', rc == 0, err)
    for uid, f in units:
        q.cr('unit', uid, '--kind', 'backend', '--files', f)
    n0 = q.count('codex-review')
    q.commit('backend/app.py', 'X = 101\n')
    rc, _, err = q.cr('review', 'u1')
    check('продолжение: u1 (3/3, последние круги без прогресса) остановлена, вызова нет', rc == 3 and q.count('codex-review') == n0, err)
    q.commit('backend/b.py', 'X = 102\n')
    rc, _, _ = q.cr('review', 'u2')
    check('продолжение: u2 (1/3) получает ревью, чужой расход её не блокирует', rc == 1 and q.count('codex-review') == n0 + 1)

    def crash(s):   # сбой после receipt, до применения вердикта
        u = s['units']['u2']
        u['verdict'], u['reviewed'], u['review_applied'], u['status'] = None, None, 0, 'open'
        u['stall'], u['progress'], u['rounds'] = 0, None, []   # вердикт и итог круга сохраняются одной записью: до неё круга ещё нет
    state_edit(q, crash)
    rc, _, _ = q.cr('review', 'u2')
    check('восстановление в продолжении: ответ применён по receipt, нового вызова нет', rc == 1 and q.count('codex-review') == n0 + 1)
    q.commit('backend/b.py', 'X = 103\n')
    rc, _, _ = q.cr('review', 'u2')
    q.commit('backend/b.py', 'X = 104\n')
    rc4, _, err4 = q.cr('review', 'u2')
    check('u2: два круга подряд без прогресса (замечания не закрыты) — четвёртое ревью остановлено до пересмотра подхода',
          rc == 1 and rc4 == 3 and 'без прогресса' in err4 and q.count('codex-review') == n0 + 2, err4)
    st = json.loads((q.repo / '.ai/task/state.json').read_text(encoding='utf-8'))
    check('общий расход цепочки считается вместе с перенесённым', st['inherited']['calls'] == 6 and len(st['calls']) == 2, st['inherited'])


def t_task_budget_scope():
    """Бюджет принадлежит конкретной задаче плана: после принятой задачи следующая начинает с нуля, незавершённая сохраняет расход
    при новом запуске даже после другой самостоятельной задачи; предел Ивана действует только на ту цепочку, для которой задан."""
    pas = {'text': 'VERDICT: PASS'}
    p = Proj('scope')
    p.script('codex', [FIX_T, FIX_T, pas] + [FIX_T] * 3 + [pas])
    p.cr('start', 'задача плана один', '--id', 'plan-1', '--max-calls', '4')
    rcs = [review_n(p, n)[0] for n in range(3)]
    rc_fin, out, _ = p.cr('finish')
    check('две задачи плана: первая принята (3 ревью, предел Ивана 4 заданный для неё)', rcs == [1, 1, 0] and rc_fin == 0 and 'ACCEPTANCE: VERIFIED' in out, (rcs, rc_fin))
    rc, _, err = p.cr('start', 'задача плана два', '--id', 'plan-2')
    st = rstate(p)
    check('две задачи плана: вторая начинается с нуля — расход, единицы и предел первой не переносятся',
          rc == 0 and st['inherited']['calls'] == 0 and not st['inherited']['units'] and st['budget']['hard'] is None and st['chain'] == 'plan-2', (st.get('inherited'), err))
    rcs2 = [review_n(p, 10 + n)[0] for n in range(3)]
    check('две задачи плана: вторая получает полный набор своих ревью независимо от расхода первой', rcs2 == [1, 1, 1] and p.count('codex-review') == 6, rcs2)
    p.cr('finish', '--abandon')
    led = {e['id']: e for e in rledger(p)}
    check('история первой задачи сохранена в журнале (расход, результат, предел)',
          led['plan-1']['result'] == 'PASS' and led['plan-1']['calls'] == 3 and led['plan-1']['max_calls'] == 4 and led['plan-2']['calls'] == 3, led)

    q = Proj('scope-resume')
    q.script('codex', [FIX_T] * 3 + [pas, pas] + [FIX_T] * 3)
    q.cr('start', 'незавершённая задача', '--id', 'bot-1')
    for n in range(3):
        review_n(q, n)
    q.cr('finish', '--abandon')
    rc, _, err = q.cr('start', 'другая задача плана', '--id', 'plan-b')
    check('незавершённая задача: другая задача плана без решения владельца не стартует', rc == 2 and 'Бюджеты не сбрасываются' in err, err)
    rc, _, err = q.cr('start', 'другая задача плана', '--id', 'plan-b', '--new-work', 'самостоятельная задача плана, незавершённая остаётся открытой')
    check('незавершённая задача: самостоятельная задача стартует по решению владельца и с нуля',
          rc == 0 and rstate(q)['inherited']['calls'] == 0, err)
    review_n(q, 20)
    rc_fin, _, _ = q.cr('finish')
    check('незавершённая задача: самостоятельная задача принята', rc_fin == 0 and rledger(q)[-1]['result'] == 'PASS')
    n0 = q.count('codex-review')
    rc, _, err = q.cr('start', 'незавершённая задача снова', '--id', 'plan-b2', '--continues', 'plan-b')
    check('принятую задачу продолжить нельзя', rc == 2 and 'нет непринятой задачи' in err, err)
    rc, _, err = q.cr('start', 'незавершённая задача снова', '--id', 'bot-2', '--continues', 'bot-1')
    st = rstate(q) if rc == 0 else {}
    check('незавершённая задача: возврат после другой задачи сохраняет её расход (3 вызова, единица main 3 ревью), расход другой задачи не примешан',
          rc == 0 and st['inherited']['calls'] == 3 and st['inherited']['units']['main']['review'] == 3 and st['chain'] == 'bot-1', (st.get('inherited'), err))
    rc, _, err = q.cr('review')
    check('незавершённая задача: после возврата контрольная точка 3/3 без подтверждённого прогресса действует, нового вызова нет',
          rc == 3 and q.count('codex-review') == n0, err)
    led = {e['id']: e for e in rledger(q)}
    check('незавершённая задача: связь продолжения записана, повторное продолжение той же записи отказано',
          led['bot-1']['continued_by'] == 'bot-2' and q.cr('start', 'ещё', '--id', 'bot-3', '--continues', 'bot-1')[0] == 2, led['bot-1'])


def t_unavailable_and_modes():
    p = Proj('nocodex')
    p.cr('start', 'нет codex', '--id', 'noc-1', '--kind', 'mixed', '--contract-file', p.contract())
    p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    rc, _, err = p.cr('cycle', 'fe', env={'CODEX_BIN': json.dumps(['nonexistent-codex-bin-xyz'])})
    unit = json.loads((p.repo / '.ai/task/state.json').read_text(encoding='utf-8'))['units']['fe']
    check('Codex недоступен: блокер маршрута, автор не подменён Claude, вызовов нет',
          rc == 2 and unit['author'] == 'codex' and not p.calls() and not unit['candidate'], err)
    l = Proj('learn', mode='учебный')
    rc, _, err = l.cr('start', 'учебная задача', '--id', 'learn-1')
    check('учебный режим: без «да» на план start отказан', rc == 2 and 'учебный' in err, err)
    check('учебный режим: с --plan-approved start разрешён', l.cr('start', 'учебная задача', '--id', 'learn-1', '--plan-approved')[0] == 0)
    b = Proj('busy')
    p2 = subprocess.Popen([sys.executable, '-c',
                           'import importlib.util,sys,time;from pathlib import Path;'
                           f's=importlib.util.spec_from_file_location("e",r"{ENGINE}");m=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                           f'l=m.Lock(Path(r"{b.repo}")).__enter__();print("held",flush=True);time.sleep(20)'],
                          stdout=subprocess.PIPE, text=True)
    p2.stdout.readline()
    rc, _, err = b.cr('unit', 'x1', '--kind', 'backend', '--files', 'backend/app.py')
    rc_status, _, _ = b.cr('status')
    p2.kill()
    check('структурная команда при занятой структуре задачи → BUSY (4); status блокировкой не задерживается', rc == 4 and rc_status == 0, err)
    b.cr('start', 'занято', '--id', 'busy-1')
    p3 = subprocess.Popen([sys.executable, '-c',
                           'import importlib.util,sys,time;from pathlib import Path;'
                           f's=importlib.util.spec_from_file_location("e",r"{ENGINE}");m=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                           f'l=m.Lock(Path(r"{b.repo}"),"unit-main").__enter__();print("held",flush=True);time.sleep(20)'],
                          stdout=subprocess.PIPE, text=True)
    p3.stdout.readline()
    rc, _, err = b.cr('review', 'main')
    rc_f, _, err_f = b.cr('finish')
    rc_s, _, _ = b.cr('status')
    p3.kill()
    check('та же единица занята → BUSY (4); finish при работающей единице → BUSY; status доступен', rc == 4 and rc_f == 4 and rc_s == 0, err + err_f)


def t_mixed():
    p = Proj('mixed')
    p.cr('start', 'смешанная: список', '--id', 'mix-1', '--kind', 'mixed', '--contract-file', p.contract())
    check('mixed: backend-единица', p.cr('unit', 'be', '--kind', 'backend', '--files', 'backend/app.py')[0] == 0)
    check('mixed: protected-единица', p.cr('unit', 'cfg', '--kind', 'protected', '--files', 'package.json')[0] == 0)
    check('mixed: frontend-единица после backend', p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES, '--after', 'be')[0] == 0)
    p.script('codex', [{'text': '- [MAJOR] backend/app.py:2 — нет проверки типов. Как исправить: добавить.\nVERDICT: FIX'},
                       {'text': 'VERDICT: PASS'}, {'files': FILES_OK1}])
    p.script('claude', [OK])
    rc, _, err = p.cr('cycle', 'fe')
    check('mixed: frontend не стартует, пока backend не принят; вызовов нет, код 5 WAIT', rc == 5 and 'принята единица be' in err and 'WAIT' in err and not p.calls(), err)
    p.commit('backend/app.py', 'def add(a, b):\n    return a + b\n# контракт: GET /items\n')
    rc, _, _ = p.cr('review', 'be')
    check('mixed: backend (автор Claude) ревьюит Codex, FIX', rc == 1 and p.count('codex-review') == 1)
    p.commit('backend/app.py', 'def add(a, b):\n    return a + b  # проверено\n# контракт: GET /items\n', 'fix')
    rc, _, _ = p.cr('review', 'be')
    check('mixed: backend PASS после исправления', rc == 0)
    rc, out, err = p.cr('cycle', 'fe')
    check('mixed: затем frontend от Codex, ревью Claude, PASS', rc == 0 and p.count('codex-author') == 1 and p.count('claude-review') == 1, out + err)
    p.commit('package.json', '{"name": "x"}\n', 'deps')
    rc, out, err = p.cr('integrate', 'fe')
    check('mixed: ветка ушла вперёд без файлов единицы → слияние без конфликтов, оба набора изменений в ветке', rc == 0 and 'слияние' in out
          and len(p.git('rev-list', '--parents', '-n1', 'HEAD').split()) == 3 and (p.repo / 'package.json').read_text(encoding='utf-8').count('name') == 1
          and 'className' in (p.repo / FE_FILES[0]).read_text(encoding='utf-8'), out + err)
    q = Proj('mixed-overlap')
    q.cr('start', 'пересечение', '--id', 'ov-1', '--kind', 'mixed', '--contract-file', q.contract())
    q.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    q.script('codex', [{'files': FILES_OK1}])
    q.script('claude', [OK])
    q.cr('cycle', 'fe')
    q.commit(FE_FILES[1], TEST_V1 + "test('d', () => {});\n", 'чужая правка файла единицы')
    rc, _, err = q.cr('integrate', 'fe')
    check('mixed: ветка ушла вперёд и изменила файлы единицы → интеграция отказана, ветка не тронута', rc == 2 and 'изменила файлы единицы' in err
          and q.git('rev-list', '--count', 'HEAD') == '2', err)


def commit_with_paths(repo, paths):
    """Коммит с произвольными путями plumbing-командами (index и Windows такие имена не принимают). Ввод — БАЙТЫ: в текстовом режиме
    Windows добавил бы CR к именам, и проверка отклоняла бы путь по постороннему признаку."""
    def run_git(args, data=b''):
        return subprocess.run(['git', '-C', str(repo), *args], input=data, capture_output=True).stdout.decode().strip()
    blob = run_git(['hash-object', '-w', '--stdin'], b'x')

    def build(items):
        files = {n: None for n in items if '/' not in n}
        dirs = {}
        for n in items:
            if '/' in n:
                d, rest = n.split('/', 1)
                dirs.setdefault(d, []).append(rest)
        data = b''.join(f'100644 blob {blob}\t{n}\n'.encode() for n in files)
        data += b''.join(f'040000 tree {build(v)}\t{d}\n'.encode() for d, v in dirs.items())
        return run_git(['mktree'], data)
    tree = build(paths)
    return run_git(['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit-tree', tree, '-m', 'paths'])


def docker_runs(p):
    f = p.fake / 'docker.log'
    return [l for l in f.read_text(encoding='utf-8').splitlines() if l.startswith('run ')] if f.exists() else []


def t_checks():
    # --- зелёный путь backend: проверка в контейнере ДО модельного ревью, ограничения контейнера, закреплённый образ
    p = Proj('chk-be')
    p.cr('start', 'проверки backend', '--id', 'chk-be', '--kind', 'backend')
    p.commit('backend/app.py', ADD2)
    p.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = p.cr('review')
    runs = docker_runs(p)
    line = runs[0] if runs else ''
    check('checks: backend проверен в контейнере, затем независимое ревью PASS', rc == 0 and len(runs) == 1 and p.count('codex-review') == 1, out + err)
    check('checks: контейнер без сети, root только для чтения, без capabilities, образ не скачивается, исходники read-only',
          all(x in line for x in ('--network none', '--read-only', '--cap-drop ALL', '--pull never', 'no-new-privileges', 'readonly', '--entrypoint sh', '--log-driver none')), line)
    check('checks: в контейнер не передаются переменные окружения хоста (только HOME и CI)', line.count(' -e ') == 2, line)
    check('checks: образ закреплён по ID, а не по имени из конфигурации', 'sha256:fakeimage' in line and 'aq-fake:1' not in line, line)
    listing = (p.fake / 'last-src-list.txt').read_text(encoding='utf-8').splitlines()
    check('checks: в контейнер уходит точный коммит без .git и .ai', 'backend/app.py' in listing and not any(x.startswith(('.git', '.ai')) for x in listing), listing)
    rc, out, _ = p.cr('finish')
    check('checks: finish → ACCEPTANCE: VERIFIED (проверки выполнены на точном SHA)', rc == 0 and 'ACCEPTANCE: VERIFIED' in out and 'ТОЛЬКО СТАТИЧЕСКОГО' not in out, out)
    entry = json.loads((p.repo / '.ai/ledger.json').read_text(encoding='utf-8'))[-1]
    check('checks: журнал хранит acceptance=verified', entry['acceptance'] == 'verified' and entry['result'] == 'PASS', entry)

    # --- регрессия кандидата: красная проверка (зелёная на базе) возвращается автору без платного ревью
    q = Proj('chk-fe')
    q.cr('start', 'красная проверка frontend', '--id', 'chk-fe', '--kind', 'mixed', '--contract-file', q.contract())
    q.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    q.script('codex', [{'files': {FE_FILES[0]: APP_V1 + '// FAIL_test\n', FE_FILES[1]: TEST_V1}}, {'files': FILES_OK1}])
    q.script('claude', [OK])
    rc, out, err = q.cr('cycle', 'fe')
    second = (q.fake / 'codex-prompt-1.txt').read_text(encoding='utf-8') if (q.fake / 'codex-prompt-1.txt').exists() else ''
    check('checks: красная проверка кандидата → автору Codex возвращён вывод, ревью Claude только после зелёных (1 ревью)',
          rc == 0 and q.count('codex-author') == 2 and q.count('claude-review') == 1 and 'Проверки на кандидате не прошли' in second and 'FAILED test' in second, out + err)
    check('checks: красная команда сравнена с базой (базовый запуск был)', len(docker_runs(q)) == 3, len(docker_runs(q)))
    rc, out, err = q.cr('integrate', 'fe')
    check('checks: integrate после PASS и зелёных проверок', rc == 0, out + err)
    rc, out, _ = q.cr('finish')
    check('checks: frontend finish → VERIFIED', rc == 0 and 'ACCEPTANCE: VERIFIED' in out, out)

    # --- уже существующее падение базы: не вина кандидата, но и не подтверждение; модель не вызывается
    r = Proj('chk-pre', extra={'backend/legacy.py': '# FAIL_test\n'})
    r.cr('start', 'красная база', '--id', 'chk-pre', '--kind', 'backend')
    r.commit('backend/app.py', ADD2)
    r.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = r.cr('review')
    check('checks: красная и на базе → блокер «уже существующее падение», модель не вызывалась', rc == 2 and 'красная и на базе' in err and r.count('codex') == 0, err)
    rc, out, _ = r.cr('finish')
    check('checks: такая задача не принята', rc == 1 and 'НЕ ПОДТВЕРЖДЕНА' in out, out)

    # --- нехватка окружения: конкретный блокер до платного вызова
    d = Proj('chk-nodocker')
    d.cr('start', 'без docker', '--id', 'chk-nd', '--kind', 'backend')
    d.commit('backend/app.py', ADD2)
    (d.fake / 'nodocker').write_text('1')
    d.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = d.cr('review')
    check('нет Docker: блокер с причиной, ревью не вызывалось, состояние сохранено', rc == 2 and 'Docker не запущен' in err and d.count('codex') == 0 and (d.repo / '.ai/task/state.json').exists(), err)
    (d.fake / 'nodocker').unlink()
    d.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = d.cr('review')
    check('после устранения блокера то же ревью проходит (вызов не был израсходован впустую)', rc == 0 and d.count('codex-review') == 1, out + err)
    m = Proj('chk-noimage')
    m.cr('start', 'без образа', '--id', 'chk-ni', '--kind', 'mixed', '--contract-file', m.contract())
    m.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    (m.fake / 'noimage').write_text('1')
    m.script('codex', [{'files': FILES_OK1}])
    rc, out, err = m.cr('cycle', 'fe')
    check('нет образа: блокер ДО вызова Codex-автора, автор не вызывался, образ не собирается/скачивается',
          rc == 2 and 'отсутствует локально' in err and m.count('codex') == 0 and not [l for l in (m.fake / 'docker.log').read_text().splitlines() if l.startswith(('build', 'pull'))], err)

    # --- нет проверок в доверенной конфигурации: блокер; обоснование — только явное и не вместо настроенных проверок
    n = Proj('chk-none', config=None)
    n.cr('start', 'без проверок', '--id', 'chk-none', '--kind', 'backend')
    n.commit('backend/app.py', ADD2)
    n.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = n.cr('review')
    check('нет проверок в конфигурации: блокер, а не молчаливое «не требуется»', rc == 2 and 'нет проверок' in err and n.count('codex') == 0, err)
    rc, _, err = n.cr('waive', 'main', 'не нужно')
    check('обоснование короче 20 символов отклонено', rc == 2, err)
    rc, _, err = n.cr('waive', 'main', 'в проекте нет автоматических тестов для этого файла, проверка вручную')
    check('обоснование записано', rc == 0, err)
    rc, out, err = n.cr('review')
    check('с обоснованием ревью проходит, reviewer видит обоснование', rc == 0 and 'обоснование' in (n.fake / 'codex-prompt-0.txt').read_text(encoding='utf-8'), out + err)
    rc, out, _ = n.cr('finish')
    entry = json.loads((n.repo / '.ai/ledger.json').read_text(encoding='utf-8'))[-1]
    check('finish: ACCEPTANCE: VERIFIED-WAIVED с записью в журнал', rc == 0 and 'VERIFIED-WAIVED' in out and entry['acceptance'] == 'verified-waived', out)
    w = Proj('chk-waive-refused')
    w.cr('start', 'обоснование при настроенных проверках', '--id', 'chk-wr', '--kind', 'backend')
    rc, _, err = w.cr('waive', 'main', 'хочу пропустить проверки, потому что лень их ждать')
    check('при настроенных проверках обоснование отклонено', rc == 2 and 'выполнить' in err, err)

    # --- конфигурация проверок берётся только из базы: кандидат её менять не может
    t = Proj('chk-tamper')
    t.cr('start', 'подмена конфигурации', '--id', 'chk-tp', '--kind', 'backend')
    t.commit('.agents/agentq.json', json.dumps({'checks': {'backend': [['true']]}, 'check_image': 'aq-fake:1'}))
    t.commit('backend/app.py', ADD2)
    t.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = t.cr('review')
    check('кандидат меняет .agents/agentq.json: проверки не запускаются, ревью не вызывается', rc == 2 and 'agentq.json' in err and t.count('codex') == 0 and not docker_runs(t), err)

    # --- ссылки в кандидате не экспортируются
    k = Proj('chk-link')
    k.cr('start', 'ссылка в кандидате', '--id', 'chk-lk', '--kind', 'backend')
    k.commit('backend/app.py', ADD2)
    blob = subprocess.run(['git', '-C', str(k.repo), 'hash-object', '-w', '--stdin'], input='/etc/passwd', capture_output=True, text=True).stdout.strip()
    k.git('update-index', '--add', '--cacheinfo', f'120000,{blob},backend/link')
    k.git('commit', '-q', '-m', 'link')
    k.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = k.cr('review')
    check('символическая ссылка в кандидате: проверки не запускаются (блокер), контейнер не стартовал', rc == 2 and 'ссылка' in err and not docker_runs(k) and k.count('codex') == 0, err)

    # --- шлюзы: статический PASS не открывает integrate и finish
    g = Proj('chk-gate')
    g.cr('start', 'шлюзы', '--id', 'chk-gt', '--kind', 'mixed', '--contract-file', g.contract())
    g.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    g.script('codex', [{'files': FILES_OK1}])
    g.script('claude', [OK])
    rc, _, _ = g.cr('cycle', 'fe')
    state_edit(g, lambda s: s['units']['fe'].update(checks=None))
    rc1, _, err1 = g.cr('integrate', 'fe')
    check('integrate без подтверждённых проверок отказан даже при PASS ревью', rc == 0 and rc1 == 2 and 'проверок' in err1, err1)
    rc2, out2, _ = g.cr('checks', 'fe')
    rc3, _, err3 = g.cr('integrate', 'fe')
    check('после CR checks интеграция разрешена (модель не вызывалась повторно)', rc2 == 0 and rc3 == 0 and g.count('claude-review') == 1, err3)
    h = Proj('chk-gate-be')
    h.cr('start', 'шлюз finish', '--id', 'chk-gf', '--kind', 'backend')
    h.commit('backend/app.py', ADD2)
    h.script('codex', [{'text': 'VERDICT: PASS'}])
    h.cr('review')
    state_edit(h, lambda s: s['units']['main'].update(checks=None))
    rc1, out1, _ = h.cr('finish')
    check('finish: независимый PASS без проверок → не принято, состояние сохранено (не закрыто)', rc1 == 1 and 'НЕ ПОДТВЕРЖДЕНА' in out1 and 'статическое ревью их не заменяет' in out1 and (h.repo / '.ai/task/state.json').exists(), out1)
    h.cr('checks')
    rc2, out2, _ = h.cr('finish')
    check('finish после CR checks: VERIFIED', rc2 == 0 and 'ACCEPTANCE: VERIFIED' in out2, out2)

    # --- замечания внешней приёмки области A2 (Codex, 25.10): регрессии
    pa = Proj('chk-partial')
    pa.cr('start', 'частичная приёмка', '--id', 'chk-pa', '--kind', 'mixed', '--contract-file', pa.contract())
    pa.cr('unit', 'be', '--kind', 'backend', '--files', 'backend/app.py')
    pa.cr('unit', 'cfg', '--kind', 'protected', '--files', 'package.json')
    pa.commit('backend/app.py', ADD2)
    pa.script('codex', [{'text': 'VERDICT: PASS'}])
    pa.cr('review', 'be')
    state_edit(pa, lambda st: st['units']['be'].update(checks=None))
    rc, out, _ = pa.cr('finish')
    check('finish при частичной приёмке (be: PASS без проверок, cfg: без PASS) не закрывает задачу и не теряет состояние',
          rc == 1 and (pa.repo / '.ai/task/state.json').exists() and json.loads((pa.repo / '.ai/ledger.json').read_text(encoding='utf-8')) == [] and 'finish --abandon' in out, out)
    rc, _, _ = pa.cr('checks', 'be')
    rc2, out2, _ = pa.cr('finish', '--abandon')
    entry = json.loads((pa.repo / '.ai/ledger.json').read_text(encoding='utf-8'))[-1]
    check('после CR checks закрытие непринятой — только явным --abandon, в журнале NOT_ACCEPTED с расходом',
          rc == 0 and rc2 == 1 and entry['result'] == 'NOT_ACCEPTED' and entry['calls'] == 1 and not (pa.repo / '.ai/task/state.json').exists(), (out2, entry))

    ut = Proj('chk-untracked', extra={'backend/pkg/__init__.py': 'x = 1\n'})
    ut.cr('start', 'неотслеживаемый файл после PASS', '--id', 'chk-ut', '--kind', 'mixed', '--contract-file', ut.contract())
    ut.cr('unit', 'be', '--kind', 'backend', '--files', 'backend/pkg')
    ut.commit('backend/pkg/__init__.py', 'x = 2\n')
    ut.script('codex', [{'text': 'VERDICT: PASS'}])
    ut.cr('review', 'be')
    (ut.repo / 'backend/pkg/new_module.py').write_text('import os\n', encoding='utf-8')
    rc, out, _ = ut.cr('finish')
    check('новый неотслеживаемый файл в принятом каталоге после PASS делает приёмку неподтверждённой', rc == 1 and 'НЕ ПОДТВЕРЖДЕНА' in out, out)

    for label, text in (('нумерованный список', '1. [MAJOR] backend/app.py:2 — проблема. Как исправить.\nVERDICT: PASS'),
                        ('жирный маркер', '**[BLOCKER]** backend/app.py:2 — проблема. Как исправить.\nVERDICT: PASS'),
                        ('цитата', '> [MAJOR] backend/app.py:2 — проблема.\nVERDICT: PASS'),
                        ('маркер плюс', '+ [MAJOR] backend/app.py:2 — проблема.\nVERDICT: PASS'),
                        ('нумерация и цитата', '1. > [MAJOR] backend/app.py:2 — проблема.\nVERDICT: PASS'),
                        ('вложенные маркеры', '- > * **[BLOCKER]** backend/app.py:2 — проблема.\nVERDICT: PASS')):
        x = Proj('chk-contra')
        x.cr('start', 'противоречивый PASS', '--id', 'chk-ct', '--kind', 'backend')
        x.commit('backend/app.py', ADD2)
        x.script('codex', [{'text': text}])
        rc, out, err = x.cr('review')
        check(f'PASS при BLOCKER/MAJOR в отчёте ({label}) считается FIX', rc == 1 and 'VERDICT: FIX' in out, out + err)
    y = Proj('chk-contra-fe')
    y.cr('start', 'структурный PASS с MAJOR', '--id', 'chk-cf', '--kind', 'mixed', '--contract-file', y.contract())
    y.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    y.script('codex', [{'files': FILES_OK1}, {'files': FILES_OK2}])
    y.script('claude', [{'verdict': 'PASS', 'summary': 's', 'findings': FIXV['findings']}, OK])
    rc, out, err = y.cr('cycle', 'fe')
    check('Claude-вердикт PASS с MAJOR в structured findings считается FIX и возвращается автору', rc == 0 and y.count('codex-author') == 2 and y.count('claude-review') == 2, out + err)

    # --- замечания повторной внешней приёмки (Codex, 25.10): пути Windows, размеры до чтения, режим повторного ревью backend
    eng = load_engine()
    xp = Proj('chk-export')
    bl = subprocess.run(['git', '-C', str(xp.repo), 'hash-object', '-w', '--stdin'], input='x', capture_output=True, text=True).stdout.strip()
    for label, name, reason in (('обратная косая черта', 'a' + chr(92) + 'b.py', 'небезопасный путь'), ('управляющий символ', 'a' + chr(8) + 'b.py', 'небезопасный путь'),
                                ('зарезервированное имя Windows', 'aux.py', 'небезопасный путь'), ('имя с точкой на конце', 'x.', 'небезопасный путь'),
                                ('имя в каталоге с точкой на конце', 'dir./x.py', 'небезопасный путь')):
        xq = Proj('chk-export-n')
        commit = commit_with_paths(xq.repo, [name])
        try:
            eng.export_commit(xq.repo, commit, xq.root / 'exp')
            rejected = False
        except eng.Blocked as e:
            rejected = reason in str(e)
        check(f'экспорт отклоняет путь: {label}', rejected)
    # контракт dest: экспорт создаёт НОВЫЙ каталог; существующий каталог (даже пустой), файл и ссылка отклоняются, их содержимое не меняется
    ex = Proj('chk-exclusive')
    pre = ex.root / 'exp-pre' / 'backend'
    pre.mkdir(parents=True)
    (pre / 'app.py').write_text('другой файл с тем же именем', encoding='utf-8')
    (ex.root / 'exp-pre' / 'extra.txt').write_text('посторонний', encoding='utf-8')
    empty_commit = commit_with_paths(ex.repo, [])
    for label, rev in (('обычный коммит', 'HEAD'), ('пустой коммит', empty_commit)):
        try:
            eng.export_commit(ex.repo, rev, ex.root / 'exp-pre')
            coll = False
        except eng.Blocked as e:
            coll = 'dest' in str(e) and (pre / 'app.py').read_text(encoding='utf-8') == 'другой файл с тем же именем' and (ex.root / 'exp-pre' / 'extra.txt').read_text(encoding='utf-8') == 'посторонний'                 and sorted(q.name for q in (ex.root / 'exp-pre').iterdir()) == ['backend', 'extra.txt']
        except OSError:
            coll = False   # необработанная системная ошибка вместо понятного Blocked — это тоже провал контракта
        check(f'существующий непустой dest отклоняется без изменений ({label})', coll)
    (ex.root / 'exp-empty-dir').mkdir()
    try:
        eng.export_commit(ex.repo, 'HEAD', ex.root / 'exp-empty-dir')
        rej = False
    except eng.Blocked as e:
        rej = 'dest' in str(e) and not any((ex.root / 'exp-empty-dir').iterdir())
    except OSError:
        rej = False
    check('существующий пустой dest отклоняется (экспорт создаёт новый каталог)', rej)
    (ex.root / 'exp-file').write_text('файл', encoding='utf-8')
    try:
        eng.export_commit(ex.repo, 'HEAD', ex.root / 'exp-file')
        rej = False
    except eng.Blocked as e:
        rej = 'dest' in str(e) and (ex.root / 'exp-file').read_text(encoding='utf-8') == 'файл'
    except OSError:
        rej = False
    check('dest — существующий файл: отклоняется без изменений', rej)
    target_dir = ex.root / 'exp-target'
    target_dir.mkdir()
    link = ex.root / 'exp-link'
    try:
        if os.name == 'nt':   # junction не требует прав администратора; обычная symlink — требует
            made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target_dir)], capture_output=True).returncode == 0
        else:
            os.symlink(target_dir, link)
            made = True
    except OSError:
        made = False
    if made:
        try:
            eng.export_commit(ex.repo, 'HEAD', link)
            rej = False
        except eng.Blocked as e:
            rej = 'dest' in str(e) and not any(target_dir.iterdir())
        except OSError:
            rej = False
        check('dest — ссылка/junction: отклоняется, цель не изменяется', rej)
    else:
        print('SKIP: ссылку/junction создать не удалось — проверка dest-ссылки пропущена')
    new_dest = ex.root / 'deep' / 'new-dest'
    try:
        eng.export_commit(ex.repo, 'HEAD', new_dest)
        okn = (new_dest / 'backend' / 'app.py').is_file()
    except (eng.Blocked, OSError):
        okn = False
    check('несуществующий dest (с новыми родительскими каталогами) создаётся и заполняется', okn)
    for label, names, reason in (('каталоги различаются регистром', ['Foo/a.txt', 'foo/b.txt'], 'каталоги различаются только регистром'),
                                 ('8.3-алиас каталога', ['LONGDI~1/a.txt'], '8.3'), ('8.3-алиас файла', ['backend/LONGFI~1.TXT'], '8.3'),
                                 ('файлы различаются регистром', ['a/File.txt', 'a/file.txt'], 'различаются только регистром'),
                                 ('файл и каталог с одним именем', ['Foo', 'foo/x.txt'], 'файл и каталог с одним именем'),
                                 ('вложенный файл и каталог с одним именем', ['a/B', 'a/b/c.txt'], 'файл и каталог с одним именем')):
        xe = Proj('chk-dirs')
        commit = commit_with_paths(xe.repo, names)
        try:
            eng.export_commit(xe.repo, commit, xe.root / 'exp')
            rejected = False
        except eng.Blocked as e:
            rejected = reason in str(e)
        check(f'экспорт отклоняет слияние путей: {label}', rejected)
    # контракт путей: ТОЛЬКО печатный ASCII — регистровое сопоставление NTFS и устройства Windows для не-ASCII (ı/I, COM¹) Python не повторяет
    for label, names in (('U+0131 рядом с I (NTFS сливает каталоги)', ['I/a.txt', 'ı/b.txt']), ('COM-надстрочная цифра', ['COM¹.txt']), ('LPT-надстрочная цифра', ['LPT²']),
                         ('кириллица', ['файл.txt']), ('кириллица в каталоге', ['папка/a.txt']), ('комбинирующий символ', ['é.txt'])):
        xn = Proj('chk-ascii')
        commit = commit_with_paths(xn.repo, names)
        try:
            eng.export_commit(xn.repo, commit, xn.root / 'exp')
            rejected = False
        except eng.Blocked as e:
            rejected = 'не-ASCII' in str(e) and not (xn.root / 'exp').exists()   # отказ ДО создания dest и записи файлов
        except OSError:
            rejected = False
        check(f'экспорт отклоняет не-ASCII путь: {label}', rejected)
    # недопустимые для Windows символы и слишком длинные компоненты — Blocked ДО создания dest (а не OSError после него)
    for label, names in (('знак вопроса', ['a?.txt']), ('звёздочка в каталоге', ['d*/a.txt']), ('угловые скобки', ['<a>.txt']), ('кавычка', ['a"b.txt']), ('вертикальная черта', ['a|b.txt']),
                         ('компонент 256 символов', ['a' * 256 + '.txt']), ('длинный каталог', ['b' * 256 + '/x.txt'])):
        xb = Proj('chk-badchars')
        commit = commit_with_paths(xb.repo, names)
        try:
            eng.export_commit(xb.repo, commit, xb.root / 'exp')
            rejected = False
        except eng.Blocked as e:
            rejected = 'небезопасный путь' in str(e) and not (xb.root / 'exp').exists()
        except OSError:
            rejected = False
        check(f'экспорт отклоняет недопустимое имя Windows: {label}', rejected)
    # пустые поддеревья (`ls-tree -r` их не показывает) подчиняются тем же правилам имён
    def commit_with_empty_dirs(repo, empties, files=()):
        def rg(args, data=b''):
            return subprocess.run(['git', '-C', str(repo), *args], input=data, capture_output=True).stdout.decode().strip()
        empty, blob = rg(['mktree']), rg(['hash-object', '-w', '--stdin'], b'x')
        def build(files_, dirs_):
            top = {}
            for n in dirs_:
                top.setdefault(n.split('/', 1)[0], ([], []))[1].append(n.split('/', 1)[1] if '/' in n else None)
            for n in files_:
                top.setdefault(n.split('/', 1)[0], ([], []))[0].append(n.split('/', 1)[1] if '/' in n else None)
            rows = b''
            for name, (fs, ds) in top.items():
                if None in fs:
                    rows += f'100644 blob {blob}\t{name}\n'.encode()
                elif any(x is not None for x in fs + ds):
                    rows += f'040000 tree {build([x for x in fs if x], [x for x in ds if x])}\t{name}\n'.encode()
                else:
                    rows += f'040000 tree {empty}\t{name}\n'.encode()
            return rg(['mktree'], rows)
        return rg(['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit-tree', build(list(files), list(empties)), '-m', 'empty-dirs'])
    for label, empties, files, reason in (('пустой .git', ['.git'], ['a.txt'], 'небезопасный путь'), ('пустое con', ['con'], [], 'небезопасный путь'), ('пустой вложенный .git', ['src/.git'], ['src/a.txt'], 'небезопасный путь'),
                                          ('пустой каталог с ?', ['d?'], [], 'небезопасный путь'), ('пустой каталог Foo и файл foo', ['Foo'], ['foo'], 'одним'), ('пустые foo и Foo', ['foo', 'Foo'], [], 'регистром')):
        xt = Proj('chk-empty-tree')
        commit = commit_with_empty_dirs(xt.repo, empties, files)
        try:
            eng.export_commit(xt.repo, commit, xt.root / 'exp')
            rejected = False
        except eng.Blocked as e:
            rejected = reason in str(e) and not (xt.root / 'exp').exists()
        except OSError:
            rejected = False
        check(f'экспорт отклоняет пустое поддерево: {label}', rejected)
    xt2 = Proj('chk-empty-tree-ok')
    try:
        eng.export_commit(xt2.repo, commit_with_empty_dirs(xt2.repo, ['docs', 'src/empty'], ['src/a.txt']), xt2.root / 'exp')
        ok_empty_dirs = sorted(q.relative_to(xt2.root / 'exp').as_posix() for q in (xt2.root / 'exp').rglob('*') if q.is_file()) == ['src/a.txt']
    except eng.Blocked:
        ok_empty_dirs = False
    check('допустимые пустые поддеревья не мешают экспорту', ok_empty_dirs)
    xl = Proj('chk-name-255')
    try:
        eng.export_commit(xl.repo, commit_with_paths(xl.repo, ['c' * 100 + '.txt']), xl.root / 'e')
        ok255 = (xl.root / 'e' / ('c' * 100 + '.txt')).is_file()
    except eng.Blocked:
        ok255 = False
    check('имя допустимой длины экспортируется', ok255)
    # гонка: dest создан другим процессом после проверки существования — одинаковый Blocked для пустого и непустого дерева, чужие файлы целы
    xr = Proj('chk-race')
    race_empty = commit_with_paths(xr.repo, [])
    for label, rev in (('непустое дерево', 'HEAD'), ('пустое дерево', race_empty)):
        rd = xr.root / ('exp-race-' + str(len(label)))
        rd.mkdir()
        (rd / 'foreign.txt').write_text('чужой', encoding='utf-8')
        real = eng.os.path.lexists
        eng.os.path.lexists = lambda q, _r=real, _d=rd: False if str(q) == str(_d) else _r(q)   # имитация: проверка прошла до чужого mkdir
        try:
            eng.export_commit(xr.repo, rev, rd)
            raced = False
        except eng.Blocked as e:
            raced = 'dest' in str(e) and (rd / 'foreign.txt').read_text(encoding='utf-8') == 'чужой' and sorted(q.name for q in rd.iterdir()) == ['foreign.txt']
        except OSError:
            raced = False
        finally:
            eng.os.path.lexists = real
        check(f'гонка при создании dest даёт Blocked ({label})', raced)
    # любая другая ошибка создания dest (имя, права) — тоже Blocked, одинаково для обоих веток
    for label, rev in (('непустое дерево', 'HEAD'), ('пустое дерево', race_empty)):
        real_mkdir = eng.Path.mkdir
        def bad_mkdir(self, *a, **k):
            raise OSError(22, 'Invalid argument')
        eng.Path.mkdir = bad_mkdir
        try:
            eng.export_commit(xr.repo, rev, xr.root / 'exp-oserr')
            oserr = False
        except eng.Blocked as e:
            oserr = 'dest не создан' in str(e)
        except OSError:
            oserr = False
        finally:
            eng.Path.mkdir = real_mkdir
        check(f'ошибка ОС при создании dest даёт Blocked ({label})', oserr)
    xv = Proj('chk-ascii-ok')
    valid = ['README.md', 'src/app-1.py', 'src/_x/y.test.ts', 'docs/My File (1).md', '.github/workflows/ci.yml', 'a+b/c,d=e.txt', 'x/COM10.txt', 'x/aux1.txt', 'x/console.py', 'tilde~name.txt'.replace('~', '-')]
    try:
        eng.export_commit(xv.repo, commit_with_paths(xv.repo, valid), xv.root / 'exp')
        got = sorted(q.relative_to(xv.root / 'exp').as_posix() for q in (xv.root / 'exp').rglob('*') if q.is_file())
        ok_valid = got == sorted(valid)
    except eng.Blocked:
        ok_valid = False
    check('допустимые ASCII-имена (пробелы, скобки, точки, COM10, aux1, console) экспортируются целиком', ok_valid)
    xok = Proj('chk-dirs-ok')
    try:
        eng.export_commit(xok.repo, commit_with_paths(xok.repo, ['src/a.txt', 'src/b.txt', 'docs/c.txt']), xok.root / 'exp-ok')
        ok_tree = sorted(q.relative_to(xok.root / 'exp-ok').as_posix() for q in (xok.root / 'exp-ok').rglob('*') if q.is_file()) == ['docs/c.txt', 'src/a.txt', 'src/b.txt']
    except eng.Blocked:
        ok_tree = False
    check('обычное дерево с общими каталогами экспортируется целиком', ok_tree)
    em = Proj('chk-empty')
    tree = subprocess.run(['git', '-C', str(em.repo), 'mktree'], input='', capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(['git', '-C', str(em.repo), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit-tree', tree, '-m', 'empty'], capture_output=True, text=True).stdout.strip()
    try:
        eng.export_commit(em.repo, commit, em.root / 'exp-empty')
        ok_empty = (em.root / 'exp-empty').is_dir()
    except eng.Blocked:
        ok_empty = False
    check('пустой коммит экспортируется как пустой каталог (без ошибки размеров)', ok_empty)
    old_limit = eng.EXPORT_LIMIT
    eng.EXPORT_LIMIT = 10
    try:
        eng.export_commit(xp.repo, 'HEAD', xp.root / 'exp-big')
        rejected = False
    except eng.Blocked as e:
        rejected = 'больше' in str(e) and not (xp.root / 'exp-big' / 'backend' / 'app.py').exists()
    finally:
        eng.EXPORT_LIMIT = old_limit
    check('превышение лимита экспорта обнаруживается ДО чтения и записи содержимого', rejected)
    ri = Proj('chk-incremental')
    ri.cr('start', 'повторное ревью backend инкрементально', '--id', 'chk-in', '--kind', 'backend')
    ri.commit('backend/app.py', ADD2)
    ri.script('codex', [{'text': '- [MAJOR] backend/app.py:2 — лишнее. Как исправить.\nVERDICT: FIX'}, {'text': 'VERDICT: PASS'}])
    ri.cr('review')
    ri.commit('backend/app.py', 'def add(a, b):\n    return a + b\n', 'fix')
    rc, out, err = ri.cr('review')
    pr = (ri.fake / 'codex-prompt-1.txt').read_text(encoding='utf-8')
    check('backend: повторное ревью инкрементально (прошлые замечания, diff от прошлого SHA, не от базы)', rc == 0 and 'ПРОШЛЫЕ ЗАМЕЧАНИЯ' in pr, out + err)

    # --- замечания внешней приёмки области A1 (Codex, 25.10): регрессии
    f = Proj('chk-filter')
    marker = f.root / 'FILTER_RAN.txt'
    f.git('config', 'filter.runner.smudge', f'"{sys.executable}" -c "open(r\'{marker}\', \'w\').write(\'x\')"')
    f.git('config', 'filter.runner.required', 'false')
    f.cr('start', 'git-фильтр кандидата', '--id', 'chk-fl', '--kind', 'backend')
    f.commit('.gitattributes', '*.py filter=runner\n')
    f.commit('backend/app.py', ADD2)
    f.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = f.cr('review')
    check('экспорт кандидата не запускает git smudge-фильтры хоста (маркер не создан), проверки выполнены', not marker.exists() and len(docker_runs(f)) == 1, (marker.exists(), err))

    o = Proj('chk-waive-tamper', config=None)
    o.cr('start', 'waive и подмена конфигурации', '--id', 'chk-wt', '--kind', 'backend')
    o.cr('waive', 'main', 'в проекте пока нет автоматических проверок, проверка вручную')
    o.commit('.agents/agentq.json', json.dumps({'checks': {'backend': [['true']]}, 'check_image': 'aq-fake:1'}))
    o.commit('backend/app.py', ADD2)
    o.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = o.cr('review')
    check('waive не обходит запрет менять .agents/agentq.json (проверка до ветки waive)', rc == 2 and 'agentq.json' in err and o.count('codex') == 0, err)

    rn = Proj('chk-rename')
    rn.cr('start', 'переименование конфигурации', '--id', 'chk-rn', '--kind', 'backend')
    rn.git('mv', '.agents/agentq.json', '.agents/saved.json')
    rn.git('commit', '-q', '-m', 'rename')
    rn.commit('backend/app.py', ADD2)
    rn.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = rn.cr('review')
    check('переименование/удаление конфигурации кандидатом отклонено (без обнаружения переименований)', rc == 2 and 'agentq.json' in err and rn.count('codex') == 0, err)

    bk = Proj('chk-badblob')
    bk.cr('start', 'нечитаемая конфигурация базы', '--id', 'chk-bb', '--kind', 'backend')
    bk.commit('backend/app.py', ADD2)
    oid = bk.git('rev-parse', 'HEAD~1:.agents/agentq.json')
    objf = bk.repo / '.git/objects' / oid[:2] / oid[2:]
    if objf.exists():
        os.chmod(objf, 0o666)
        objf.unlink()
        bk.script('codex', [{'text': 'VERDICT: PASS'}])
        rc, out, err = bk.cr('review')
        rc2, _, err2 = bk.cr('waive', 'main', 'проверок якобы нет, потому что конфигурация не читается совсем')
        check('нечитаемый blob конфигурации базы — блокер, а не «проверок нет» (waive тоже невозможен)',
              rc == 2 and 'не прочитана' in err + out and rc2 == 2 and bk.count('codex') == 0, (err, err2))
    else:
        check('нечитаемый blob: объект упакован, сценарий пропущен', True)

    fl = Proj('chk-flood')
    fl.cr('start', 'огромный вывод', '--id', 'chk-fd', '--kind', 'backend')
    fl.commit('backend/app.py', ADD2 + '# FLOOD_test FAIL_test\n')
    fl.script('codex', [{'text': 'VERDICT: PASS'}])
    rc, out, err = fl.cr('review')
    tail = json.loads((fl.repo / '.ai/task/state.json').read_text(encoding='utf-8'))['units']['main']['checks']['tail']
    check('огромный вывод кандидата читается ограниченным буфером: красная проверка зафиксирована, хвост ограничен', rc == 1 and len(tail) < 12000, (rc, len(tail), err))


def state_edit(p, fn):
    f = p.repo / '.ai/task/state.json'
    s = json.loads(f.read_text(encoding='utf-8'))
    fn(s)
    f.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding='utf-8')


def t_review_fixes():
    # замечания независимого ревью Codex (раунд 1) — регрессии
    p = Proj('fix1', extra={'next.config.js': 'module.exports = {};\n'})
    p.cr('start', 'регрессии ревью', '--id', 'fix-1', '--kind', 'mixed', '--contract-file', p.contract())
    rc, _, err = p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES, '--context', 'next.config.js')
    check('контекст-конфиг (защищённый) не передаётся Codex-автору', rc == 2 and 'защищ' in err.lower(), err)
    rc, _, err = p.cr('unit', 'fe', '--kind', 'frontend', '--files', 'frontend/src/Card.tsx')
    check('единица создана для проверки алиасов', rc == 0, err)
    rc, _, err = p.cr('unit', 'fe2', '--kind', 'frontend', '--files', 'frontend/src/card.tsx')
    check('владение: алиас по регистру отклонён', rc == 2 and 'принадлежат' in err, err)

    link_ok = True
    q = Proj('fix2')
    outside = q.root / 'outside.txt'
    outside.write_text('внешний файл\n', encoding='utf-8')
    link = q.repo / 'frontend/src/Link.tsx'
    try:
        os.symlink(outside, link)
        q.git('add', '-A')
        q.git('commit', '-q', '-m', 'link')
    except (OSError, NotImplementedError):
        link_ok = False
    if link_ok:
        q.cr('start', 'ссылка', '--id', 'link-1', '--kind', 'mixed', '--contract-file', q.contract())
        rc, _, err = q.cr('unit', 'fe', '--kind', 'frontend', '--files', 'frontend/src/Link.tsx')
        check('символическая ссылка в списке файлов отклонена, внешний файл не затронут',
              rc == 2 and 'символическая' in err and outside.read_text(encoding='utf-8') == 'внешний файл\n', err)
    else:
        print('SKIP  символическая ссылка: нет прав на создание ссылок в этой среде')

    r = Proj('fix3')
    r.cr('start', 'восстановление ревью', '--id', 'rec-rev', '--kind', 'backend')
    r.commit('backend/app.py', 'def add(a, b):\n    return a+b\n')
    r.script('codex', [{'text': 'VERDICT: PASS'}, {'text': 'VERDICT: FIX'}])
    rc, _, _ = r.cr('review')
    # сбой процесса после сохранения receipt, но до сохранения вердикта
    def crash(s):
        u = s['units']['main']
        u['verdict'], u['reviewed'], u['review_applied'], u['status'] = None, None, 0, 'open'
    state_edit(r, crash)
    rc2, _, err = r.cr('review')
    check('восстановление ревью: сохранённый ответ применяется без нового вызова', rc == 0 and rc2 == 0 and r.count('codex-review') == 1, r.calls())

    f = Proj('fix4')
    f.cr('start', 'восстановление автора', '--id', 'rec-aut', '--kind', 'mixed', '--contract-file', f.contract())
    f.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    f.script('codex', [{'files': FILES_OK1}, {'files': FILES_OK2}])
    f.script('claude', [OK])
    rc, _, _ = f.cr('author', 'fe')
    def crash2(s):
        u = s['units']['fe']
        u['candidate'], u['author_applied'], u['unmet'] = None, 0, ['потеряно']
    state_edit(f, crash2)
    rc2, _, err = f.cr('author', 'fe')
    st = json.loads((f.repo / '.ai/task/state.json').read_text(encoding='utf-8'))['units']['fe']
    check('восстановление автора: коммит принят по receipt, повторного вызова нет, метаданные ответа восстановлены',
          rc == 0 and rc2 == 0 and f.count('codex-author') == 1 and st['candidate'] and st['unmet'] == [], (f.calls(), err))
    # подмена: содержимое коммита не совпадает с ответом автора
    g = Proj('fix5')
    g.cr('start', 'подмена коммита', '--id', 'tamper', '--kind', 'mixed', '--contract-file', g.contract())
    g.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    g.script('codex', [{'files': FILES_OK1}])
    g.cr('author', 'fe')
    wt = next(g.wt.glob('*'))
    (wt / FE_FILES[0]).write_text(APP_V1 + '// лишнее\n', encoding='utf-8', newline='\n')
    subprocess.run(['git', '-C', str(wt), 'commit', '-q', '-a', '--amend', '--no-edit', '-m', 'x [aq:tamper/fe/r1]'], capture_output=True)
    state_edit(g, lambda s: s['units']['fe'].update(candidate=None, author_applied=0))
    rc, _, err = g.cr('author', 'fe')
    check('подмена содержимого коммита после сбоя не принимается как кандидат', rc == 2 and 'не совпадает' in err, err)

    h = Proj('fix6')
    h.cr('start', 'приёмка не по статусу', '--id', 'acc-1', '--kind', 'mixed', '--contract-file', h.contract())
    h.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    h.script('codex', [{'files': FILES_OK1}])
    h.script('claude', [OK])
    h.cr('cycle', 'fe')
    h.cr('integrate', 'fe')
    h.git('checkout', '-q', '-b', 'other-branch')
    rc, out, _ = h.cr('finish')
    check('finish не подтверждает приёмку после смены ветки', rc == 1 and 'НЕ ПОДТВЕРЖДЕНА' in out, out)


FIX_T = {'text': '- [MAJOR] backend/app.py:2 — плохо.\nVERDICT: FIX'}
FIXC_T = {'text': '- [MAJOR] backend/app.py:2 — осталось.\n[ЗАКРЫТО] прежнее замечание исправлено\nVERDICT: FIX'}
BODY_T = 'def add(a, b):\n    return a + b  # {n}\n'
NOTE_T = 'упростить: убрать вложенные ветки и проверить границы в одном месте'


def rstate(x):
    return json.loads((x.repo / '.ai/task/state.json').read_text(encoding='utf-8'))


def rledger(x):
    return json.loads((x.repo / '.ai/ledger.json').read_text(encoding='utf-8'))


def review_n(x, n, uid=None):
    """Коммит правки и ревью; возвращает (код, stdout, stderr)."""
    x.commit('backend/app.py', BODY_T.format(n=n))
    return x.cr('review', *([uid] if uid else []))


def t_progress_policy():
    """Контрольные точки 3/3/12 и контроль содержательного прогресса: закрытые замечания разрешают продолжение, два круга без прогресса
    возвращают управление тимлиду, повторное зацикливание после пересмотра — окончательная остановка, явный предел Ивана жёсткий."""
    p = Proj('prog')
    p.cr('start', 'прогресс', '--id', 'pg-1', '--kind', 'backend')
    p.script('codex', [FIX_T, FIXC_T, FIXC_T, FIXC_T, FIXC_T, {'text': 'VERDICT: PASS'}])
    rcs = [review_n(p, n)[0] for n in range(1, 6)]
    check('прогресс: контрольная точка (3 ревью) не запрещает продолжение — закрытые замечания, ревью 4 и 5 состоялись',
          rcs == [1] * 5 and p.count('codex-review') == 5, rcs)
    first = (p.fake / 'codex-prompt-0.txt').read_text(encoding='utf-8')
    second = (p.fake / 'codex-prompt-1.txt').read_text(encoding='utf-8')
    check('запрос reviewer: критерии готовности, границы, минимальное решение и доказательность BLOCKER/MAJOR',
          all(x in first for x in ('КРИТЕРИИ ГОТОВНОСТИ', 'ГРАНИЦЫ', 'МИНИМАЛЬНОЕ ДОСТАТОЧНОЕ', 'ДОКАЗАТЕЛЬНОСТЬ', 'сценарий')), first[:300])
    check('первое ревью без статусов прежних замечаний, повторное — со статусами [ЗАКРЫТО]/[ОСТАЛОСЬ]/[НОВОЕ] и прежним отчётом',
          'СТАТУС ПРЕЖНИХ ЗАМЕЧАНИЙ' not in first and 'СТАТУС ПРЕЖНИХ ЗАМЕЧАНИЙ' in second and '[ОСТАЛОСЬ]' in second and '=== ПРОШЛЫЕ ЗАМЕЧАНИЯ ===' in second)
    st = rstate(p)['units']['main']
    check('история кругов в состоянии: прогресс по закрытию замечаний, а не по числу', st['stall'] == 0 and st['progress'] is True
          and [r['progress'] for r in st['rounds']] == [True] * 4, st['rounds'])
    rc, _, _ = review_n(p, 6)
    n6 = p.count('codex-review')
    rc2, out, _ = p.cr('review')
    check('принятый неизменённый кандидат: повторное ревью не вызывает модель', rc == 0 and rc2 == 0 and p.count('codex-review') == n6 == 6 and 'уже проверен' in out, out)
    rc, out, err = p.cr('finish')
    check('после PASS и проверок: приёмка VERIFIED без дополнительных кругов', rc == 0 and 'ACCEPTANCE: VERIFIED' in out, out + err)

    q = Proj('prog-stop')
    q.cr('start', 'остановка', '--id', 'ps-1', '--kind', 'backend')
    q.script('codex', [FIX_T, FIXC_T, FIX_T, FIX_T, FIX_T, FIX_T])
    for n in (1, 2, 3):
        review_n(q, n)
    q.commit('backend/app.py', BODY_T.format(n=4))
    rc, _, err = q.cr('review')
    check('контрольная точка без подтверждённого прогресса последнего круга: ревью остановлено, вызова нет, подсказан rethink',
          rc == 3 and 'контрольная точка' in err and 'rethink' in err and q.count('codex-review') == 3, err)
    rc, _, err = q.cr('rethink', 'main', 'коротко')
    check('rethink: короткое описание подхода отказано', rc == 2 and '20' in err, err)
    rc, out, err = q.cr('rethink', 'main', NOTE_T)
    check('rethink: причина и более простой подход записаны', rc == 0 and rstate(q)['units']['main']['rethinks'][0]['note'] == NOTE_T, out + err)
    rc, _, err = q.cr('review')
    prompt = sorted(q.fake.glob('codex-prompt-*.txt'), key=lambda f: int(f.stem.split('-')[-1]))[-1].read_text(encoding='utf-8')
    check('после пересмотра круг разрешён, и новый подход идёт в запрос reviewer', rc == 1 and q.count('codex-review') == 4 and NOTE_T in prompt, err)
    review_n(q, 5)
    rc, _, err = q.cr('review')
    check('повторное зацикливание после пересмотра: окончательная остановка с блокером, выполненной работой и рекомендацией, вызова нет',
          rc == 3 and 'повторное зацикливание' in err and 'Рекомендуемое решение' in err and 'выполнено' in err and q.count('codex-review') == 4, err)
    rc, _, err = q.cr('rethink', 'main', NOTE_T + ' ещё раз')
    check('второй пересмотр на той же единице невозможен', rc == 3 and q.count('codex-review') == 4, err)
    rc, out, _ = q.cr('status')
    check('status показывает круги без прогресса и факт пересмотра', 'без прогресса подряд' in out and 'пересмотр подхода был' in out, out)

    s = Proj('prog-stall')
    s.cr('start', 'два круга', '--id', 'pst-1', '--kind', 'backend')
    s.script('codex', [FIX_T] * 5)
    for n in (1, 2, 3):
        review_n(s, n)
    rc, _, err = review_n(s, 4)
    check('два круга подряд без прогресса: автоматическая передача остановлена, вызова нет, управление тимлиду', rc == 3 and 'СТОП' in err and 'другой, более простой подход' in err
          and s.count('codex-review') == 3, err)
    rc, _, err = s.cr('rethink', 'main', NOTE_T)
    rc2, _, _ = s.cr('review')
    check('после пересмотра подхода тимлидом круг возобновляется', rc == 0 and rc2 == 1 and s.count('codex-review') == 4, err)

    h = Proj('prog-hard')
    h.cr('start', 'предел', '--id', 'ph-1', '--kind', 'backend', '--max-calls', '2')
    h.script('codex', [FIXC_T] * 4)
    review_n(h, 1)
    review_n(h, 2)
    rc, _, err = review_n(h, 3)
    check('явный предел Ивана жёсткий даже при прогрессе: третий вызов отказан', rc == 3 and 'предел' in err.lower() and h.count('codex-review') == 2, err)
    h.cr('finish', '--abandon')
    check('предел записан в журнал', rledger(h)[0]['max_calls'] == 2)
    rc, _, err = h.cr('start', 'предел', '--id', 'ph-2', '--kind', 'backend', '--continues', 'ph-1')
    check('продолжение не поднимает предел Ивана: старт отказан', rc == 3 and 'предел' in err.lower() and not (h.repo / '.ai/task/state.json').exists(), err)

    c = Proj('prog-chain')
    c.cr('start', 'цепочка', '--id', 'pc-1', '--kind', 'backend')
    c.script('codex', [FIX_T] * 3)
    state_edit(c, lambda s: s.update(inherited={'author': 0, 'review': 12, 'calls': 12, 'units': {}, 'verdicts': {}, 'meta': {}}))
    rc, _, err = review_n(c, 1)
    check('контрольная точка цепочки (12 вызовов) без подтверждённого прогресса: вызова нет', rc == 3 and '12/12' in err and c.count('codex-review') == 0, err)
    state_edit(c, lambda s: s['units']['main'].update(progress=True))
    rc, _, err = c.cr('review')
    check('с подтверждённым прогрессом цепочка продолжается за 12', rc == 1 and c.count('codex-review') == 1, err)


def t_cont_a():
    """Продолжение (--continues): расход и история прогресса принадлежат работе; неподдерживаемые операции — безопасный отказ."""
    a = Proj('contA', extra={'backend/b.py': 'B = 1\n'})
    a.cr('start', 'работа', '--id', 'ca-1', '--kind', 'mixed', '--contract-file', a.contract())
    a.cr('unit', 'main', '--kind', 'backend', '--files', 'backend/app.py')
    a.cr('unit', 'other', '--kind', 'backend', '--files', 'backend/b.py')
    a.cr('unit', 'prot', '--kind', 'protected', '--files', 'package.json')
    a.script('codex', [FIX_T] * 12)
    for n in range(3):
        a.commit('backend/app.py', BODY_T.format(n=n))
        a.cr('review', 'main')
    a.commit('package.json', '{"a": 1}\n')
    a.cr('review', 'prot')
    a.cr('finish', '--abandon')
    n0 = a.count('codex-review')
    a.cr('start', 'работа', '--id', 'ca-2', '--kind', 'mixed', '--contract-file', a.contract(), '--continues', 'ca-1')
    for uid, kind, files in (('retry', 'backend', ['backend/app.py']), ('ab', 'backend', ['backend/app.py', 'backend/b.py']),
                             ('prot2', 'backend', ['package.json'])):
        rc, _, err = a.cr('unit', uid, '--kind', kind, '--files', *files)
        check(f'A: {uid} (переименование/объединение/смена рода под новым uid) — безопасный отказ, единица не создана',
              rc == 2 and 'пересекается' in err and uid not in rstate(a)['units'], err)
    rc, _, err = a.cr('unit', 'other', '--kind', 'backend', '--files', 'backend/b.py')
    check('A: единица без расхода, не пересекающаяся с прежней работой, создаётся', rc == 0, err)
    rc, _, err = a.cr('unit', 'prot', '--kind', 'backend', '--files', 'package.json')
    check('A: тот же uid и то же семейство ролей (protected → backend) продолжает работу, а не открывает новый бюджет', rc == 0, err)
    a.cr('unit', 'main', '--kind', 'backend', '--files', 'backend/app.py')
    rc, out, _ = a.cr('status')
    check('A: тот же uid наследует расход и историю прогресса (ревью 3/3, два круга без прогресса)',
          'main: backend' in out and 'ревью 3/3' in out and 'без прогресса подряд 2' in out and 'ревью 1/3' in out, out)
    a.commit('backend/app.py', BODY_T.format(n=9))
    rc, _, err = a.cr('review', 'main')
    check('A: остановка по прогрессу сохраняется в продолжении: вызова нет', rc == 3 and 'СТОП' in err and a.count('codex-review') == n0, err)
    rc, _, err = a.cr('rethink', 'main', NOTE_T)
    rc2, _, _ = a.cr('review', 'main')
    check('A: пересмотр подхода в продолжении разрешает круг', rc == 0 and rc2 == 1 and a.count('codex-review') == n0 + 1, err)
    a.cr('finish', '--abandon')
    a.cr('start', 'работа', '--id', 'ca-3', '--kind', 'mixed', '--contract-file', a.contract(), '--continues', 'ca-2')
    a.cr('unit', 'main', '--kind', 'backend', '--files', 'backend/app.py')
    check('A: пересмотр подхода переносится по цепочке (второго нет)', len(rstate(a)['units']['main']['rethinks']) == 1)

    L = Proj('contL')
    L.cr('start', 'работа', '--id', 'cl-1', '--kind', 'backend')
    L.script('codex', [FIX_T] * 12)
    for n in range(3):
        review_n(L, n)
    L.cr('finish', '--abandon')
    led = rledger(L)
    led[0].pop('unit_meta', None)
    (L.repo / '.ai/ledger.json').write_text(json.dumps(led), encoding='utf-8')
    L.cr('start', 'работа', '--id', 'cl-2', '--kind', 'mixed', '--contract-file', L.contract(), '--continues', 'cl-1')
    rc, _, err = L.cr('unit', 'retry', '--kind', 'backend', '--files', 'backend/app.py')
    check('A: журнал прежней версии, новый uid не сопоставить: отказ с причиной', rc == 2 and 'нельзя сопоставить' in err and 'retry' not in rstate(L)['units'], err)
    rc, _, err = L.cr('unit', 'main', '--kind', 'backend', '--files', 'backend/app.py')
    check('A: журнал прежней версии, прежний uid: принят, основание diff — база прежней задачи', rc == 0 and rstate(L)['units']['main']['base'] == led[0]['base'], err)
    n0 = L.count('codex-review')
    rc, _, err = review_n(L, 9, 'main')
    check('A: расход прежней версии учтён: контрольная точка 3/3 без подтверждённого прогресса — вызова нет', rc == 3 and L.count('codex-review') == n0, err)


def t_cont_g():
    """Продолжение (--continues): исходная база, все прежние замечания; потерянный отчёт — остановка до вызова (G)."""
    g = Proj('contG')
    g.cr('start', 'работа', '--id', 'cg-1', '--kind', 'backend')
    base0 = g.git('rev-parse', 'HEAD')
    g.script('codex', [FIX_T] * 6)
    g.commit('backend/app.py', 'def add(a, b):\n    return a + b  # MARKER_FIRST\n')
    g.cr('review')
    rep1 = rstate(g)['units']['main']['last_report']
    g.cr('finish', '--abandon')
    g.cr('start', 'работа', '--id', 'cg-2', '--kind', 'backend', '--continues', 'cg-1')
    check('G: база единицы продолжения — исходная база прежней работы', rstate(g)['units']['main']['base'] == base0)
    g.commit('backend/app.py', 'def add(a, b):\n    return a + b  # MARKER_FIRST\n\n\ndef sub(a, b):\n    return a - b\n')
    n0 = g.count('codex-review')
    os.replace(rep1, rep1 + '.moved')
    rc, _, err = g.cr('review')
    check('G: отчёт прежнего ревью потерян — ревью не запускается, вызова нет', rc == 2 and 'не найден' in err and g.count('codex-review') == n0, err)
    os.replace(rep1 + '.moved', rep1)
    rc, _, err = g.cr('review')
    prompt = sorted(g.fake.glob('codex-prompt-*.txt'), key=lambda f: int(f.stem.split('-')[-1]))[-1].read_text(encoding='utf-8')
    diff = prompt.split('=== DIFF ===')[-1]
    check('G: ревью продолжения видит изменения прежней работы (добавленная строка первой задачи в diff)',
          rc == 1 and any(x.startswith('+') and 'MARKER_FIRST' in x for x in diff.splitlines()), err)
    check('G: прежние замечания идут в запрос дословно', 'ПРОШЛЫЕ ЗАМЕЧАНИЯ' in prompt and Path(rep1).read_text(encoding='utf-8').strip() in prompt)

    L = Proj('contGL')
    L.cr('start', 'работа', '--id', 'gl-1', '--kind', 'backend')
    L.script('codex', [FIX_T] * 3)
    review_n(L, 1)
    L.cr('finish', '--abandon')
    led = rledger(L)
    led[0].pop('unit_meta', None)
    (L.repo / '.ai/ledger.json').write_text(json.dumps(led), encoding='utf-8')
    for f in (L.repo / '.ai/reviews').glob('*.md'):
        f.unlink()
    L.cr('start', 'работа', '--id', 'gl-2', '--kind', 'backend', '--continues', 'gl-1')
    n0 = L.count('codex-review')
    rc, _, err = review_n(L, 2)
    check('G: у прежней единицы были ревью, отчёт журнала прежней версии потерян — остановка до вызова, не «замечаний нет»',
          rc == 2 and 'не найден' in err and L.count('codex-review') == n0, err)


def t_cont_f():
    """Продолжение: интегрированная frontend-единица не ломает backend-ревью и сохраняет исходную базу (G, регрессия)."""
    p = Proj('contF')
    p.cr('start', 'фронт и бэк', '--id', 'cf-1', '--kind', 'backend')
    base0 = p.git('rev-parse', 'HEAD')
    p.cr('unit', 'ui', '--kind', 'frontend', '--files', *FE_FILES)
    p.script('codex', [{'files': FILES_OK1}, FIX_T, FIX_T, FIX_T])
    p.script('claude', [OK, OK])
    rc, out, err = p.cr('cycle', 'ui')
    rc2, out2, err2 = p.cr('integrate', 'ui')
    check('F: frontend-единица проверена и интегрирована', rc == 0 and rc2 == 0, out + err + out2 + err2)
    review_n(p, 1, 'main')
    p.cr('finish', '--abandon')
    p.cr('start', 'фронт и бэк', '--id', 'cf-2', '--kind', 'backend', '--continues', 'cf-1')
    main = rstate(p)['units']['main']
    check('F: исключение принятых файлов frontend перенесено вместе с исходной базой', sorted(main['carried_exclude']) == sorted(FE_FILES) and main['base'] == base0, main)
    rc, out, err = review_n(p, 2)
    check('F: backend-ревью после интегрированной frontend-единицы не отвергается ложной ошибкой РОЛЬ', rc == 1 and 'РОЛЬ' not in err and p.count('codex-review') == 2, out + err)
    p.commit('frontend/src/App.tsx', APP_V2)
    rc, _, err = p.cr('review')
    check('F: правка принятых файлов frontend без их единицы не проходит незамеченной', rc == 2 and 'прежних принятых единиц' in err and p.count('codex-review') == 2, err)
    rc, _, err = p.cr('unit', 'ui', '--kind', 'frontend', '--files', *FE_FILES)
    ui = rstate(p)['units']['ui']
    check('F: frontend-единица продолжения: ревью берёт исходную базу прежней работы, а база рабочей копии автора не закрепляется заранее',
          rc == 0 and ui['review_base'] == base0 and ui['base'] is None, err + str(ui))


def t_cont_c():
    """Продолжение: журнал проверяется на согласованность до любых вызовов, в том числе после start (C)."""
    c = Proj('contC')
    c.cr('start', 'работа', '--id', 'cc-1', '--kind', 'backend')
    c.script('codex', [FIX_T] * 12)
    for n in range(3):
        review_n(c, n)
    c.cr('finish', '--abandon')
    good = rledger(c)
    lp = c.repo / '.ai/ledger.json'
    cases = {
        'перенесённых больше вызовов': lambda e: e[0].update(inherited_calls=4),
        'вызовов меньше расхода по ролям': lambda e: e[0].update(calls=1),
        'вызовов больше расхода по ролям': lambda e: e[0].update(calls=9),
        'перенесённые не равны расходу цепочки': lambda e: e[0].update(inherited_calls=2),
        'единицы не сходятся с ролями': lambda e: e[0]['units']['main'].update(review=0),
        'вызовы не число': lambda e: e[0].update(calls='три'),
        'отрицательные роли': lambda e: e[0]['roles'].update(review=-3, author=6),
        'повторный id': lambda e: e.append(json.loads(json.dumps(e[0]))),
        'неизвестный результат': lambda e: e[0].update(result='OK'),
        'предел вызовов не число': lambda e: e[0].update(max_calls='два'),
        'вердикт не разобран': lambda e: e[0].update(verdicts={'main': {'value': 'ДА', 'sha': 'x'}}),
        'запись не объект': lambda e: e.__setitem__(0, 7),
    }
    for name, mut in cases.items():
        e = json.loads(json.dumps(good))
        mut(e)
        lp.write_text(json.dumps(e), encoding='utf-8')
        raw = lp.read_bytes()
        n0 = c.count('codex-review')
        rc, _, err = c.cr('start', 'работа', '--id', 'cc-2', '--kind', 'backend', '--continues', 'cc-1')
        check(f'C: {name} — остановка до состояния и вызовов, журнал не изменён',
              rc == 2 and 'журнал повреждён' in err and 'Traceback' not in err and not (c.repo / '.ai/task/state.json').exists()
              and lp.read_bytes() == raw and c.count('codex-review') == n0, err[-200:])
    for name, raw in (('обрыв записи', json.dumps(good)[:40]), ('не список', '{"a": 1}')):
        lp.write_text(raw, encoding='utf-8')
        rc, _, err = c.cr('start', 'работа', '--id', 'cc-2', '--kind', 'backend', '--continues', 'cc-1')
        check(f'C: {name} — остановка с понятной ошибкой, без трассировки', rc == 2 and 'Traceback' not in err and not (c.repo / '.ai/task/state.json').exists(), err[-200:])
    lp.write_text(json.dumps(good), encoding='utf-8')
    rc, _, err = c.cr('start', 'работа', '--id', 'cc-2', '--kind', 'backend', '--continues', 'cc-1')
    st = rstate(c) if rc == 0 else {}
    check('C: исправный журнал принимается, расход цепочки перенесён, контрольные точки не изменены', rc == 0 and st['inherited']['calls'] == 3 and st['budget']['total'] == 12, err)
    n0 = c.count('codex-review')
    lp.write_text(json.dumps(good)[:40], encoding='utf-8')   # повреждение после start, до следующего платного вызова
    rc, _, err = review_n(c, 50)
    check('C: журнал повреждён после start — остановка ДО платного вызова, а не на finish', rc == 2 and 'журнал повреждён' in err and c.count('codex-review') == n0, err[-200:])
    lp.write_text(json.dumps(good), encoding='utf-8')
    c.cr('rethink', 'main', NOTE_T)
    rc, _, err = c.cr('review')
    check('C: журнал восстановлен — работа продолжается без потери состояния', rc == 1 and c.count('codex-review') == n0 + 1, err)
    c.cr('finish', '--abandon')
    rc, _, err = c.cr('start', 'работа', '--id', 'cc-3', '--kind', 'backend', '--continues', 'cc-2')
    check('C: цепочка из трёх задач согласована', rc == 0 and rstate(c)['inherited']['calls'] == 4, err)
    (c.repo / '.ai/task/state.json').write_text('[]', encoding='utf-8')
    rc, _, err = c.cr('status')
    check('C: состояние задачи неверного типа — остановка с понятной ошибкой, без трассировки',
          rc == 2 and 'повреждено' in err and 'Traceback' not in err, err[-200:])


def t_cont_h():
    """Независимая приёмка 1: область, принятый результат, проверки и остановка при продолжении; журнал без разбивки по единицам."""
    a = Proj('contH', extra={'backend/b.py': 'B = 1\n'})
    a.cr('start', 'работа', '--id', 'ch-1', '--kind', 'mixed', '--contract-file', a.contract())
    a.cr('unit', 'p1', '--kind', 'backend', '--files', 'backend/app.py')
    a.cr('unit', 'p2', '--kind', 'backend', '--files', 'backend/b.py', 'package.json')
    a.script('codex', [{'text': 'VERDICT: PASS'}, FIX_T, FIX_T])
    a.commit('backend/app.py', BODY_T.format(n=1))
    a.cr('review', 'p1')
    a.commit('backend/b.py', 'B = 2\n')
    a.cr('review', 'p2')
    a.cr('finish', '--abandon')
    a.cr('start', 'работа', '--id', 'ch-2', '--kind', 'backend', '--continues', 'ch-1')
    check('H: единица без файлов (вся задача) не обходит сопоставление с прежними единицами с файлами: старт отказан, состояние не создано',
          not (a.repo / '.ai/task/state.json').exists())
    a.cr('start', 'работа', '--id', 'ch-2', '--kind', 'mixed', '--contract-file', a.contract(), '--continues', 'ch-1')
    rc, _, err = a.cr('unit', 'p2', '--kind', 'backend', '--files', 'backend/b.py')
    check('H: сужение области той же единицы при продолжении отказано (изменения вне нового списка ушли бы из ревью)',
          rc == 2 and 'область единицы' in err and 'p2' not in rstate(a)['units'], err)
    n0 = a.count('codex-review')
    a.cr('unit', 'p1', '--kind', 'backend', '--files', 'backend/app.py')
    u = rstate(a)['units']['p1']
    check('H: принятый неизменённый результат прежней работы остаётся принятым (PASS на том же SHA), модель не вызывается',
          u['status'] == 'accepted' and u['verdict']['value'] == 'PASS' and u['reviewed'] == u['verdict']['sha'] and a.count('codex-review') == n0, u)

    c = Proj('contH2')
    c.cr('start', 'проверки', '--id', 'cp-1', '--kind', 'backend')
    c.script('codex', [FIX_T] * 6)
    review_n(c, 1)
    c.commit('backend/app.py', BODY_T.format(n=2) + '# FAIL_test\n')
    rc, _, _ = c.cr('review')
    check('H: падающие проверки при открытых замечаниях — круг без прогресса, модель не вызывалась', rc == 1 and c.count('codex-review') == 1 and rstate(c)['units']['main']['open_checks'], rc)
    c.cr('finish', '--abandon')
    c.cr('start', 'проверки', '--id', 'cp-2', '--kind', 'backend', '--continues', 'cp-1')
    check('H: состояние «проверки падали» переносится при продолжении', rstate(c)['units']['main']['open_checks'] is True)
    rc, _, err = review_n(c, 3)
    m = rstate(c)['units']['main']
    check('H: исправление проверок и ревью на одном кандидате — один круг с прогрессом (не два, не затёрт результатом ревью)',
          rc == 1 and m['progress'] is True and m['stall'] == 0 and len(m['rounds']) == 1 and 'исправлены падавшие проверки' in m['rounds'][-1]['why']
          and 'ревью FIX' in m['rounds'][-1]['why'], m)

    s = Proj('contH3')
    s.cr('start', 'остановка', '--id', 'cs-1', '--kind', 'backend')
    s.script('codex', [FIX_T] * 6)
    for n in (1, 2, 3):
        review_n(s, n)
    s.commit('backend/app.py', BODY_T.format(n=4) + '# FAIL_test\n')
    s.cr('review')
    n0 = s.count('codex-review')
    rc, _, err = review_n(s, 5)
    check('H: исправление проверок не снимает уже наступившую остановку (нужен rethink): вызова нет',
          rc == 3 and 'СТОП' in err and s.count('codex-review') == n0 and rstate(s)['units']['main']['stall'] == 2, err)

    d = Proj('contH4')
    d.cr('start', 'старый журнал', '--id', 'cd-1', '--kind', 'backend')
    d.script('codex', [FIX_T])
    review_n(d, 1)
    d.cr('finish', '--abandon')
    led = rledger(d)
    led[0].pop('units'), led[0].pop('unit_meta')
    (d.repo / '.ai/ledger.json').write_text(json.dumps(led), encoding='utf-8')
    rc, _, err = d.cr('start', 'старый журнал', '--id', 'cd-2', '--kind', 'backend', '--continues', 'cd-1')
    check('H: журнал прежней версии без разбивки по единицам: замечания не восстановить — продолжение отказано, а не «замечаний нет»',
          rc == 2 and 'без описаний' in err and not (d.repo / '.ai/task/state.json').exists(), err)


def t_cont_i():
    """Независимая приёмка 2: принятая frontend-единица, круг по проверкам после прерывания, смешанный журнал старых форматов."""
    f = Proj('contI')
    f.cr('start', 'фронт', '--id', 'ci-1', '--kind', 'mixed', '--contract-file', f.contract())
    f.cr('unit', 'ui', '--kind', 'frontend', '--files', *FE_FILES)
    f.cr('unit', 'p2', '--kind', 'backend', '--files', 'backend/app.py')
    f.script('codex', [{'files': FILES_OK1}, FIX_T])
    f.script('claude', [OK])
    rc, _, _ = f.cr('cycle', 'ui')
    rc2, _, _ = f.cr('integrate', 'ui')
    f.commit('backend/app.py', BODY_T.format(n=1))
    f.cr('review', 'p2')
    f.cr('finish', '--abandon')
    f.cr('start', 'фронт', '--id', 'ci-2', '--kind', 'mixed', '--contract-file', f.contract(), '--continues', 'ci-1')
    f.cr('unit', 'ui', '--kind', 'frontend', '--files', *FE_FILES)
    n0 = len(f.calls())
    rc3, _, err = f.cr('cycle', 'ui')
    ui = rstate(f)['units']['ui']
    check('I: принятая и интегрированная frontend-единица остаётся принятой при продолжении: автор и reviewer не вызываются',
          rc == 0 and rc2 == 0 and rc3 == 0 and ui['status'] == 'integrated' and ui['verdict']['value'] == 'PASS' and len(f.calls()) == n0, err + str(ui))

    c = Proj('contI2')
    c.cr('start', 'проверки', '--id', 'cj-1', '--kind', 'backend')
    c.script('codex', [FIX_T] * 4)
    review_n(c, 1)
    prev = {k: rstate(c)['units']['main'][k] for k in ('verdict', 'reviewed', 'last_report', 'review_applied')}
    c.commit('backend/app.py', BODY_T.format(n=2) + '# FAIL_test\n')
    c.cr('review')
    review_n(c, 3)
    n0 = c.count('codex-review')
    state_edit(c, lambda s: s['units']['main'].update(prev))   # прерывание после засчитанного круга по проверкам, до применения вердикта ревью
    rc, _, err = c.cr('review')
    m = rstate(c)['units']['main']
    check('I: после прерывания круг по исправленным проверкам не заменяется «без прогресса» результатом ревью того же кандидата',
          rc == 1 and c.count('codex-review') == n0 and m['progress'] is True and m['stall'] == 0 and len([r for r in m['rounds'] if r['sha'] == m['reviewed']]) == 1, m['rounds'])

    r = Proj('contI4')
    r.cr('start', 'без вызовов', '--id', 'cr-1', '--kind', 'mixed', '--contract-file', r.contract())
    r.cr('unit', 'xx', '--kind', 'backend', '--files', 'backend/app.py')
    for n in (1, 2, 3):
        r.commit('backend/app.py', BODY_T.format(n=n) + '# FAIL_test\n')
        r.cr('review', 'xx')
    st = rstate(r)['units']['xx']
    r.cr('finish', '--abandon')
    r.cr('start', 'без вызовов', '--id', 'cr-2', '--kind', 'mixed', '--contract-file', r.contract(), '--continues', 'cr-1')
    rc, _, err = r.cr('unit', 'yy', '--kind', 'backend', '--files', 'backend/app.py')
    check('I: переименование единицы с историей кругов без прогресса, но без платных вызовов, отказано (stop не обходится новым uid)',
          st['stall'] == 2 and r.count('codex-review') == 0 and rc == 2 and 'пересекается' in err and 'yy' not in rstate(r)['units'], err + str(st['stall']))

    w = Proj('contI5', extra={'backend/b.py': 'B = 1'+chr(10)})
    w.cr('start', 'вся задача', '--id', 'cw-1', '--kind', 'backend')
    w.cr('unit', 'oth', '--kind', 'backend', '--files', 'backend/b.py')
    w.script('codex', [{'text': 'VERDICT: PASS'}, FIX_T])
    review_n(w, 1, 'main')
    w.commit('backend/b.py', 'B = 2'+chr(10))
    w.cr('review', 'oth')
    w.cr('finish', '--abandon')
    n0 = w.count('codex-review')
    w.cr('start', 'вся задача', '--id', 'cw-2', '--kind', 'backend', '--continues', 'cw-1')
    mn = rstate(w)['units']['main']
    check('I: принятая неизменённая единица без списка файлов (вся задача) остаётся принятой при продолжении, модель не вызывается',
          mn['status'] == 'accepted' and mn['verdict']['value'] == 'PASS' and w.count('codex-review') == n0, mn)

    z = Proj('contI6')
    z.cr('start', 'без расхода', '--id', 'cz-1', '--kind', 'mixed', '--contract-file', z.contract())
    z.cr('unit', 'api', '--kind', 'backend', '--files', 'backend/app.py')
    z.commit('backend/app.py', BODY_T.format(n=1))
    z.cr('finish', '--abandon')
    z.cr('start', 'без расхода', '--id', 'cz-2', '--kind', 'mixed', '--contract-file', z.contract(), '--continues', 'cz-1')
    rc, _, err = z.cr('unit', 'api2', '--kind', 'backend', '--files', 'backend/app.py')
    check('I: переименование прежней единицы вообще без расхода отказано (иначе исходная реализация осталась бы вне базы ревью)',
          rc == 2 and 'пересекается' in err and 'api2' not in rstate(z)['units'], err)
    rc, _, err = z.cr('unit', 'api', '--kind', 'backend', '--files', 'backend/app.py')
    check('I: тот же uid без расхода продолжает работу с исходной базой', rc == 0 and rstate(z)['units']['api']['base'] != z.git('rev-parse', 'HEAD'), err)

    n = Proj('contI7', extra={'backend/legacy.py': '# FAIL_test' + chr(10)})   # проверка красная и на базе: результат «не подтверждено» (ok=None)
    n.cr('start', 'не подтверждено', '--id', 'cn-1', '--kind', 'backend')
    n.script('codex', [FIX_T] * 3)
    state_edit(n, lambda s: s['units']['main'].update(open_checks=True, open_review=True, progress=False))
    rc, _, err = review_n(n, 1)
    m = rstate(n)['units']['main']
    check('I: проверки «не подтверждено» (красные и на базе) не засчитываются как исправление: прогресса нет, состояние «проверки падали» сохраняется, модель не вызвана',
          rc != 0 and m['progress'] is False and m['open_checks'] is True and not m['rounds'] and n.count('codex-review') == 0, str(m['rounds']) + err[-200:])

    d = Proj('contI3')
    (d.repo / '.ai').mkdir(exist_ok=True)
    head = d.git('rev-parse', 'HEAD')
    common = {'goal_sha': 'a', 'base': head, 'result': 'NOT_ACCEPTED', 'chain': 'x-1'}
    led = [{**common, 'id': 'x-1', 'roles': {'author': 0, 'review': 1}, 'calls': 1, 'inherited_calls': 0, 'resolved': True, 'continued_by': 'x-2'},
           {**common, 'id': 'x-2', 'roles': {'author': 1, 'review': 0}, 'units': {'main': {'author': 1, 'review': 0}}, 'calls': 2, 'inherited_calls': 1}]
    (d.repo / '.ai/ledger.json').write_text(json.dumps(led), encoding='utf-8')
    rc, _, err = d.cr('start', 'смешанный журнал', '--id', 'x-3', '--kind', 'backend', '--continues', 'x-2')
    check('I: цепочка старых форматов (часть расхода без разбивки по единицам) — отказ, более поздние описания замечаний не восстанавливают',
          rc == 2 and 'без описаний' in err and not (d.repo / '.ai/task/state.json').exists(), err)


def t_links():
    eng = load_engine()
    d = Path(tempfile.mkdtemp(prefix='aq-selftest-links-'))
    (d / 'real').mkdir()
    (d / 'real' / 'f.txt').write_text('x', encoding='utf-8')
    made = False
    if os.name == 'nt':
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(d / 'junc'), str(d / 'real')], capture_output=True).returncode == 0
    else:
        try:
            os.symlink(d / 'real', d / 'junc')
            made = True
        except OSError:
            pass
    if not made:
        print('SKIP  junction/symlink: создать не удалось')
        return
    check('has_link видит junction/symlink в компоненте пути', eng.has_link(d, 'junc/f.txt') and not eng.has_link(d, 'real/f.txt'))


def bg(p, *args, env=None):
    e = dict(p.env)
    e.update(env or {})
    return subprocess.Popen([sys.executable, str(ENGINE), *map(str, args)], cwd=p.repo, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8')


def done(pr, timeout=240):
    out, err = pr.communicate(timeout=timeout)
    return pr.returncode, out, err


def until(cond, secs=40):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def intervals(p):
    """Интервалы работы поддельного Codex из timeline.log: {роль: [(начало, конец)]}."""
    f = p.fake / 'timeline.log'
    cur, out = {}, {}
    for line in (f.read_text(encoding='utf-8').splitlines() if f.exists() else []):
        t, ev, name = line.split()
        if ev == 'start':
            cur.setdefault(name, []).append(float(t))
        else:
            out.setdefault(name, []).append((cur[name].pop(0), float(t)))
    return out


def drive(p, code):
    """Прямой вызов движка в отдельном процессе (имитация упавшего/параллельного процесса)."""
    pre = ('import importlib.util,os,sys\nfrom pathlib import Path\n'
           f's=importlib.util.spec_from_file_location("e",r"{ENGINE}");m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
           f'os.chdir(r"{p.repo}");t=m.Task(Path(r"{p.repo}")).load()\n')
    return subprocess.run([sys.executable, '-c', pre + code], capture_output=True, text=True, encoding='utf-8', env=p.env, timeout=120)


def t_parallel():
    # --- А. независимые backend и frontend работают одновременно, ветка уходит вперёд, интеграция слиянием, приёмка по итоговому SHA
    p = Proj('par')
    p.cr('start', 'параллельно', '--id', 'par-1', '--kind', 'mixed', '--contract-file', p.contract())
    p.cr('unit', 'be', '--kind', 'backend', '--files', 'backend/app.py')
    p.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    p.script('codex', [{'role': 'author', 'files': FILES_OK1, 'sleep': 4}, {'role': 'review', 'text': 'VERDICT: PASS', 'sleep': 4}])
    p.script('claude', [OK])
    fe = bg(p, 'cycle', 'fe')
    check('parallel: база frontend закреплена, автор Codex работает', until(lambda: rstate(p)['units']['fe']['base'] and p.count('codex-author') == 1))
    p.commit('backend/app.py', 'def add(a, b):\n    return a + b\n# параллельный коммит backend\n')
    t0 = time.time()
    be = bg(p, 'review', 'be')
    rc_s, _, _ = p.cr('status')
    check('parallel: status не ждёт работающих единиц', rc_s == 0 and time.time() - t0 < 4)
    rc_be, _, err_be = done(be)
    rc_fe, out_fe, err_fe = done(fe)
    check('parallel: обе части завершились (backend PASS, frontend PASS)', rc_be == 0 and rc_fe == 0, err_be + err_fe + out_fe)
    iv = intervals(p)
    a_iv, r_iv = iv['codex-author'][0], iv['codex-review'][0]
    check('parallel: вызовы Codex-автора (frontend) и Codex-ревью (backend) выполнялись одновременно', a_iv[0] < r_iv[1] and r_iv[0] < a_iv[1], iv)
    st = rstate(p)
    check('parallel: ничего не потеряно — 3 вызова с receipts, вердикты и кандидат на месте',
          len(st['calls']) == 3 and st['units']['be']['verdict']['value'] == 'PASS' and st['units']['fe']['status'] == 'accepted'
          and all((p.repo / '.ai/task/calls' / k.replace(':', '-') / 'receipt.json').exists() for k in st['calls']), st['calls'].keys())
    check('parallel: общий учёт вызовов цепочки = 3 (автор 1, ревью 2)', p.count('codex-author') == 1 and p.count('codex-review') == 1 and p.count('claude-review') == 1)
    head0 = p.git('rev-parse', 'HEAD')
    rc, out, err = p.cr('integrate', 'fe')
    merged = p.git('rev-parse', 'HEAD')
    check('parallel: интеграция — слияние, не ff (ветка ушла вперёд без файлов единицы)', rc == 0 and merged != head0 and
          p.git('rev-parse', 'HEAD^1') == head0 and p.git('rev-parse', 'HEAD^2') == rstate(p)['units']['fe']['candidate'], out + err)
    src = (p.fake / 'last-src-list.txt').read_text(encoding='utf-8').splitlines()
    check('parallel: проверки итогового SHA запущены на дереве слияния (backend + frontend вместе)', rstate(p)['units']['fe']['checks']['sha'] == merged
          and 'backend/app.py' in src and FE_FILES[0] in src, rstate(p)['units']['fe']['checks'].get('sha'))
    rc, out, err = p.cr('finish')
    check('parallel: finish → ACCEPTANCE: VERIFIED на итоговом SHA', rc == 0 and 'ACCEPTANCE: VERIFIED' in out, out + err)

    # --- Б. красные проверки итогового SHA: ветка не двигается; после исправления интеграция проходит
    r = Proj('par-red')
    r.cr('start', 'красный итог', '--id', 'red-1', '--kind', 'mixed', '--contract-file', r.contract())
    r.cr('unit', 'be', '--kind', 'backend', '--files', 'backend/app.py')
    r.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    r.script('codex', [{'role': 'author', 'files': FILES_OK1}])
    r.script('claude', [OK])
    fe = bg(r, 'cycle', 'fe')
    done(fe)
    r.commit('backend/app.py', 'def add(a, b):\n    return a + b  # FAIL_test\n')
    head0 = r.git('rev-parse', 'HEAD')
    rc, _, err = r.cr('integrate', 'fe')
    check('parallel: красные проверки итогового SHA → интеграция отказана, ветка и единица не изменены',
          rc == 2 and 'НЕ прошли' in err and r.git('rev-parse', 'HEAD') == head0 and rstate(r)['units']['fe']['status'] == 'accepted'
          and not rstate(r)['units']['fe']['merged'], err)
    r.commit('backend/app.py', 'def add(a, b):\n    return a + b\n', 'исправление')
    rc, out, err = r.cr('integrate', 'fe')
    check('parallel: после исправления ветки интеграция проходит, модели не вызывались повторно', rc == 0 and r.count('codex-author') == 1 and r.count('claude-review') == 1, out + err)
    merged_sha, runs = r.git('rev-parse', 'HEAD'), len(docker_runs(r))
    state_edit(r, lambda st: st['units']['fe'].update(status='accepted'))   # обрыв после перемещения ветки, до записи статуса
    rc, out, err = r.cr('integrate', 'fe')
    check('parallel: обрыв integrate после перемещения ветки — повтор дозаписывает статус без новых проверок и вызовов',
          rc == 0 and 'дозаписано' in out and rstate(r)['units']['fe']['status'] == 'integrated' and r.git('rev-parse', 'HEAD') == merged_sha
          and len(docker_runs(r)) == runs, out + err)
    r.git('reset', '--hard', r.git('rev-parse', 'HEAD^1'))   # обрыв между записью проверок на SHA слияния и записью намерения: ветка не двигалась
    state_edit(r, lambda st: st['units']['fe'].update(status='accepted', merged=None))
    rc, out, err = r.cr('integrate', 'fe')
    check('parallel: обрыв до перемещения ветки — повтор строит то же слияние, берёт записанные проверки и вливает без новых запусков',
          rc == 0 and r.git('rev-parse', 'HEAD') == merged_sha and len(docker_runs(r)) == runs and rstate(r)['units']['fe']['merged'] == merged_sha, out + err)
    (r.repo / '.ai/task/state.json.bak').write_text((r.repo / '.ai/task/state.json').read_text(encoding='utf-8'), encoding='utf-8')
    (r.repo / '.ai/task/state.json').write_text('{"units": ', encoding='utf-8')
    rc, _, err = r.cr('finish')
    check('parallel: finish при нечитаемом состоянии отказан до закрытия (без блокировок единиц не работает)', rc == 2, err)

    # --- В. две интеграции одновременно выстраиваются последовательно
    q = Proj('par-int', extra={'frontend/src/B.tsx': APP_OLD})
    q.cr('start', 'две интеграции', '--id', 'int-1', '--kind', 'mixed', '--contract-file', q.contract())
    q.cr('unit', 'f1', '--kind', 'frontend', '--files', 'frontend/src/App.tsx')
    q.cr('unit', 'f2', '--kind', 'frontend', '--files', 'frontend/src/B.tsx')
    q.script('codex', [{'files': {'frontend/src/App.tsx': APP_V1}}, {'files': {'frontend/src/B.tsx': APP_V1}}])
    q.script('claude', [OK, OK])
    q.cr('cycle', 'f1')
    q.cr('cycle', 'f2')
    a1, a2 = bg(q, 'integrate', 'f1'), bg(q, 'integrate', 'f2')
    (rc1, o1, e1), (rc2, o2, e2) = done(a1), done(a2)
    tree = q.git('ls-tree', '-r', 'HEAD', '--name-only').splitlines()
    check('parallel: две одновременные интеграции — обе успешны по очереди, оба файла в ветке, один слиянием',
          rc1 == 0 and rc2 == 0 and 'className' in (q.repo / 'frontend/src/App.tsx').read_text(encoding='utf-8')
          and 'className' in (q.repo / 'frontend/src/B.tsx').read_text(encoding='utf-8')
          and len(q.git('rev-list', '--merges', 'HEAD').splitlines()) == 1 and 'frontend/src/B.tsx' in tree, o1 + e1 + o2 + e2)
    rc, out, _ = q.cr('finish')
    check('parallel: finish после двух интеграций → VERIFIED', rc == 0 and 'ACCEPTANCE: VERIFIED' in out, out)

    # --- Г. общий бюджет при одновременном резервировании: предел Ивана 2, три параллельных ревью → ровно два вызова
    b = Proj('par-budget')
    b.cr('start', 'общий бюджет', '--id', 'bud-1', '--max-calls', '2')
    for n in (1, 2, 3):
        b.cr('unit', f'b{n}', '--kind', 'backend', '--files', f'backend/m{n}.py')
    for n in (1, 2, 3):
        b.commit(f'backend/m{n}.py', f'X = {n}\n')
    b.script('codex', [{'role': 'review', 'text': 'VERDICT: PASS', 'sleep': 2}] * 3)
    ps = [bg(b, 'review', f'b{n}') for n in (1, 2, 3)]
    codes = sorted(done(x)[0] for x in ps)
    st = rstate(b)
    check('parallel: предел 2 при трёх одновременных ревью — ровно два вызова, третий LIMIT (3), резервы не потеряны',
          codes == [0, 0, 3] and len(st['calls']) == 2 and b.count('codex-review') == 2, (codes, len(st['calls'])))

    # --- Д. одновременное создание единиц с общим файлом: владелец один
    o = Proj('par-own')
    o.cr('start', 'владение', '--id', 'own-1')
    x1, x2 = bg(o, 'unit', 'u1', '--kind', 'backend', '--files', 'backend/app.py'), bg(o, 'unit', 'u2', '--kind', 'backend', '--files', 'backend/app.py')
    codes = sorted([done(x1)[0], done(x2)[0]])
    owners = [k for k, v in rstate(o)['units'].items() if 'backend/app.py' in v['files']]
    check('parallel: два одновременных unit с одним файлом — успешен ровно один, владелец один', codes[0] == 0 and codes[1] != 0 and len(owners) == 1, (codes, owners))
    o.cr('unit', 'u3', '--kind', 'backend', '--files', 'backend/other.py')
    rc, _, err = o.cr('review')
    check('parallel: review без uid при нескольких единицах — отказ до блокировок и вызовов', rc == 2 and 'укажи единицу' in err and not o.calls(), err)
    res = drive(o, 't2 = m.Task(Path(r"' + str(o.repo) + '")).load()\n'
                   't2.s["calls"]["u1:review:1"] = {"unit": "u1", "role": "review", "kind": "codex-review", "n": 1, "prompt_sha256": "x", "prompt_bytes": 1, "at": 0}\n'
                   't2.save()\n'
                   'try:\n    m.model_call(t, "u1", "review", 1, "codex-review", "P", lambda d, p: print("LAUNCHED"), m.parse_codex_review)\n'
                   'except m.Blocked as e:\n    print("BLOCKED", e.code)\n')
    check('parallel: ключ вызова, зарезервированный другим процессом, перепроверяется в транзакции — второй вызов не запущен',
          res.stdout.split() == ['BLOCKED', '4'], res.stdout + res.stderr)

    # --- Е. зависимая единица ждёт backend без расхода, затем стартует
    w = Proj('par-wait')
    w.cr('start', 'ожидание', '--id', 'wait-1', '--kind', 'mixed', '--contract-file', w.contract())
    w.cr('unit', 'be', '--kind', 'backend', '--files', 'backend/app.py')
    w.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES, '--after', 'be')
    w.script('codex', [{'role': 'author', 'files': FILES_OK1}, {'role': 'review', 'text': 'VERDICT: PASS'}])
    w.script('claude', [OK])
    rc, _, err = w.cr('cycle', 'fe', '--wait', '1', env={'AQ_POLL': '0.2'})
    check('parallel: --wait без готовой зависимости истёк → WAIT (5), вызовов нет', rc == 5 and not w.calls(), err)
    fe = bg(w, 'cycle', 'fe', '--wait', '120', env={'AQ_POLL': '0.2'})
    time.sleep(2.5)
    check('parallel: зависимая единица ждёт и не тратит вызовы', fe.poll() is None and not w.calls())
    w.commit('backend/app.py', 'def add(a, b):\n    return a + b\n# готово\n')
    rc_be, _, _ = w.cr('review', 'be')
    rc_fe, out, err = done(fe)
    iv = intervals(w)
    check('parallel: после PASS backend frontend стартует сам и доходит до PASS; автор начал после ревью backend',
          rc_be == 0 and rc_fe == 0 and iv['codex-author'][0][0] >= iv['codex-review'][0][1], out + err)

    # --- Ж. прерывание: упавший процесс не теряет чужие результаты; готовый ответ читается без повторной оплаты, неопределённый — не повторяется
    k = Proj('par-kill')
    k.cr('start', 'прерывание', '--id', 'kill-1', '--kind', 'mixed', '--contract-file', k.contract())
    k.cr('unit', 'be', '--kind', 'backend', '--files', 'backend/app.py')
    k.cr('unit', 'fe', '--kind', 'frontend', '--files', *FE_FILES)
    k.commit('backend/app.py', 'def add(a, b):\n    return a + b\n# k\n')
    k.script('codex', [{'role': 'author', 'files': FILES_OK1, 'sleep': 6}])
    fe = bg(k, 'cycle', 'fe')
    until(lambda: any(c.startswith('fe:author') for c in rstate(k)['calls']))
    crash = drive(k, 'def crash(d, prompt):\n    (d / "raw.txt").write_text("- [MINOR] x — y.\\nVERDICT: PASS", encoding="utf-8"); os._exit(7)\n'
                     'm.model_call(t, "be", "review", 1, "codex-review", "PROMPT", crash, m.parse_codex_review)\n')
    check('parallel: процесс backend умер после сохранения ответа (7), пока frontend работает', crash.returncode == 7, crash.stderr)
    fe.kill()
    fe.communicate()
    n_before = len(rstate(k)['calls'])
    res = drive(k, 'def never(d, prompt):\n    print("RELAUNCH")\n'
                   'r = m.model_call(t, "be", "review", 1, "codex-review", "PROMPT", never, m.parse_codex_review)\nprint(r["verdict"])\n')
    st = rstate(k)
    check('parallel: готовый ответ упавшего процесса читается без нового вызова, резервы обоих процессов сохранены',
          res.stdout.split() == ['PASS'] and set(st['calls']) == {'fe:author:1', 'be:review:1'} and len(st['calls']) == n_before, (res.stdout, res.stderr, list(st['calls'])))
    rc, _, err = k.cr('cycle', 'fe')
    check('parallel: пока процесс прерванного вызова жив — BUSY, повторного вызова нет', rc == 4 and k.count('codex-author') == 1, err)
    time.sleep(7)
    rc, _, err = k.cr('cycle', 'fe')
    check('parallel: ответ прерванного вызова не получен — повтор запрещён, оплата не повторяется', rc == 2 and 'повтор' in err and k.count('codex-author') == 1, err)


def main():
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8', errors='replace')
    for t in (t_route, t_backend, t_role_rules, t_frontend_cycle, t_limits_recovery, t_hidden_paths_and_budget, t_budget_units_chain, t_task_budget_scope, t_progress_policy, t_cont_a, t_cont_g, t_cont_f, t_cont_c, t_cont_h, t_cont_i, t_engine_recovery, t_author_validation, t_mixed, t_review_fixes, t_links, t_parallel,
              t_unavailable_and_modes):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            check(f'{t.__name__}: исключение', False, repr(e))
    bad = [n for n, ok in results if not ok]
    print(f'\nИтого: {len(results) - len(bad)}/{len(results)} прошло. Временные проекты: {tempfile.gettempdir()}\\aq-selftest-* (не удаляются)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
