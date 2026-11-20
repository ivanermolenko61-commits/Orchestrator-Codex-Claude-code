"""Focused regression tests for the symmetric service pair; no live calls."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import selftest as fixture
import agentq_global as e
import qwen_cli as q

PASS = {'verdict': 'PASS', 'summary': 'ok', 'findings': [], 'prior': []}


class PairTests(unittest.TestCase):
    def command(self, p, *args):
        rc, out, err = p.cr(*args)
        self.assertEqual(rc, 0, out + err)
        return out

    def test_service_and_language_matrix(self):
        for author in e.SERVICES:
            for kind in ('frontend', 'backend'):
                with self.subTest(author=author, kind=kind):
                    p = fixture.Proj('pair-matrix')
                    self.command(p, 'start', 'matrix', '--kind', 'mixed', '--author', author, '--contract-file', p.contract())
                    self.command(p, 'unit', 'code', '--kind', kind, '--author', author, '--files', 'backend/app.py')
                    files = {'backend/app.py': 'def add(a, b):\n    return a + b + 1\n'}
                    if author == 'codex':
                        p.script('codex', [{'files': files}])
                        p.script('qwen', [PASS])
                    else:
                        p.script('qwen', [{'files': files, 'usage': {'input_tokens': 11, 'output_tokens': 22}}])
                        p.script('codex', [{'json': PASS}])
                    output = self.command(p, 'cycle', 'code')
                    model = e.MODEL if author == 'codex' else q.MODEL
                    self.assertIn(f'Автор {author} ({model}/high)', output)
                    before = (p.repo / '.ai/task/state.json').read_bytes()
                    data = json.loads(self.command(p, 'status', '--json'))
                    self.assertEqual(data['units']['code']['author'], author)
                    p.env['AGENTQ_LEAD'] = 'codex' if author == 'qwen' else 'qwen'
                    after = json.loads(self.command(p, 'status', '--json'))
                    self.assertEqual(after['units'], data['units'])
                    self.assertEqual(before, (p.repo / '.ai/task/state.json').read_bytes())
                    self.command(p, 'cycle', 'code')
                    self.assertEqual(len(p.calls()), 2)
                    self.command(p, 'integrate', 'code')
                    self.command(p, 'finish')
                    self.assertTrue(all('claude' not in line for line in p.calls()))

    def test_current_lead_writes_without_author_call(self):
        p = fixture.Proj('pair-direct')
        p.env['AGENTQ_LEAD'] = 'codex'
        self.command(p, 'start', 'direct')
        p.commit('frontend/src/App.tsx', fixture.APP_V1)
        p.script('qwen', [PASS])
        self.command(p, 'review')
        self.command(p, 'finish')
        self.assertEqual(len(p.calls()), 1)
        self.assertTrue(p.calls()[0].startswith('qwen-review'))

    def test_legacy_state_is_readable_and_never_relabelled(self):
        p = fixture.Proj('pair-legacy')
        self.command(p, 'start', 'legacy')
        path = p.repo / '.ai/task/state.json'
        state = json.loads(path.read_text())
        state.pop('pair')
        state['units']['main'].update(author='claude', reviewer='codex')
        path.write_text(json.dumps(state))
        original = path.read_bytes()
        self.command(p, 'status', '--json')
        self.command(p, 'report')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(p.cr('review')[0], 2)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(p.calls())

    def test_two_execution_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            with e.execution_slot(tmp), e.execution_slot(tmp):
                with self.assertRaises(e.Blocked) as raised:
                    with e.execution_slot(tmp):
                        pass
                self.assertEqual(raised.exception.code, 4)
            with e.execution_slot(tmp):
                pass

    def test_direct_wrong_lead_cannot_self_review(self):
        p = fixture.Proj('pair-direct-identity')
        self.command(p, 'start', 'direct', '--author', 'qwen')
        p.commit('backend/app.py', 'def add(a,b):\n    return a+b+1\n')
        p.env['AGENTQ_LEAD'] = 'codex'
        rc, out, err = p.cr('review')
        self.assertEqual(rc, 2, out + err)
        self.assertIn('чат автора qwen', out + err)
        self.assertFalse(p.calls())
        p.env['AGENTQ_LEAD'] = 'qwen'
        p.script('codex', [{'json': PASS}])
        self.command(p, 'review')
        p.env['AGENTQ_LEAD'] = 'codex'
        self.command(p, 'finish')

    def test_cross_class_checks_and_waiver(self):
        p = fixture.Proj('pair-check-coverage')
        self.command(p, 'start', 'cross class', '--author', 'qwen')
        p.commit('frontend/src/App.tsx', fixture.APP_V1)
        t = e.Task(p.repo).load()
        tip = p.git('rev-parse', 'HEAD')
        self.assertEqual(e.check_kinds(p.repo, 'backend', t.unit('main')['base'], tip), ['backend', 'frontend'])
        seen = []
        def suite(root, kind, base, sha):
            seen.append((kind, sha))
            return {'configured': True, 'ok': kind != 'frontend', 'tail': kind, 'commands': []}
        with patch.object(e, 'run_check_suite', side_effect=suite):
            result = e.run_checks(p.repo, 'backend', t.unit('main')['base'], tip)
        self.assertFalse(result['ok'])
        self.assertEqual(seen, [('backend', tip), ('frontend', tip)])
        t.unit('main')['checks'] = {'configured': True, 'ok': True, 'sha': tip}
        self.assertFalse(e.full_green(t, 'main', tip))
        t.unit('main')['checks']['suites'] = ['backend', 'frontend']
        self.assertTrue(e.full_green(t, 'main', tip))
        with patch.object(e, 'trusted_config', return_value={'checks': {'frontend': [['true']]}}):
            self.assertTrue(e.unit_has_checks(t, 'main'))

    def test_npm_qwen_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / 'node_modules/@qwen-code/qwen-code/cli.js'
            cli.parent.mkdir(parents=True)
            cli.write_text('// fixture')
            def which(name):
                return str(Path(tmp) / ('qwen.cmd' if name == 'qwen' else 'node.exe'))
            with patch.dict(os.environ, {'QWEN_BIN': ''}), patch.object(q.shutil, 'which', side_effect=which):
                self.assertEqual(q.discover(e.split_bin), [which('node'), str(cli)])

    def test_profile_rejects_inert_arguments(self):
        p = fixture.Proj('pair-profile')
        self.command(p, 'start', 'profile')
        self.command(p, 'profile', 'main', 'author')
        self.assertEqual(p.cr('profile', 'main', 'author', '--effort', 'low')[0], 2)

    def test_qwen_events_and_usage(self):
        base = [{'type': 'system', 'subtype': 'init', 'model': q.MODEL, 'tools': ['structured_output'], 'mcp_servers': []},
                {'type': 'assistant', 'message': {'model': q.MODEL, 'content': [{'type': 'tool_use', 'name': 'structured_output', 'input': PASS}]}},
                {'type': 'result', 'subtype': 'success', 'structured_result': PASS, 'usage': {'input_tokens': 21, 'output_tokens': 4}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            def save(value):
                path.write_text('\n'.join(json.dumps(v) for v in value), encoding='utf-8')
            save(base)
            self.assertEqual(json.loads(q.parse_events(e, path)), PASS)
            self.assertEqual(q.usage(path), {'input_tokens': 21, 'output_tokens': 4})
            for mutate in (lambda v: v[0].update(tools=['run_shell_command']),
                           lambda v: v[0].update(model='another-model'),
                           lambda v: v[0].pop('tools'),
                           lambda v: v[1]['message'].update(model='another-model'),
                           lambda v: v[1]['message']['content'][0].update(name='read_file'),
                           lambda v: v[1].update(message='invalid'),
                           lambda v: v[1]['message'].update(content=['invalid']),
                           lambda v: v[1]['message'].update(model=42),
                           lambda v: v[-1].update(is_error=True),
                           lambda v: v[-1].pop('structured_result')):
                value = copy.deepcopy(base)
                mutate(value)
                save(value)
                with self.assertRaises(e.Blocked):
                    q.parse_events(e, path)
            save([{'type': 'result', 'subtype': 'error'}])
            self.assertIsNone(q.usage(path))
            value = copy.deepcopy(base)
            answer = {**PASS, 'summary': 'Unicode\u2028separator\u2029preserved'}
            value[1]['message']['content'][0]['input'] = answer
            value[-1]['structured_result'] = answer
            path.write_text('\n'.join(json.dumps(v, ensure_ascii=False) for v in value), encoding='utf-8')
            self.assertEqual(json.loads(q.parse_events(e, path)), answer)
            path.write_text('{invalid', encoding='utf-8')
            with self.assertRaises(e.Blocked):
                q.parse_events(e, path)

    def test_missing_cache_is_unknown(self):
        usage = {}
        e.add_usage(usage, 'review', 'qwen', {'input_tokens': 3, 'output_tokens': 4})
        self.assertIsNone(usage['review/qwen']['cached'])
        self.assertIn('неизвестно', e.usage_text(usage))

    def test_changed_services_cannot_inherit_pass(self):
        p = fixture.Proj('pair-change')
        self.command(p, 'start', 'same goal', '--id', 'pair-old', '--author', 'qwen')
        p.commit('backend/app.py', 'def add(a,b):\n    return a+b+1\n')
        p.script('codex', [{'json': PASS}])
        self.command(p, 'review')
        path = p.repo / '.ai/task/state.json'
        old = fixture.rstate(p)
        old.pop('pair')
        old['units']['main']['author'] = 'claude'
        path.write_text(json.dumps(old), encoding='utf-8')
        self.assertEqual(p.cr('finish', '--abandon')[0], 1)  # archived legacy PASS requires fresh acceptance
        self.command(p, 'start', 'same goal', '--id', 'pair-new', '--continues', 'pair-old', '--author', 'codex')
        state = fixture.rstate(p)
        self.assertIsNone(state['units']['main']['verdict'])
        self.assertEqual(state['inherited']['calls'], 1)
        self.assertEqual(state['units']['main']['reviewer'], 'qwen')


if __name__ == '__main__':
    unittest.main()
