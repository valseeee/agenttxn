"""Run the installed CLI in an isolated directory; derive counts from actual checks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


def run_smoke(executable):
    executable = str(Path(executable).resolve())
    records = []
    # An inherited token must never enable a real provider in offline validation.
    env = dict(os.environ)
    env.pop('GITHUB_TOKEN', None)
    env.pop('AGENTTXN_GITHUB_REPOS', None)
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    with tempfile.TemporaryDirectory(prefix='agenttxn-smoke-') as directory:
        database = str(Path(directory) / 'state.db')

        def check(name, *args, expected=0, field=None, value=None):
            command = [executable, '--db', database, *args]
            result = subprocess.run(command, cwd=directory, env=env, text=True,
                                    encoding='utf-8', capture_output=True, timeout=30)
            passed = result.returncode == expected
            if field is not None and passed:
                passed = json.loads(result.stdout)[field] == value
            records.append({'name': name, 'passed': passed, 'exit': result.returncode,
                            'expected': expected, 'command': command,
                            'stdout': result.stdout, 'stderr': result.stderr})
            if not passed:
                raise AssertionError(f'{name}: {result.stdout}\n{result.stderr}')
            return result.stdout.strip()

        def create(name):
            txid = check(name + '_create', 'tx', 'create', '--intent', name, '--idempotency-key', name)
            uuid.UUID(txid)
            return txid

        def add(name, txid, kind='demo.create'):
            check(name, 'tx', 'add', txid, '--kind', kind, '--payload', '{"name":"smoke"}',
                  '--effect', 'read_only' if kind == 'demo.fail' else 'reversible',
                  '--idempotency-key', name)

        try:
            check('init', 'init')
            check('version', 'version')
            txid = create('success')
            add('success_add', txid)
            check('success_prepare', 'tx', 'prepare', txid, field='status', value='AWAITING_APPROVAL')
            check('approval_required', 'tx', 'commit', txid, expected=1)
            check('success_approve', 'tx', 'approve', txid, '--by', 'smoke')
            check('success_commit', 'tx', 'commit', txid, field='status', value='COMMITTED')
            check('show', 'tx', 'show', txid)
            check('events', 'tx', 'events', txid)
            check('verify', 'tx', 'verify', txid, field='valid', value=True)
            check('list', 'tx', 'list')
            check('metrics_json', 'metrics')
            check('metrics_prometheus', 'metrics', '--format', 'prometheus')
            check('alerts', 'alerts')
            check('safe_telemetry', 'tx', 'telemetry', txid)
            check('repeat_commit_rejected', 'tx', 'commit', txid, expected=1)
            check('final_resume_rejected', 'tx', 'resume', txid, expected=1)
            check('final_reconcile_rejected', 'tx', 'reconcile', txid, expected=1)
            check('final_takeover_rejected', 'tx', 'resume', txid, '--confirm-worker-stopped', expected=1)

            txid = create('unknown')
            add('unknown_add', txid, 'demo.unknown')
            check('unknown_prepare', 'tx', 'prepare', txid)
            check('unknown_approve', 'tx', 'approve', txid, '--by', 'smoke')
            check('unknown_commit', 'tx', 'commit', txid, expected=1,
                  field='status', value='NEEDS_RECONCILIATION')
            check('unknown_reconcile', 'tx', 'reconcile', txid, field='status', value='COMMITTED')
            check('unknown_verify', 'tx', 'verify', txid, field='valid', value=True)

            txid = create('compensation')
            add('compensation_add', txid)
            add('compensation_fail', txid, 'demo.fail')
            check('compensation_prepare', 'tx', 'prepare', txid)
            check('compensation_approve', 'tx', 'approve', txid, '--by', 'smoke')
            check('compensation_commit', 'tx', 'commit', txid, field='status', value='COMPENSATED')
            check('compensation_verify', 'tx', 'verify', txid, field='valid', value=True)

            txid = create('abort')
            check('abort', 'tx', 'abort', txid, field='status', value='ABORTED')
            check('abort_verify', 'tx', 'verify', txid, field='valid', value=True)
        finally:
            # Emit partial results even if a check fails; never print a false success summary.
            for record in records:
                print(f"[{record['name']}] exit={record['exit']} expected={record['expected']} passed={record['passed']}")
                print('$ ' + json.dumps(record['command'], ensure_ascii=False))
                print(record['stdout'], end='')
                print(record['stderr'], end='')
            passed = sum(record['passed'] for record in records)
            print(f'CLI smoke: {passed}/{len(records)} checks passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True, help='Absolute path to installed agenttxn executable')
    run_smoke(parser.parse_args().executable)
