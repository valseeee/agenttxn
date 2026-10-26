from contextlib import redirect_stdout,closing
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agenttxn import ActionSpec,EffectClass,Engine,Policy
from agenttxn.cli import main
from agenttxn import telemetry
from agenttxn.store import SCHEMA,SQLiteStore
from agenttxn.errors import StorageError


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.db'
        self.engine=Engine.open(self.path,policy=Policy(require_approval_for_reversible=False))
        self.tx=self.engine.create(intent='SECRET_INTENT',idempotency_key='SECRET_KEY',metadata={'secret':'SECRET_METADATA'})

    def test_safe_events_omit_arbitrary_business_fields(self):
        self.engine.store.record_event(self.tx.id,'CUSTOM_SECRET_EVENT',{'status':'SECRET_STATUS','token':'SECRET_TOKEN'})
        output=json.dumps(telemetry.events(self.engine.store,self.tx.id))
        self.assertNotIn('SECRET',output)
        self.assertIn('OTHER',output)

    def test_metrics_are_counts_with_fixed_labels(self):
        report=telemetry.metrics(self.engine.store)
        self.assertEqual(report['transactions']['CREATED'],1)
        self.assertEqual(report['claimed_workers'],0)
        self.assertNotIn('SECRET',json.dumps(report))
        self.assertIn('agenttxn_transactions{status="CREATED"} 1',telemetry.prometheus(report))

    def test_no_alert_for_normal_created_transaction(self):
        self.assertEqual(telemetry.alerts(self.engine.store),[])

    def test_unknown_outcome_emits_actionable_alert(self):
        from agenttxn.adapters import DemoAdapter
        self.engine.register(DemoAdapter())
        self.tx.add(ActionSpec('demo.unknown',{'name':'x'},EffectClass.REVERSIBLE,'unknown'))
        self.tx.prepare();self.tx.commit()
        self.assertEqual(telemetry.alerts(self.engine.store),[{'transaction_id':self.tx.id,
            'code':'NEEDS_RECONCILIATION','severity':'warning'}])

    def test_cli_observability_does_not_initialize_adapters(self):
        for args in (['metrics'],['metrics','--format','prometheus'],['alerts'],['tx','telemetry',self.tx.id]):
            with self.subTest(args=args),patch('agenttxn.cli.GitHubIssuesAdapter',side_effect=AssertionError('network adapter initialized')):
                with patch.dict('os.environ',{'GITHUB_TOKEN':'fake'},clear=False),redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(['--db',str(self.path),*args]),0)
                self.assertNotIn('SECRET',output.getvalue())

    def test_readonly_diagnostics_do_not_create_missing_database(self):
        missing=Path(self.temp.name)/'missing'/'state.db'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--db',str(missing),'tx','list']),1)
        self.assertFalse(missing.parent.exists())

    def test_readonly_legacy_database_is_not_migrated(self):
        path=Path(self.temp.name)/'legacy.db'
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(SCHEMA)
        with self.assertRaises(StorageError): SQLiteStore(path,read_only=True)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertNotIn('worker_token',[row[1] for row in connection.execute('PRAGMA table_info(transactions)')])

    def test_readonly_store_rejects_writes(self):
        store=SQLiteStore(self.path,read_only=True)
        with self.assertRaises(StorageError):store.claim_worker(self.tx.id,'forbidden')
        self.assertFalse(self.engine.store.worker_info(self.tx.id)['claimed'])


if __name__=='__main__': unittest.main()
