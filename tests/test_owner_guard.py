from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agenttxn import ActionSpec, EffectClass, Engine, Policy, StepStatus, TransactionStatus
from agenttxn.adapters import DemoAdapter
from agenttxn.errors import ConcurrentModificationError


class OwnerGuardTests(unittest.TestCase):
    def test_taken_over_worker_cannot_persist_a_late_provider_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.db'
            adapter=DemoAdapter()
            policy=Policy(require_approval_for_reversible=False)
            engine=Engine.open(path,policy=policy,adapters=[adapter])
            other=Engine.open(path,policy=policy,adapters=[adapter])
            tx=engine.create(intent='late response',idempotency_key='late')
            tx.add(ActionSpec('demo.create',{'name':'late'},EffectClass.REVERSIBLE,'late-action'))
            tx.prepare()
            execute=adapter.execute
            def take_over(*args):
                result=execute(*args)
                other.store.claim_worker(tx.id,'replacement',force=True)
                return result
            adapter.execute=take_over
            with self.assertRaises(ConcurrentModificationError):
                tx.commit()
            self.assertEqual(tx.record.status,TransactionStatus.COMMITTING)
            self.assertEqual(tx.steps[0].status,StepStatus.EXECUTING)
            self.assertTrue(other.store.worker_info(tx.id)['claimed'])
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('SELECT status FROM attempts').fetchone()[0],'STARTED')
            adapter.execute=execute
            other.store.release_worker(tx.id,'replacement')
            self.assertEqual(tx.reconcile(confirm_worker_stopped=True).status,TransactionStatus.COMMITTED)
            self.assertEqual(len(adapter.effects),1)
            self.assertTrue(tx.verify().valid)


if __name__=='__main__': unittest.main()
