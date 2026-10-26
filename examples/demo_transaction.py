"""No-network demo: create a resource, fail the next step, compensate the first."""
from pathlib import Path
import tempfile
from agenttxn import ActionSpec, EffectClass, Engine, Policy
from agenttxn.adapters import DemoAdapter

def main():
    with tempfile.TemporaryDirectory() as d:
        adapter=DemoAdapter(Path(d)/"provider.json")
        engine=Engine.open(Path(d)/"state.db",policy=Policy(require_approval_for_reversible=False),adapters=[adapter])
        tx=engine.create(intent="Demonstrate partial failure",idempotency_key="demo:partial:1")
        tx.add(ActionSpec("demo.create",{"name":"first-resource"},EffectClass.REVERSIBLE,"demo:first"))
        tx.add(ActionSpec("demo.fail",{},EffectClass.READ_ONLY,"demo:forced-failure"))
        print("prepare:",tx.prepare().status.value)
        report=tx.commit()
        print("final:",report.status.value)
        print("reason:",report.message)
        for s in tx.steps: print(f"step {s.sequence}: {s.action.kind} -> {s.status.value}")
        print("event chain valid:",tx.verify().valid)
        print("Expected final state: COMPENSATED")
if __name__=="__main__": main()
