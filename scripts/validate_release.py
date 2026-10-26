"""Reproduce offline release checks and save real command output (Python 3.11+)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = dict(os.environ)
    env.pop('GITHUB_TOKEN', None)
    env.pop('AGENTTXN_GITHUB_REPOS', None)
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    results = []

    def run(name, command, cwd=ROOT):
        print(f'Running {name}', flush=True)
        completed = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                                   capture_output=True, text=True, encoding='utf-8', timeout=300)
        log = '$ ' + json.dumps([str(x) for x in command]) + '\n'
        log += completed.stdout + completed.stderr + f'\nexit={completed.returncode}\n'
        (ROOT / f'{name}_RESULTS.txt').write_text(log, encoding='utf-8')
        results.append({'check': name, 'exit': completed.returncode})
        if completed.returncode:
            raise RuntimeError(f'{name} failed; see {name}_RESULTS.txt')
        return completed.stdout

    try:
        run('ENVIRONMENT', [sys.executable, '-m', 'pip', 'list', '--format=json'])
        run('TEST', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        run('PYTEST', [sys.executable, '-m', 'pytest', '-q'])
        run('COMPILE', [sys.executable, '-m', 'compileall', '-q', 'src', 'tests', 'examples', 'scripts'])
        run('SUCCESS_DEMO', [sys.executable, 'examples/successful_transaction.py'])
        run('COMPENSATION_DEMO', [sys.executable, 'examples/demo_transaction.py'])
        run('WHEEL_BUILD', [sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps', '--wheel-dir', 'dist'])
        version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
        with tempfile.TemporaryDirectory(prefix='agenttxn-wheel-') as directory:
            venv.EnvBuilder(with_pip=True).create(directory)
            bin_dir = Path(directory) / ('Scripts' if os.name == 'nt' else 'bin')
            python = bin_dir / ('python.exe' if os.name == 'nt' else 'python')
            executable = bin_dir / ('agenttxn.exe' if os.name == 'nt' else 'agenttxn')
            run('PACKAGE_INSTALL', [python, '-m', 'pip', 'install', '--no-deps',
                                   ROOT / 'dist' / f'agenttxn-{version}-py3-none-any.whl'], cwd=directory)
            actual = run('WHEEL_VERSION', [executable, 'version'], cwd=directory).strip()
            if actual != version:
                raise AssertionError(f'wheel version {actual!r} differs from {version!r}')
            run('CLI_SMOKE', [python, ROOT / 'scripts/cli_smoke.py', '--executable', executable], cwd=directory)
    finally:
        (ROOT / 'VALIDATION_RESULTS.txt').write_text(json.dumps({
            'python': sys.version, 'executable': sys.executable,
            'checks': results, 'status': 'INCOMPLETE',
            'outside_this_script': ['GitHub live integration', 'remote CI matrix', 'independent security review'],
        }, indent=2) + '\n', encoding='utf-8')
    report = json.loads((ROOT / 'VALIDATION_RESULTS.txt').read_text(encoding='utf-8'))
    report['status'] = 'PASSED'
    (ROOT / 'VALIDATION_RESULTS.txt').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Offline release checks passed: {len(results)}')


if __name__ == '__main__':
    main()
