"""Repeatable process-crash reliability benchmark; no real SaaS or credentials required."""
import argparse
from collections import Counter
import json
import multiprocessing
import os
from pathlib import Path
import platform
import random
import sqlite3
import tempfile
import time
import uuid

from agenttxn import ActionSpec,EffectClass,Engine,Policy,StepStatus,TransactionStatus
from agenttxn.adapters import DemoAdapter


class Provider(DemoAdapter):
    def __init__(self,directory,key,delay_ms=0):
        self.calls=Path(directory)/(key+'.calls')
        self.delay_ms=delay_ms
        super().__init__(Path(directory)/(key+'.json'))

    def execute(self,*args):
        with self.calls.open('a',encoding='utf-8') as output:
            output.write('create\n');output.flush();os.fsync(output.fileno())
        time.sleep(self.delay_ms/1000)
        return super().execute(*args)


def open_engine(directory,key,delay_ms=0,dsn=None):
    adapter=Provider(directory,key,delay_ms)
    kwargs={'policy':Policy(require_approval_for_reversible=False),'adapters':[adapter]}
    engine=Engine.open_postgres(dsn,**kwargs) if dsn else Engine.open(Path(directory)/'state.db',**kwargs)
    return engine,adapter


def worker(directory,txid,key,delay_ms,point,dsn):
    engine,adapter=open_engine(directory,key,delay_ms,dsn)
    claim,execute,update=engine.store.claim_step,adapter.execute,engine.store.update_step
    def claimed(*args,**kwargs):
        result=claim(*args,**kwargs)
        if result and point=='after_claim': os._exit(73)
        return result
    def executed(*args):
        result=execute(*args)
        if point=='after_effect': os._exit(73)
        return result
    def updated(*args,**kwargs):
        result=update(*args,**kwargs)
        if kwargs.get('status')==StepStatus.SUCCEEDED and point=='after_step': os._exit(73)
        return result
    engine.store.claim_step=claimed;adapter.execute=executed;engine.store.update_step=updated
    report=engine.load(txid).commit()
    if point!='none' or report.status!=TransactionStatus.COMMITTED: raise SystemExit(74)


def run(transactions=12,workers=2,latency_ms=20,seed=1,dsn=None):
    if transactions < 1 or workers < 1 or latency_ms < 0: raise ValueError('counts must be positive and latency nonnegative')
    if dsn:
        from psycopg.conninfo import conninfo_to_dict
        if not conninfo_to_dict(dsn).get('dbname','').endswith('_test'):
            raise ValueError('benchmark requires a dedicated PostgreSQL database ending in _test')
    context=multiprocessing.get_context('spawn'); rng=random.Random(seed)
    started=time.perf_counter(); recovered=0; attempts=0; calls=0; plan=[]
    with tempfile.TemporaryDirectory(prefix='agenttxn-benchmark-') as directory:
        for index in range(transactions):
            key=uuid.uuid4().hex; delay=rng.randint(0,latency_ms)
            point=('none','after_claim','after_effect','after_step')[index%4]
            engine,_=open_engine(directory,key,dsn=dsn)
            tx=engine.create(intent='isolated reliability benchmark',idempotency_key=key)
            tx.add(ActionSpec('demo.create',{'name':'benchmark'},EffectClass.REVERSIBLE,key));tx.prepare()
            plan.append((tx.id,key,delay,point))
        for offset in range(0,len(plan),workers):
            active=[]
            try:
                for txid,key,delay,point in plan[offset:offset+workers]:
                    process=context.Process(target=worker,args=(directory,txid,key,delay,point,dsn))
                    process.start();active.append((process,txid,key,point))
                for process,txid,key,point in active:
                    process.join(45)
                    expected=0 if point=='none' else 73
                    if process.exitcode!=expected: raise RuntimeError('worker did not reach expected crash boundary')
                    engine,adapter=open_engine(directory,key,dsn=dsn);tx=engine.load(txid)
                    if point!='none':
                        # join() established that this exact OS process is stopped.
                        if tx.steps[0].status==StepStatus.EXECUTING:
                            report=tx.reconcile(confirm_worker_stopped=True)
                        else: report=tx.resume(confirm_worker_stopped=True)
                        if report.status!=TransactionStatus.COMMITTED: raise RuntimeError('recovery did not commit')
                        recovered+=1
                    if tx.record.status!=TransactionStatus.COMMITTED or not tx.verify().valid:
                        raise RuntimeError('invalid final transaction/audit')
                    count=len(adapter.calls.read_text(encoding='utf-8').splitlines())
                    if count!=1 or len(adapter.effects)!=1: raise RuntimeError('duplicate/missing provider call or effect')
                    calls+=count;attempts+=len(engine.store.get_attempts(txid))
            finally:
                for process,*_ in active:
                    if process.is_alive(): process.terminate()
                    process.join(5)
    elapsed=time.perf_counter()-started
    return {'backend':'postgresql' if dsn else 'sqlite','transactions':transactions,'workers':workers,
        'seed':seed,'latency_ms':{'distribution':'uniform integer','min':0,'max':latency_ms},
        'crash_points':dict(Counter(item[3] for item in plan)),'recovered_crashes':recovered,
        'provider_calls':calls,'durable_effects':transactions,'attempts':attempts,'all_audits_valid':True,
        'elapsed_seconds':elapsed,'transactions_per_second':transactions/elapsed,
        'environment':{'python':platform.python_version(),'os':platform.platform(),'cpu':platform.processor(),
            'logical_cpus':os.cpu_count(),'sqlite':sqlite3.sqlite_version,'sqlite_journal':'WAL' if not dsn else None,
            'storage_device':'not detected; results are not a production storage benchmark'},
        'boundary':'local processes and simulated durable provider; no remote fencing or SaaS outage guarantee'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transactions',type=int,default=12)
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--latency-ms',type=int,default=20)
    parser.add_argument('--seed',type=int,default=1)
    parser.add_argument('--postgres',action='store_true')
    args=parser.parse_args()
    dsn=os.environ.get('AGENTTXN_POSTGRES_TEST_DSN') if args.postgres else None
    if args.postgres and not dsn: parser.error('--postgres requires AGENTTXN_POSTGRES_TEST_DSN')
    try:
        print(json.dumps(run(args.transactions,args.workers,args.latency_ms,args.seed,dsn),indent=2))
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__}))
        raise SystemExit(1)
