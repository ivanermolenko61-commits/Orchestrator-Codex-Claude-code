"""Qwen headless transport. No task state or orchestration lives here."""
import json
import os
import shutil
import tempfile
from pathlib import Path

MODEL = 'qwen4-max'


def discover(split_bin):
    if os.environ.get('QWEN_BIN'):
        return split_bin(os.environ['QWEN_BIN'])
    node = shutil.which('node')
    found = shutil.which('qwen')
    if found and Path(found).suffix.lower() not in ('.ps1', '.cmd', '.bat'):
        return [found]
    candidates = []
    if found:
        candidates.append(Path(found).parent / 'node_modules/@qwen-code/qwen-code/cli.js')
    candidates += sorted(Path.home().glob('.vscode/extensions/qwenlm.qwen-code-vscode-ide-companion-*/dist/qwen-cli/cli.js'),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    return next(([node, str(p)] for p in candidates if node and p.is_file()), None)


def events(path):
    def unique(pairs):
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError('Qwen: duplicate JSON key')
        return dict(pairs)
    return [json.loads(line, object_pairs_hook=unique) for line in Path(path).read_text(encoding='utf-8').split('\n') if line.strip()]


def usage(path):
    try:
        result = [e for e in events(path) if e.get('type') == 'result']
        u = result[-1].get('usage') or {} if result else {}
        out = {k: u[k] for k in ('input_tokens', 'output_tokens', 'cache_read_input_tokens')
               if isinstance(u.get(k), int) and not isinstance(u[k], bool) and u[k] >= 0}
        return out if {'input_tokens', 'output_tokens'} <= out.keys() else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def parse_events(engine, path, model=MODEL):
    try:
        evs = events(path)
    except (OSError, ValueError) as exc:
        engine.need(False, f'Qwen: invalid event stream ({exc.__class__.__name__})')
    result, seen, output, initialized = None, set(), None, False
    def record_model(value):
        if value is not None:
            engine.need(isinstance(value, str) and bool(value), 'Qwen: invalid model')
            seen.add(value)
    for ev in evs:
        engine.need(isinstance(ev, dict), 'Qwen: invalid event')
        if ev.get('type') == 'system' and ev.get('subtype') == 'init':
            engine.need(not initialized, 'Qwen: duplicate init')
            initialized = True
            engine.need(ev.get('mcp_servers') == [] and ev.get('tools') == ['structured_output'], 'Qwen: unexpected tools/MCP in init')
        record_model(ev.get('model'))
        # structured_output is the synthetic schema completion, never an executable tool.
        if ev.get('type') == 'assistant':
            engine.need(initialized and result is None, 'Qwen: assistant outside initialized session')
            message = ev.get('message')
            engine.need(message is None or isinstance(message, dict), 'Qwen: invalid message')
            message = message or {}
            record_model(message.get('model'))
            content = message.get('content', [])
            engine.need(isinstance(content, list), 'Qwen: invalid content')
            for block in content:
                engine.need(isinstance(block, dict) and isinstance(block.get('type'), str), 'Qwen: invalid content block')
                if block.get('type') == 'tool_use':
                    engine.need(block.get('name') == 'structured_output', 'Qwen: executable tool call rejected')
                    output = block.get('input')
        engine.need(ev.get('type') not in ('tool_use', 'tool_result', 'error'), 'Qwen: tool/error event rejected')
        if ev.get('type') == 'result':
            engine.need(result is None, 'Qwen: duplicate result')
            result = ev
    engine.need(initialized and result and not result.get('is_error') and result.get('subtype') == 'success' and not result.get('permission_denials'), 'Qwen: no successful result')
    engine.need(seen == {model}, f'Qwen: requested model not confirmed ({sorted(seen)})')
    answer = result.get('structured_result', result.get('structured_output'))
    engine.need(isinstance(answer, dict), 'Qwen: missing structured_output')
    engine.need(output == answer, 'Qwen: terminal result differs from structured_output call')
    text = json.dumps(answer, ensure_ascii=False)
    engine.need(len(text.encode('utf-8')) <= engine.MAX_RESULT, 'Qwen: result too large')
    return text


def launch(engine, schema, role, effort='high'):
    def go(directory, prompt):
        cli = discover(engine.split_bin)
        engine.need(cli, 'Qwen CLI not found (QWEN_BIN or VS Code extension)')
        with tempfile.TemporaryDirectory(prefix='aq-qwen-') as temporary:
            root = Path(temporary)
            schema_file = root / 'schema.json'
            engine.wjson(schema_file, schema)
            engine.wjson(root / 'mcp.json', {'mcpServers': {}})
            engine.wjson(root / 'settings.json', {'model': {'reasoningEffort': effort},
                         'tools': {'core': [], 'exclude': ['*']}, 'mcpServers': {}})
            cmd = [*cli, '--bare', '--safe-mode', '--chat-recording=false', '--advisor', 'off',
                   '--model', MODEL, '--auth-type', 'qwen-subscription', '--output-format', 'stream-json',
                   '--json-schema', '@' + str(schema_file), '--max-tool-calls', '0', '--max-session-turns', '3',
                   '--exclude-tools', 'read_file', 'run_shell_command', 'get_goal', 'update_goal', 'edit', 'notebook_edit',
                   '--max-wall-time', str(engine.timeout_for('qwen-' + role)), '--mcp-config', str(root / 'mcp.json'),
                   '--system-prompt', 'Use only the supplied data. Return the requested structured result. No tools.',
                   '-p', 'Complete the task supplied on stdin.']
            env = engine.clean_env()
            env['QWEN_CODE_DISABLE_TELEMETRY'] = '1'
            env['QWEN_CODE_SYSTEM_SETTINGS_PATH'] = str(root / 'settings.json')
            env['QWEN_CODE_SYSTEM_DEFAULTS_PATH'] = str(root / 'defaults.json')
            engine.wjson(directory / 'profile.json', {'model': MODEL, 'effort': effort, 'auth': 'qwen-subscription',
                         'safe_mode': True, 'max_tool_calls': 0})
            path = engine.spawn(cmd, directory, prompt, 'qwen-' + role, root, env=env, stdout_to='events.jsonl')
            engine.wtext(directory / 'raw.txt', parse_events(engine, path))
    return go
