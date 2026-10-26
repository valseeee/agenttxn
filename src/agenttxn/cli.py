import argparse, json, os, sys
from pathlib import Path
from dataclasses import asdict, is_dataclass
from enum import Enum
from . import ActionSpec, EffectClass, Engine, Policy, __version__
from .adapters import DemoAdapter, GitHubIssuesAdapter
from .errors import AgentTxnError

def _engine(db, *, load_adapters=True):
    adapters=[]
    repos=os.environ.get("AGENTTXN_GITHUB_REPOS","").strip()
    allowed=frozenset(x.strip() for x in repos.split(",") if x.strip()) if repos else None
    if load_adapters:
        adapters.append(DemoAdapter(f"{db}.demo.json"))
        if os.environ.get("GITHUB_TOKEN"):
            if allowed is None:
                raise ValueError("Set AGENTTXN_GITHUB_REPOS=owner/repo to allow GitHub writes")
            adapters.append(GitHubIssuesAdapter(os.environ["GITHUB_TOKEN"]))
    return Engine.open(db,policy=Policy(allowed_github_repositories=allowed),adapters=adapters)

def _normalize(x):
    if isinstance(x,Enum): return x.value
    if is_dataclass(x): return _normalize(asdict(x))
    if isinstance(x,dict): return {k:_normalize(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)): return [_normalize(v) for v in x]
    return x

def _print(x): print(json.dumps(_normalize(x),indent=2,ensure_ascii=False,default=str))

def build_parser():
    p=argparse.ArgumentParser(prog="agenttxn",description="Durable transactions for agent actions.")
    p.add_argument("--db",default=".agenttxn/state.db",help="SQLite database path (place before subcommand)")
    subs=p.add_subparsers(dest="command",required=True)
    subs.add_parser("init")
    subs.add_parser("version")
    tx=subs.add_parser("tx").add_subparsers(dest="txcmd",required=True)
    c=tx.add_parser("create"); c.add_argument("--intent",required=True); c.add_argument("--idempotency-key",required=True)
    a=tx.add_parser("add"); a.add_argument("transaction_id"); a.add_argument("--kind",required=True); a.add_argument("--payload",required=True); a.add_argument("--effect",choices=[e.value for e in EffectClass],required=True); a.add_argument("--idempotency-key",required=True)
    l=tx.add_parser("list"); l.add_argument("--limit",type=int,default=50)
    for name in ("show","events","prepare","commit","resume","reconcile","verify"):
        q=tx.add_parser(name); q.add_argument("transaction_id")
        if name in {"reconcile","resume"}: q.add_argument("--confirm-worker-stopped",action="store_true")
    q=tx.add_parser("approve"); q.add_argument("transaction_id"); q.add_argument("--by",required=True); q.add_argument("--reason")
    q=tx.add_parser("abort"); q.add_argument("transaction_id"); q.add_argument("--reason",default="aborted by user")
    return p

def main(argv=None):
    parser=build_parser(); args=parser.parse_args(argv)
    try:
        if args.command=="version":
            print(__version__); return 0
        read_only=args.command=="tx" and args.txcmd in {"show","events","verify","list"}
        engine=_engine(args.db,load_adapters=not read_only)
        if args.command=="init":
            Path(args.db).parent.mkdir(parents=True,exist_ok=True)
            print(f"Initialized AgentTxn database: {args.db}"); return 0
        if args.txcmd=="create":
            print(engine.create(intent=args.intent,idempotency_key=args.idempotency_key).id); return 0
        if args.txcmd=="add":
            payload=json.loads(args.payload)
            if not isinstance(payload,dict): raise ValueError("--payload must be a JSON object")
            step=engine.load(args.transaction_id).add(ActionSpec(args.kind,payload,EffectClass(args.effect),args.idempotency_key))
            _print(step); return 0
        if args.txcmd=="list": _print(engine.list(args.limit)); return 0
        tx=engine.load(args.transaction_id)
        if args.txcmd=="show": _print({"transaction":tx.record,"steps":tx.steps,"worker":engine.store.worker_info(tx.id)}); return 0
        if args.txcmd=="events": _print(engine.store.get_events(tx.id)); return 0
        if args.txcmd=="prepare": _print(tx.prepare()); return 0
        if args.txcmd=="approve": _print(tx.approve(approved_by=args.by,reason=args.reason)); return 0
        if args.txcmd=="abort": _print(tx.abort(args.reason)); return 0
        if args.txcmd=="verify":
            r=tx.verify(); _print(r); return 0 if r.valid else 50
        if args.txcmd=="commit": r=tx.commit()
        elif args.txcmd=="resume": r=tx.resume(confirm_worker_stopped=args.confirm_worker_stopped)
        elif args.txcmd=="reconcile": r=tx.reconcile(confirm_worker_stopped=args.confirm_worker_stopped)
        else: parser.error("unsupported command")
        _print(r)
        return 0 if r.status.value in {"COMMITTED","COMPENSATED"} else 1
    except (AgentTxnError,ValueError,TypeError,json.JSONDecodeError) as e:
        print(f"agenttxn: {e}",file=sys.stderr); return 1

if __name__=="__main__": raise SystemExit(main())
