"""Explicit opt-in live Issue checks. Never run as part of the default test suite."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import uuid

from agenttxn import ActionSpec, EffectClass, Engine, Policy, TransactionStatus
from agenttxn.adapters import DemoAdapter, GitHubIssuesAdapter
from agenttxn.errors import OutcomeUnknownError


def run(repository):
    token = os.environ.get('GITHUB_TOKEN')
    allowed = {item.strip() for item in os.environ.get('AGENTTXN_GITHUB_REPOS', '').split(',')}
    if not token or repository not in allowed:
        raise ValueError('Live checks require GITHUB_TOKEN and an explicit AGENTTXN_GITHUB_REPOS allowlist')
    adapter = GitHubIssuesAdapter(token)
    run_id = uuid.uuid4().hex
    results, cleanup = [], []
    with tempfile.TemporaryDirectory(prefix='agenttxn-live-') as directory:
        database = Path(directory) / 'state.db'
        engine = Engine.open(database, policy=Policy(allowed_github_repositories=frozenset({repository})),
                             adapters=[adapter, DemoAdapter()])

        def create_tx(name, failure=False):
            tx = engine.create(intent='AgentTxn live integration ' + name,
                               idempotency_key=f'live:{run_id}:{name}')
            tx.add(ActionSpec('github.issue.create', {'repository': repository,
                'title': f'[AgentTxn integration] {name} {run_id[:8]}',
                'body': 'Automated, explicitly authorized integration test. This Issue will be closed.'},
                EffectClass.REVERSIBLE, f'live:issue:{run_id}:{name}'))
            if failure:
                tx.add(ActionSpec('demo.fail', {}, EffectClass.READ_ONLY, f'live:fail:{run_id}'))
            tx.prepare()
            tx.approve(approved_by='authorized-release-validation', reason='Opt-in integration test')
            return tx

        try:
            for name in ('success', 'lost-response', 'compensation'):
                tx = create_tx(name, failure=name == 'compensation')
                original = adapter.execute
                calls = []
                def execute(*args):
                    result = original(*args)
                    calls.append(result['issue_number'])
                    cleanup.append((tx.steps[0].action, result))
                    if name == 'lost-response':
                        raise OutcomeUnknownError('Injected lost response after real Issue creation')
                    return result
                adapter.execute = execute
                try:
                    report = tx.commit()
                    if name == 'lost-response':
                        assert report.status == TransactionStatus.NEEDS_RECONCILIATION
                        report = tx.reconcile()
                    expected = TransactionStatus.COMPENSATED if name == 'compensation' else TransactionStatus.COMMITTED
                    assert report.status == expected
                    assert len(calls) == 1, 'Provider create must be called exactly once'
                    assert tx.verify().valid, tx.verify().messages
                    number = tx.steps[0].result['issue_number']
                    issue = adapter._request('GET', adapter._issue_path(repository, number))
                    assert issue['state'] == ('closed' if name == 'compensation' else 'open')
                    results.append({'case': name, 'status': report.status.value, 'issue': issue['html_url'],
                                    'provider_creates': len(calls), 'audit_valid': True})
                finally:
                    adapter.execute = original
            assert token.encode() not in database.read_bytes(), 'Credential found in SQLite database'
            for suffix in ('-wal', '-shm'):
                path = Path(str(database) + suffix)
                if path.exists():
                    assert token.encode() not in path.read_bytes(), 'Credential found in SQLite sidecar'
        finally:
            # Known Issue numbers only; never repeat a create to infer a missing response.
            for action, result in cleanup:
                number = result['issue_number']
                issue = adapter._request('GET', adapter._issue_path(repository, number))
                if issue['state'] != 'closed':
                    from agenttxn.adapters import AdapterContext
                    adapter.compensate(AdapterContext('cleanup', 'cleanup', 'cleanup'), action, result, None)
                assert adapter._request('GET', adapter._issue_path(repository, number))['state'] == 'closed'
    print(json.dumps({'repository': repository, 'cases': results, 'cleanup': 'all known test Issues closed'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--confirm-live-writes', action='store_true', required=True)
    args = parser.parse_args()
    try:
        run(args.repository)
    except Exception as exc:
        # Provider exception chains may contain credentials; do not print them.
        print(json.dumps({'status': 'FAILED', 'error_type': type(exc).__name__}))
        raise SystemExit(1)
