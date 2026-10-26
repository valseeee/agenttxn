import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agenttxn import ActionSpec, EffectClass, Engine, Policy, TransactionStatus, StepStatus
from agenttxn.adapters import AdapterContext, DemoAdapter
from agenttxn.errors import PolicyVersionMismatchError
from agenttxn.models import canonical_json

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.adapter=DemoAdapter(self.root/"provider.json")
        self.engine=Engine.open(self.root/"state.db",policy=Policy(require_approval_for_reversible=False),adapters=[self.adapter])

    def _claim_forward(self, tx, step):
        fingerprint=hashlib.sha256(canonical_json(step.action.to_dict()).encode()).hexdigest()
        no=self.engine.store.next_attempt_no(step.id,"forward")
        aid=self.engine.store.claim_step(tx.id,step.id,attempt_no=no,phase="forward",fingerprint=fingerprint,
            allowed_statuses={StepStatus.PENDING,StepStatus.PREFLIGHTED},target_status=StepStatus.EXECUTING)
        self.assertIsNotNone(aid)
        return aid

    def test_unknown_reconciles_without_duplicate(self):
        tx=self.engine.create(intent="unknown",idempotency_key="tx:unknown")
        tx.add(ActionSpec("demo.unknown",{"name":"resource"},EffectClass.REVERSIBLE,"action:unknown"))
        tx.prepare(); r=tx.commit()
        self.assertEqual(r.status,TransactionStatus.NEEDS_RECONCILIATION); self.assertEqual(len(self.adapter.effects),1)
        r=tx.reconcile()
        self.assertEqual(r.status,TransactionStatus.COMMITTED); self.assertEqual(len(self.adapter.effects),1)

    def test_resume_reconciles_crashed_forward_attempt_then_retries_once(self):
        tx=self.engine.create(intent="crash",idempotency_key="tx:crash")
        step=tx.add(ActionSpec("demo.create",{"name":"resource"},EffectClass.REVERSIBLE,"action:crash"))
        tx.prepare(); self.engine._transition(tx.id,TransactionStatus.COMMITTING)
        self.engine.store.update_step(step.id,status=StepStatus.EXECUTING)
        report=tx.resume()
        self.assertEqual(report.status,TransactionStatus.COMMITTING)
        report=tx.reconcile(confirm_worker_stopped=True)  # operator confirms old worker stopped
        self.assertEqual(report.status,TransactionStatus.COMMITTED)
        self.assertEqual(len(self.adapter.effects),1)
        self.assertEqual(tx.steps[0].status,StepStatus.SUCCEEDED)

    def test_recovers_persisted_failure_before_compensation_started(self):
        tx=self.engine.create(intent="crash-after-failure",idempotency_key="tx:crash-after-failure")
        first=tx.add(ActionSpec("demo.create",{"name":"first"},EffectClass.REVERSIBLE,"action:first"))
        second=tx.add(ActionSpec("demo.fail",{},EffectClass.READ_ONLY,"action:second"))
        tx.prepare()
        result=self.adapter.execute(AdapterContext(tx.id,first.id,"synthetic-forward","forward"),first.action,first.preflight or {})
        first_attempt=self._claim_forward(tx,first)
        self.engine.store.finish_attempt(first_attempt,"SUCCEEDED",response=result)
        self.engine._transition(tx.id,TransactionStatus.COMMITTING)
        self.engine.store.update_step(first.id,status=StepStatus.SUCCEEDED,result=result)
        second_attempt=self._claim_forward(tx,second)
        self.engine.store.finish_attempt(second_attempt,"FAILED",error={"message":"simulated crash window"})
        self.engine.store.update_step(second.id,status=StepStatus.FAILED,last_error={"message":"simulated crash window"})
        report=tx.resume()
        self.assertEqual(report.status,TransactionStatus.COMPENSATED)
        self.assertNotIn("action:first",self.adapter.effects)
        self.assertEqual(tx.steps[0].status,StepStatus.COMPENSATED)

    def test_recovers_crash_during_compensation(self):
        tx=self.engine.create(intent="crash-during-compensation",idempotency_key="tx:crash-during-compensation")
        first=tx.add(ActionSpec("demo.create",{"name":"first"},EffectClass.REVERSIBLE,"action:comp-first"))
        second=tx.add(ActionSpec("demo.fail",{},EffectClass.READ_ONLY,"action:comp-second"))
        tx.prepare()
        result=self.adapter.execute(AdapterContext(tx.id,first.id,"synthetic-forward","forward"),first.action,first.preflight or {})
        first_attempt=self._claim_forward(tx,first)
        self.engine.store.finish_attempt(first_attempt,"SUCCEEDED",response=result)
        self.engine.store.update_step(first.id,status=StepStatus.SUCCEEDED,result=result)
        second_attempt=self._claim_forward(tx,second)
        self.engine.store.finish_attempt(second_attempt,"FAILED",error={"message":"simulated known failure"})
        self.engine.store.update_step(second.id,status=StepStatus.FAILED,last_error={"message":"simulated known failure"})
        self.engine._transition(tx.id,TransactionStatus.COMMITTING)
        self.engine._transition(tx.id,TransactionStatus.COMPENSATING)
        fingerprint=hashlib.sha256(canonical_json({"action":first.action.to_dict(),"phase":"compensation"}).encode()).hexdigest()
        aid=self.engine.store.claim_step(tx.id,first.id,attempt_no=1,phase="compensation",fingerprint=fingerprint,
            allowed_statuses={StepStatus.SUCCEEDED},target_status=StepStatus.COMPENSATING)
        self.assertIsNotNone(aid)
        report=tx.resume()
        self.assertEqual(report.status,TransactionStatus.COMPENSATING)
        report=tx.reconcile(confirm_worker_stopped=True)  # operator confirms old worker stopped
        self.assertEqual(report.status,TransactionStatus.COMPENSATED)
        self.assertNotIn("action:comp-first",self.adapter.effects)
        self.assertEqual(tx.steps[0].status,StepStatus.COMPENSATED)

    def test_policy_configuration_mismatch_fails_closed(self):
        tx=self.engine.create(intent="policy-bound",idempotency_key="tx:policy-bound")
        tx.add(ActionSpec("demo.create",{"name":"x"},EffectClass.REVERSIBLE,"action:policy-bound"))
        different=Engine.open(self.root/"state.db",policy=Policy(require_approval_for_reversible=True),adapters=[self.adapter])
        with self.assertRaises(PolicyVersionMismatchError):
            different.load(tx.id).prepare()

    def test_concurrent_step_claim_has_single_winner(self):
        tx=self.engine.create(intent="claim-race",idempotency_key="tx:claim-race")
        step=tx.add(ActionSpec("demo.create",{"name":"x"},EffectClass.REVERSIBLE,"action:claim-race"))
        tx.prepare()
        fingerprint=hashlib.sha256(canonical_json(step.action.to_dict()).encode()).hexdigest()
        def claim():
            return self.engine.store.claim_step(tx.id,step.id,attempt_no=1,phase="forward",fingerprint=fingerprint,
                allowed_statuses={StepStatus.PENDING,StepStatus.PREFLIGHTED},target_status=StepStatus.EXECUTING)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(self.engine.store.get_step(step.id).status,StepStatus.EXECUTING)

if __name__=="__main__": unittest.main()
