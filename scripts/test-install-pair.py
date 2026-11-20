"""Isolated installation, preservation, upgrade, rollback and shared-entry checks."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install-pair.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_reparse_points_on_older_python(self):
        from types import SimpleNamespace
        path = SimpleNamespace(is_symlink=lambda: False, exists=lambda: True,
                               lstat=lambda: SimpleNamespace(st_file_attributes=0x400))
        with patch.object(installer.os, 'name', 'nt'):
            self.assertTrue(installer.linked(path))
        path.lstat = lambda: SimpleNamespace(st_file_attributes=0)
        with patch.object(installer.os, 'name', 'nt'):
            self.assertFalse(installer.linked(path))

    def test_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            untouched = {home / '.qwen/settings.json': b'{"personal":true}', home / '.codex/config.toml': b'# personal',
                         home / '.claude/PAUSED': b'paused', home / '.claude/CLAUDE.md': b'# personal'}
            for path, data in untouched.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            self.assertEqual(installer.main(['--home', tmp]), 1)
            self.assertFalse((home / '.agents').exists())
            self.assertEqual(installer.main(['--home', tmp, '--apply']), 0)
            self.assertEqual(installer.main(['--home', tmp]), 0)
            runtime = home / '.agents/skills/agentq'
            self.assertEqual((runtime / 'SKILL.md').read_bytes(), (home / '.qwen/skills/agentq/SKILL.md').read_bytes())
            for path, data in untouched.items():
                self.assertEqual(path.read_bytes(), data)
            result = subprocess.run([sys.executable, str(runtime / 'agentq_global.py'), '--help'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            old = runtime / 'qwen_cli.py'
            old.write_bytes(b'# previous adapter')
            installer.main(['--home', tmp, '--apply'])
            backups = list((home / 'agentq-backups').glob('*/.agents/skills/agentq/qwen_cli.py'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'# previous adapter')
            self.assertEqual(old.read_bytes(), (ROOT / 'skills/agentq/qwen_cli.py').read_bytes())
            (runtime / 'VERSION').write_text('999.0.0')
            with self.assertRaises(SystemExit):
                installer.main(['--home', tmp, '--apply'])


if __name__ == '__main__':
    unittest.main()
