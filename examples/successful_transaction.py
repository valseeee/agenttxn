"""No-network success demo."""
from pathlib import Path
import tempfile
from agenttxn import ActionSpec, EffectClass, Engine, Policy
from agenttxn.adapters import DemoAdapter

def main():
    with tempfile.TemporaryDirectory() as d:
        engine=Engine.open(Path(d)/"state.db",policy=Policy(require_approval_for_reversible=False),
                           adapters=[DemoAdapter(Path(d)/"provider.json")])
        tx=engine.create(intent="Create a simulated resource",idempotency_key="demo:success:1")
        tx.add(ActionSpec("demo.create",{"name":"hello-agenttxn"},EffectClass.REVERSIBLE,"demo:hello"))
        tx.prepare(); report=tx.commit()
        print(f"{report.status.value}: {report.message}")
        print("verification:",tx.verify().valid)
if __name__=="__main__": main()
