import tempfile, unittest
from pathlib import Path
from agenttxn import ActionSpec, EffectClass, Engine, Policy, TransactionStatus
from agenttxn.adapters import DemoAdapter
from agenttxn.errors import PolicyDeniedError

class PolicyTests(unittest.TestCase):
    def test_irreversible_denied(self):
        with tempfile.TemporaryDirectory() as d:
            e=Engine.open(Path(d)/"state.db",policy=Policy.default_safe(),adapters=[DemoAdapter()])
            tx=e.create(intent="irreversible",idempotency_key="tx:irr")
            tx.add(ActionSpec("demo.create",{"name":"x"},EffectClass.IRREVERSIBLE,"action:irr"))
            with self.assertRaises(PolicyDeniedError): tx.prepare()
            self.assertEqual(tx.record.status,TransactionStatus.PREPARATION_FAILED)
if __name__=="__main__": unittest.main()
