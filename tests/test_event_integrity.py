import json, tempfile, unittest
from pathlib import Path
from agenttxn import ActionSpec, EffectClass, Engine, Policy
from agenttxn.adapters import DemoAdapter

class EventIntegrityTests(unittest.TestCase):
    def test_modified_event_detected(self):
        with tempfile.TemporaryDirectory() as d:
            e=Engine.open(Path(d)/"state.db",policy=Policy(require_approval_for_reversible=False),adapters=[DemoAdapter()])
            tx=e.create(intent="integrity",idempotency_key="tx:integrity")
            tx.add(ActionSpec("demo.create",{"name":"x"},EffectClass.REVERSIBLE,"action:integrity"))
            self.assertTrue(tx.verify().valid)
            c=e.store.raw_connection_for_tests()
            try:
                row=c.execute("SELECT id,payload_json FROM events WHERE transaction_id=? ORDER BY sequence LIMIT 1",(tx.id,)).fetchone()
                payload=json.loads(row["payload_json"]); payload["intent"]="tampered"
                c.execute("UPDATE events SET payload_json=? WHERE id=?",(json.dumps(payload),row["id"])); c.commit()
            finally: c.close()
            r=tx.verify(); self.assertFalse(r.valid); self.assertFalse(r.checks["event_chain"])
if __name__=="__main__": unittest.main()
