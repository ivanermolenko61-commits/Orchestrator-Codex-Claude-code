import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = None
ENGINE = Path(__file__).resolve().parent.parent / 'skills/agentq/agentq_global.py'
os.environ['PYTHONIOENCODING'] = 'utf-8'

def run(cwd, argv, expected=0):
    start = time.monotonic()
    p = subprocess.run(list(map(str, argv)), cwd=cwd, capture_output=True, encoding='utf-8')
    print(json.dumps({'cwd': str(cwd), 'argv': list(map(str, argv[1:])), 'rc': p.returncode, 'seconds': round(time.monotonic()-start, 3), 'stdout': p.stdout, 'stderr': p.stderr}, ensure_ascii=False), flush=True)
    if p.returncode != expected:
        raise RuntimeError(f'Unexpected exit {p.returncode}, expected {expected}')
    return p.stdout

def prepare(name):
    root = ROOT / name
    root.mkdir()
    def write(path, content):
        target = root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    write('.gitignore', '.ai/\n__pycache__/\n')
    write('.agents/agentq.json', json.dumps({'checks': {'backend': [['python','-m','unittest','test_slug']], 'frontend': [['python','-m','unittest','test_clamp']]}, 'check_image':'python:3.12-slim-bookworm'}))
    write('slug.py','def slug(text):\n    return text\n')
    write('clamp.py','def clamp(value, lo, hi):\n    return value\n')
    write('test_slug.py','import unittest\nfrom slug import slug\nclass Tests(unittest.TestCase):\n def test_existing(self): self.assertEqual(slug("hello"),"hello")\n')
    write('test_clamp.py','import unittest\nfrom clamp import clamp\nclass Tests(unittest.TestCase):\n def test_existing(self): self.assertEqual(clamp(3,0,5),3)\n')
    run(root,['git','init','-b','main']);run(root,['git','config','user.email','agentq-test@example.invalid']);run(root,['git','config','user.name','Agentq verification'])
    run(root,['git','add','.']);run(root,['git','commit','-m','baseline'])
    return root

def aq(root, *args, lead='codex', expected=0):
    return run(root,[sys.executable,ENGINE,'--lead',lead,*args],expected)

SPECS = {
 'slug': 'Implement slug(text): strip leading/trailing whitespace, lowercase, split on whitespace, join with a single ASCII hyphen. Preserve other characters including punctuation and Unicode. Empty or whitespace-only gives empty string. Add unittest cases for these requirements in test_slug.py. Edit only slug.py and test_slug.py. No dependencies.',
 'clamp': 'Implement clamp(value, lo, hi): if lo > hi raise ValueError, otherwise return lo if value < lo, hi if value > hi, else value. Include both boundaries. Inputs are numbers. Add unittest cases including inverted bounds in test_clamp.py. Edit only clamp.py and test_clamp.py. No dependencies.'}

def benchmark(mode):
    root=prepare('live-'+mode)
    contract=ROOT/'live-contract.txt';contract.write_text('Two independent pure Python functions. No cross imports. Specs per unit are authoritative. Standard library unittest only.',encoding='utf-8')
    aq(root,'route')
    aq(root,'start','Implement slug and clamp', '--id','live-'+mode,'--kind','mixed','--contract-file',contract)
    for uid, author,kind in [('slug','codex','backend'),('clamp','qwen','frontend')]:
        spec=ROOT/(uid+'-spec.txt');spec.write_text(SPECS[uid],encoding='utf-8')
        aq(root,'unit',uid,'--kind',kind,'--author',author,'--files',uid+'.py','test_'+uid+'.py','--spec-file',spec)
    start=time.monotonic()
    if mode=='parallel':
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for f in [pool.submit(aq,root,'cycle',uid) for uid in SPECS]:f.result()
    else:
        for uid in SPECS:aq(root,'cycle',uid)
    cycles=time.monotonic()-start
    first=json.loads(aq(root,'status','--json'))
    second=json.loads(aq(root,'status','--json',lead='qwen'))
    assert first['units']==second['units']
    for uid in SPECS:aq(root,'integrate',uid,lead='qwen')
    run(root,[sys.executable,'-m','unittest','discover'])
    # Independent acceptance assertions beyond the agents' authored tests.
    run(root,[sys.executable,'-c',"from slug import slug;from clamp import clamp;assert slug('  Ab C \\t D ')== 'ab-c-d';assert slug('   ')=='';assert slug('Ёж!')=='ёж!';assert clamp(-2,0,5)==0;assert clamp(8,0,5)==5;assert clamp(0,0,5)==0\ntry:clamp(1,5,0)\nexcept ValueError:pass\nelse:raise AssertionError('inverted bounds')"])
    aq(root,'report')
    state=json.loads((root/'.ai/task/state.json').read_text(encoding='utf-8'))
    aq(root,'finish',lead='qwen')
    result={'mode':mode,'cycle_seconds':cycles,'total_seconds':time.monotonic()-start,'calls':state['calls'],'units':state['units']}
    (ROOT/('results-'+mode+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser(description='LIVE paid CLI benchmark in disposable projects, requires Docker and authenticated Codex/Qwen.')
    parser.add_argument('--run-live', action='store_true', required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('mode', choices=('sequential', 'parallel'))
    args = parser.parse_args()
    ROOT = args.output_root.resolve()
    ROOT.mkdir(parents=True, exist_ok=True)
    os.environ['AQ_WORKTREES'] = str(ROOT / 'worktrees')
    benchmark(args.mode)
