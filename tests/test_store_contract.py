"""The same persistence/recovery contract runs against SQLite and optional real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import tempfile
import unittest
import uuid

from agenttxn import ActionSpec, EffectClass, Engine, Policy, TransactionStatus
from agenttxn.adapters import DemoAdapter
from agenttxn.errors import ConcurrentModificationError, DuplicateIdempotencyKeyError, StorageError


class StoreContract:
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key=uuid.uuid4().hex
        self.adapter=DemoAdapter()
        self.policy=Policy(require_approval_for_reversible=False)
        self.engine=self.open_engine()

    def transaction(self,kind='demo.create'):
        tx=self.engine.create(intent='store contract',idempotency_key=self.key)
        tx.add(ActionSpec(kind,{'name':'contract'},EffectClass.REVERSIBLE,self.key))
        return tx

    def test_commit_and_reopen_audit(self):
        tx=self.transaction()
        tx.prepare()
        self.assertEqual(tx.commit().status,TransactionStatus.COMMITTED)
        self.assertTrue(self.open_engine().load(tx.id).verify().valid)

    def test_unknown_reconciliation_does_not_reexecute(self):
        tx=self.transaction('demo.unknown')
        tx.prepare()
        self.assertEqual(tx.commit().status,TransactionStatus.NEEDS_RECONCILIATION)
        self.assertEqual(tx.reconcile().status,TransactionStatus.COMMITTED)
        self.assertEqual(len(self.engine.store.get_attempts(tx.id)),1)
        self.assertTrue(tx.verify().valid)

    def test_compensation(self):
        tx=self.transaction()
        tx.add(ActionSpec('demo.fail',{},EffectClass.READ_ONLY,self.key+':fail'))
        tx.prepare()
        self.assertEqual(tx.commit().status,TransactionStatus.COMPENSATED)
        self.assertFalse(self.adapter.effects)
        self.assertTrue(tx.verify().valid)

    def test_concurrent_claim_has_one_owner(self):
        tx=self.transaction()
        second=self.open_engine()
        def claim(item):
            store,token=item
            try:
                store.claim_worker(tx.id,token)
                return True
            except ConcurrentModificationError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(claim,[(self.engine.store,'one'),(second.store,'two')]))
        self.assertEqual(sum(results),1)
        self.engine.store.release_worker(tx.id,'one' if results[0] else 'two')
        self.assertTrue(tx.verify().valid)

    def test_transaction_and_action_uniqueness(self):
        tx=self.transaction()
        with self.assertRaises(DuplicateIdempotencyKeyError):
            self.engine.create(intent='duplicate',idempotency_key=self.key)
        other=self.engine.create(intent='other',idempotency_key=self.key+':other')
        with self.assertRaises(DuplicateIdempotencyKeyError):
            other.add(tx.steps[0].action)

    def test_owner_scope_rejects_replaced_token(self):
        tx=self.transaction()
        self.engine.store.claim_worker(tx.id,'old')
        with self.engine.store.owner_scope(tx.id,'old'):
            self.open_engine().store.claim_worker(tx.id,'new',force=True)
            with self.assertRaises(ConcurrentModificationError):
                self.engine.store.update_transaction(tx.id,event_type='SHOULD_NOT_APPEAR')
        self.assertNotIn('SHOULD_NOT_APPEAR',[event.event_type for event in self.engine.store.get_events(tx.id)])
        self.engine.store.release_worker(tx.id,'new')


class SQLiteContractTests(StoreContract,unittest.TestCase):
    def open_engine(self):
        return Engine.open(Path(self.temp.name)/'state.db',policy=self.policy,adapters=[self.adapter])


@unittest.skipUnless(os.environ.get('AGENTTXN_POSTGRES_TEST_DSN'),'requires explicit isolated PostgreSQL test DSN')
class PostgresContractTests(StoreContract,unittest.TestCase):
    def open_engine(self):
        dsn=os.environ['AGENTTXN_POSTGRES_TEST_DSN']
        from psycopg.conninfo import conninfo_to_dict
        if not conninfo_to_dict(dsn).get('dbname','').endswith('_test'):
            raise RuntimeError('PostgreSQL tests require a dedicated database ending in _test')
        return Engine.open_postgres(dsn,policy=self.policy,adapters=[self.adapter])

    def test_future_schema_rejected_without_downgrade(self):
        import psycopg
        dsn=os.environ['AGENTTXN_POSTGRES_TEST_DSN']
        try:
            with psycopg.connect(dsn) as connection:
                connection.execute('UPDATE agenttxn.schema_version SET version=99')
            with self.assertRaises(StorageError): self.open_engine()
            with psycopg.connect(dsn) as connection:
                self.assertEqual(connection.execute('SELECT version FROM agenttxn.schema_version').fetchone()[0],99)
        finally:
            with psycopg.connect(dsn) as connection:
                connection.execute('UPDATE agenttxn.schema_version SET version=1')


if __name__=='__main__': unittest.main()
