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
                    self.command(p, 'cycle', 'code')
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
                           lambda v: v[-1].update(is_error=True),
                           lambda v: v[-1].pop('structured_result')):
                value = copy.deepcopy(base)
                mutate(value)
                save(value)
                with self.assertRaises(e.Blocked):
                    q.parse_events(e, path)
            save([{'type': 'result', 'subtype': 'error'}])
            self.assertIsNone(q.usage(path))

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
