"""Real external side effect; use a sandbox repo only."""
import os, sys
from pathlib import Path
from agenttxn import ActionSpec, EffectClass, Engine, Policy
from agenttxn.adapters import GitHubIssuesAdapter

def main():
    token=os.environ.get("GITHUB_TOKEN"); repo=os.environ.get("AGENTTXN_GITHUB_REPO")
    if not token or not repo:
        print("Set GITHUB_TOKEN and AGENTTXN_GITHUB_REPO=owner/sandbox-repo",file=sys.stderr); return 2
    engine=Engine.open(".agenttxn/github-demo.db",policy=Policy(allowed_github_repositories=frozenset({repo})),adapters=[GitHubIssuesAdapter(token)])
    tx=engine.create(intent=f"Create a test issue in {repo}",idempotency_key=f"github-demo:{repo}:v0.5.0")
    tx.add(ActionSpec("github.issue.create",{"repository":repo,"title":"[AgentTxn demo] Adapter test",
        "body":"Created by AgentTxn's optional GitHub adapter."},EffectClass.REVERSIBLE,f"github-demo:{repo}:issue"))
    prep=tx.prepare(); print("prepared:",prep.status.value)
    if prep.approval_required:
        if input("Create a real issue? Type YES: ")!="YES":
            tx.abort("user declined"); print("aborted"); return 0
        tx.approve(approved_by=os.environ.get("USER","local-user"),reason="interactive demo approval")
    report=tx.commit(); print(report.status.value,report.message)
    return 0 if report.status.value=="COMMITTED" else 1
if __name__=="__main__": raise SystemExit(main())
