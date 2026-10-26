"""Verify the source SHA/manifest and run tests from a fresh extraction."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile


def verify(archive_path):
    archive_path = Path(archive_path).resolve()
    expected, filename = archive_path.with_suffix('.zip.sha256').read_text(encoding='utf-8').split()
    if filename != archive_path.name or hashlib.sha256(archive_path.read_bytes()).hexdigest() != expected:
        raise ValueError('SHA-256 sidecar does not match archive')
    log = ['SHA-256 verified: ' + expected]
    log_path = archive_path.with_suffix('.zip.verification.txt')
    env = dict(os.environ)
    env.pop('GITHUB_TOKEN', None)
    env.pop('AGENTTXN_GITHUB_REPOS', None)
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(prefix='agenttxn-extracted-') as directory:
            paths = [PurePosixPath(name) for name in archive.namelist()]
            for path in paths:
                if path.is_absolute() or '..' in path.parts or any(':' in part for part in path.parts):
                    raise ValueError('Unsafe archive path')
                if any(part.startswith(('.venv', 'venv')) or part in {'__pycache__', '.git', 'build', 'dist', '.pytest_cache'}
                       or part.endswith('.egg-info') for part in path.parts):
                    raise ValueError('Runtime/build artifact in source ZIP')
                if path.suffix in {'.pyc', '.db', '.sqlite', '.sqlite3', '.key', '.pem'} or path.name.startswith('.env'):
                    raise ValueError('Runtime data or credential file in source ZIP')
            roots = {path.parts[0] for path in paths}
            if len(roots) != 1:
                raise ValueError('Expected a single source root')
            root_name = roots.pop()
            for required in ('src/agenttxn/engine.py', 'src/agenttxn/store.py', 'pyproject.toml',
                             'README.md', 'LICENSE', 'tests/test_process_recovery.py', 'scripts/cli_smoke.py',
                             'src/agenttxn/postgres.py', 'src/agenttxn/telemetry.py',
                             'deploy/compose.yml', 'deploy/Dockerfile', 'scripts/network_probe.py',
                             'docs/independent-security-review.md'):
                if f'{root_name}/{required}' not in archive.namelist():
                    raise ValueError('Missing required file: ' + required)
            log.append(f'Manifest verified: {len(paths)} files')
            log.extend(str(path) for path in paths)
            archive.extractall(directory)
            root = Path(directory) / root_name
            # Explicitly bind imports to extracted source, ahead of any editable installation.
            commands = [
                [sys.executable, '-c', "import sys; sys.path.insert(0, 'src'); import agenttxn; "
                 "from pathlib import Path; assert Path(agenttxn.__file__).resolve().is_relative_to(Path.cwd().resolve()); "
                 "import unittest; unittest.main(module=None, argv=['unittest','discover','-s','tests','-v'])"],
                [sys.executable, '-c', "import sys; sys.path.insert(0, 'src'); import pytest; raise SystemExit(pytest.main(['-q']))"],
            ]
            for command in commands:
                result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                        text=True, encoding='utf-8', timeout=300)
                log.append('$ ' + json.dumps(command))
                log.extend((result.stdout, result.stderr, f'exit={result.returncode}'))
                if result.returncode:
                    raise RuntimeError('Extracted-source tests failed')
        log.append('PASSED: checksum, manifest, extracted unittest and pytest')
    finally:
        log_path.write_text('\n'.join(log) + '\n', encoding='utf-8')
    print(f'Verified {archive_path}; evidence: {log_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    verify(parser.parse_args().archive)
