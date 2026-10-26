"""Regression tests for v0.3 migration, worker ownership and safe failure semantics."""
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from agenttxn import ActionSpec, EffectClass, Engine, Policy, StepStatus, TransactionStatus
from agenttxn.adapters import DemoAdapter, GitHubIssuesAdapter
from agenttxn.cli import main as cli_main
from agenttxn.errors import ConcurrentModificationError, PolicyDeniedError, PreflightError
from agenttxn.store import SQLiteStore, SCHEMA


@contextmanager
def db(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.db'
        self.adapter = DemoAdapter(Path(self.temp.name) / 'world.json')
        self.engine = Engine.open(self.path, policy=Policy(require_approval_for_reversible=False), adapters=[self.adapter])

    def make(self, kind='demo.create', key='key:1', payload=None, preconditions=None):
        tx = self.engine.create(intent='regression', idempotency_key='tx:' + key)
        tx.add(ActionSpec(kind, payload if payload is not None else {'name': 'example'},
                          EffectClass.REVERSIBLE if kind != 'demo.fail' else EffectClass.READ_ONLY,
                          'action:' + key, preconditions or {}))
        return tx

    def test_worker_ownership_excludes_second_commit(self):
        entered, release = threading.Event(), threading.Event()
        adapter = self.adapter
        original = adapter.execute
        def blocked(ctx, action, preflight):
            entered.set()
            self.assertTrue(release.wait(timeout=8))
            return original(ctx, action, preflight)
        adapter.execute = blocked
        tx = self.make()
        tx.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(tx.commit)
            self.assertTrue(entered.wait(timeout=8))
            with self.assertRaises(ConcurrentModificationError):
                self.engine.load(tx.id).commit()
            self.assertTrue(self.engine.store.worker_info(tx.id)['claimed'])
            release.set()
            self.assertEqual(future.result(timeout=8).status, TransactionStatus.COMMITTED)
        self.assertFalse(self.engine.store.worker_info(tx.id)['claimed'])
        self.assertEqual(len(self.adapter.effects), 1)

    def test_worker_takeover_must_be_explicit(self):
        tx = self.make(key='takeover')
        tx.prepare()
        self.engine._transition(tx.id, TransactionStatus.COMMITTING)
        step = tx.steps[0]
        self.engine.store.claim_step(tx.id, step.id, attempt_no=1, phase='forward',fingerprint='test',
                                     allowed_statuses={StepStatus.PREFLIGHTED}, target_status=StepStatus.EXECUTING)
        self.engine.store.claim_worker(tx.id, 'dead-process')
        with self.assertRaises(ConcurrentModificationError): tx.resume()
        with self.assertRaises(ConcurrentModificationError): tx.reconcile()
        self.assertEqual(tx.reconcile(confirm_worker_stopped=True).status, TransactionStatus.COMMITTED)
        self.assertFalse(self.engine.store.worker_info(tx.id)['claimed'])
        self.assertEqual(len(self.adapter.effects), 1)

    def test_recover_interrupted_preparation(self):
        tx = self.make(key='prepare')
        self.engine._transition(tx.id, TransactionStatus.PREPARING)
        report = tx.resume()
        self.assertEqual(report.status, TransactionStatus.PREPARED)
        self.assertTrue(tx.verify().valid)

    def test_policy_fingerprint_full_sha256(self):
        identity = Policy().identity
        self.assertEqual(len(identity.split(':')[-1]), 64)
        self.assertEqual(Policy().legacy_identity, identity[:-48])

    def test_preflight_is_bound_to_frozen_plan(self):
        tx = self.make(key='preflight')
        tx.prepare()
        self.assertTrue(tx.verify().valid)
        with db(self.path) as c:
            c.execute("UPDATE steps SET preflight_json=? WHERE transaction_id=?",(json.dumps({'exists': True}),tx.id))
        result = tx.verify()
        self.assertFalse(result.valid)
        self.assertFalse(result.checks['plan_hash'])
        self.assertFalse(result.checks['step_audit_digests'])

    def test_result_digest_detects_db_tamper(self):
        tx = self.make(key='result')
        tx.prepare(); tx.commit()
        self.assertTrue(tx.verify().valid)
        with db(self.path) as c:
            c.execute("UPDATE steps SET result_json=? WHERE transaction_id=?",(json.dumps({'resource_id':'tampered'}),tx.id))
        self.assertFalse(tx.verify().checks['step_audit_digests'])

    def test_runtime_precondition_blocks_changed_provider_state(self):
        tx = self.make(key='precondition',preconditions={'exists':False})
        tx.prepare()
        original = self.adapter.preflight
        self.adapter.preflight = lambda *args: {'exists':True}
        self.assertEqual(tx.commit().status, TransactionStatus.FAILED)
        self.assertFalse(self.adapter.effects)
        self.adapter.preflight = original

    def test_prepare_rejects_false_precondition(self):
        tx = self.make(key='precondition-prepare',preconditions={'exists':True})
        with self.assertRaises(PreflightError): tx.prepare()
        self.assertEqual(tx.record.status,TransactionStatus.PREPARATION_FAILED)

    def test_unexpected_adapter_exception_is_unknown_and_redacted(self):
        tx = self.make(key='unexpected')
        tx.prepare()
        self.adapter.execute = lambda *args: (_ for _ in ()).throw(RuntimeError('MY_SECRET_TOKEN_ABC'))
        report=tx.commit()
        self.assertEqual(report.status,TransactionStatus.NEEDS_RECONCILIATION)
        self.assertEqual(tx.steps[0].status,StepStatus.UNKNOWN)
        with db(self.path) as c:
            self.assertNotIn('MY_SECRET_TOKEN_ABC',str(c.execute('SELECT * FROM events').fetchall()))
            self.assertNotIn('MY_SECRET_TOKEN_ABC',str(c.execute('SELECT * FROM attempts').fetchall()))

    def test_unserializable_provider_result_is_unknown(self):
        tx = self.make(key='unserializable')
        tx.prepare()
        original=self.adapter.execute
        def bad_result(ctx, action, preflight):
            original(ctx,action,preflight)
            return {'nonjson': object()}
        self.adapter.execute=bad_result
        self.assertEqual(tx.commit().status,TransactionStatus.NEEDS_RECONCILIATION)
        self.assertEqual(len(self.adapter.effects),1)
        self.assertEqual(tx.reconcile().status,TransactionStatus.COMMITTED)

    def test_unexpected_compensation_error_is_unknown(self):
        tx=self.make(key='compensation')
        tx.add(ActionSpec('demo.fail',{},EffectClass.READ_ONLY,'action:failure'))
        tx.prepare()
        self.adapter.compensate=lambda *args: (_ for _ in ()).throw(RuntimeError('SECRET_COMPENSATION'))
        self.assertEqual(tx.commit().status,TransactionStatus.NEEDS_RECONCILIATION)
        self.assertEqual(tx.steps[0].status,StepStatus.COMPENSATION_UNKNOWN)
        with db(self.path) as c:
            self.assertNotIn('SECRET_COMPENSATION',str(c.execute('SELECT * FROM events').fetchall()))

    def test_github_label_compensation_disabled_by_default(self):
        action=ActionSpec('github.issue.add_labels',{'repository':'a/b','issue_number':1,'labels':['a']},
                          EffectClass.REVERSIBLE,'label-key')
        self.assertFalse(GitHubIssuesAdapter('test-token').can_compensate(action))
        self.assertTrue(GitHubIssuesAdapter('test-token',allow_label_compensation=True).can_compensate(action))

    def test_github_repo_allowlist(self):
        engine=Engine.open(Path(self.temp.name)/'allowlist.db',
            policy=Policy(allowed_github_repositories=frozenset({'owner/sandbox'})),adapters=[GitHubIssuesAdapter('fake-token')])
        tx=engine.create(intent='deny',idempotency_key='tx:deny')
        tx.add(ActionSpec('github.issue.create',{'repository':'other/repo','title':'x'},EffectClass.REVERSIBLE,'action:deny'))
        with self.assertRaises(PolicyDeniedError): tx.prepare()

    def test_cli_read_only_does_not_require_github_allowlist(self):
        tx=self.make(key='cli')
        tx.prepare()
        with patch.dict(os.environ,{'GITHUB_TOKEN':'fake-token'},clear=True):
            out=io.StringIO()
            with redirect_stdout(out):
                rc=cli_main(['--db',str(self.path),'tx','show',tx.id])
            self.assertEqual(rc,0)
            self.assertIn('transaction',out.getvalue())

    def test_legacy_database_migration_without_json_index(self):
        path=Path(self.temp.name)/'legacy.db'
        with db(path) as c:
            c.executescript(SCHEMA)
            c.execute("INSERT INTO transactions(id,intent,idempotency_key,status,policy_version,metadata_json,created_at,updated_at) VALUES('tx','legacy','legacy-key','CREATED','legacy','{}','a','a')")
            action=ActionSpec('demo.create',{'name':'x'},EffectClass.REVERSIBLE,'legacy-action')
            c.execute("INSERT INTO steps(id,transaction_id,sequence,action_kind,action_json,status) VALUES(?,?,?,?,?,?)",
                      ('step','tx',1,'demo.create',json.dumps(action.to_dict()),'PENDING'))
        store=SQLiteStore(path)
        with db(path) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(c.execute('SELECT action_idempotency_key FROM steps').fetchone()[0],'legacy-action')
            indexes=c.execute("SELECT sql FROM sqlite_master WHERE name='idx_action_idempotency'").fetchone()[0]
            self.assertNotIn('json_extract',indexes)
            self.assertIn('worker_token',[x[1] for x in c.execute('PRAGMA table_info(transactions)')])
        self.assertEqual(store.get_step('step').action.idempotency_key,'legacy-action')

    def test_future_schema_version_is_rejected_without_downgrade(self):
        path=Path(self.temp.name)/'future.db'
        with db(path) as c:
            c.execute('PRAGMA user_version=99')
        with self.assertRaisesRegex(RuntimeError,'newer than supported'):
            SQLiteStore(path)
        with db(path) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],99)

    def test_database_rejects_null_action_idempotency_key(self):
        tx=self.make(key='trigger')
        with self.assertRaises(sqlite3.IntegrityError):
            with db(self.path) as c:
                action=ActionSpec('demo.create',{'name':'b'},EffectClass.REVERSIBLE,'trigger-missing')
                c.execute("INSERT INTO steps(id,transaction_id,sequence,action_kind,action_json,status) VALUES(?,?,?,?,?,?)",
                          ('missing-key',tx.id,2,'demo.create',json.dumps(action.to_dict()),'PENDING'))

    def test_attempt_cannot_be_overwritten(self):
        tx=self.make(key='attempt')
        tx.prepare()
        step=tx.steps[0]
        aid=self.engine.store.claim_step(tx.id,step.id,attempt_no=1,phase='forward',fingerprint='f',
                                         allowed_statuses={StepStatus.PREFLIGHTED},target_status=StepStatus.EXECUTING)
        self.engine.store.finish_attempt(aid,'UNKNOWN')
        with self.assertRaises(ConcurrentModificationError): self.engine.store.finish_attempt(aid,'SUCCEEDED')


class AdditionalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.db'
        self.adapter=DemoAdapter(Path(self.temp.name)/'world.json')
        self.engine=Engine.open(self.path,policy=Policy(require_approval_for_reversible=False),adapters=[self.adapter])

    def test_unknown_compensation_reconciles_to_compensated(self):
        from agenttxn.errors import OutcomeUnknownError
        tx=self.engine.create(intent='compensation lost response',idempotency_key='tx:comp-unknown')
        tx.add(ActionSpec('demo.create',{'name':'x'},EffectClass.REVERSIBLE,'action:comp-unknown'))
        tx.add(ActionSpec('demo.fail',{},EffectClass.READ_ONLY,'action:fail-unknown'))
        tx.prepare()
        original=self.adapter.compensate
        def lost_response(*args):
            original(*args)
            raise OutcomeUnknownError('response lost')
        self.adapter.compensate=lost_response
        self.assertEqual(tx.commit().status,TransactionStatus.NEEDS_RECONCILIATION)
        self.assertEqual(tx.steps[0].status,StepStatus.COMPENSATION_UNKNOWN)
        self.assertEqual(tx.reconcile().status,TransactionStatus.COMPENSATED)
        self.assertTrue(tx.verify().valid)

    def test_legacy_v02_plan_and_policy_remain_readable(self):
        tx=self.engine.create(intent='legacy',idempotency_key='tx:legacy')
        tx.add(ActionSpec('demo.create',{'name':'x'},EffectClass.REVERSIBLE,'action:legacy'))
        tx.prepare()
        with db(self.path) as c:
            c.execute('UPDATE transactions SET policy_version=? WHERE id=?',
                      (self.engine.policy.legacy_identity,tx.id))
        legacy_hash=self.engine._plan_hash(tx.record,tx.steps,legacy=True)
        with db(self.path) as c:
            c.execute('UPDATE transactions SET plan_hash=? WHERE id=?',(legacy_hash,tx.id))
        report=tx.verify()
        self.assertTrue(report.valid)
        self.assertTrue(any('legacy' in x for x in report.messages))
        self.assertEqual(tx.commit().status,TransactionStatus.COMMITTED)

    def test_tampered_transaction_status_detected(self):
        tx=self.engine.create(intent='status integrity',idempotency_key='tx:status')
        tx.add(ActionSpec('demo.create',{'name':'x'},EffectClass.REVERSIBLE,'action:status'))
        tx.prepare(); tx.commit()
        self.assertTrue(tx.verify().valid)
        with db(self.path) as c:
            c.execute("UPDATE transactions SET status='PREPARED' WHERE id=?",(tx.id,))
        self.assertFalse(tx.verify().checks['transaction_status_audit'])

if __name__=="__main__": unittest.main()

class WorkerCrashBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.db'
        self.engine=Engine.open(self.path,policy=Policy(require_approval_for_reversible=False),
                                adapters=[DemoAdapter(Path(self.temp.name)/'world.json')])

    def test_resume_explicitly_takes_over_crashed_preparing(self):
        tx=self.engine.create(intent='prepare crash',idempotency_key='tx:prepare-crash')
        tx.add(ActionSpec('demo.create',{'name':'x'},EffectClass.REVERSIBLE,'action:prepare-crash'))
        self.engine._transition(tx.id,TransactionStatus.PREPARING)
        self.engine.store.claim_worker(tx.id,'dead-worker')
        with self.assertRaises(ConcurrentModificationError): tx.resume()
        self.assertEqual(tx.resume(confirm_worker_stopped=True).status,TransactionStatus.PREPARED)

    def test_resume_explicitly_takes_over_crashed_before_commit_transition(self):
        tx=self.engine.create(intent='commit crash',idempotency_key='tx:commit-crash')
        tx.add(ActionSpec('demo.create',{'name':'x'},EffectClass.REVERSIBLE,'action:commit-crash'))
        tx.prepare()
        self.engine.store.claim_worker(tx.id,'dead-worker')
        with self.assertRaises(ConcurrentModificationError): tx.commit()
        self.assertEqual(tx.resume(confirm_worker_stopped=True).status,TransactionStatus.COMMITTED)

    def test_resume_explicitly_takes_over_crashed_before_prepare_transition(self):
        tx=self.engine.create(intent='preparation crash',idempotency_key='tx:created-crash')
        tx.add(ActionSpec('demo.create',{'name':'x'},EffectClass.REVERSIBLE,'action:created-crash'))
        self.engine.store.claim_worker(tx.id,'dead-worker')
        self.assertEqual(tx.resume(confirm_worker_stopped=True).status,TransactionStatus.PREPARED)
