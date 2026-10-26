"""Isolated multi-container integration fixture. Never point this at a business database."""
import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler,HTTPServer
from urllib.parse import quote
from urllib.request import Request,urlopen

from agenttxn import ActionSpec,EffectClass,Engine,Policy,ReconcileOutcome,TransactionStatus
from agenttxn.adapters import ProviderCapabilities
from agenttxn.errors import ConcurrentModificationError,OutcomeUnknownError


class HTTPProvider:
    kinds=frozenset({'probe.create'})
    def request(self,method,key,body=None):
        data=json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(Request('http://provider:8080/'+key,data=data,method=method,
                                 headers={'Content-Type':'application/json'}),timeout=75) as response:
                return json.load(response)
        except Exception:
            if method!='GET': raise OutcomeUnknownError('probe response lost') from None
            raise
    def validate(self,action):
        if action.kind not in self.kinds: raise ValueError('unsupported probe action')
    def preflight(self,ctx,action): return self.request('GET','resources/'+quote(action.idempotency_key))
    def execute(self,ctx,action,preflight): return self.request('POST','resources/'+quote(action.idempotency_key),action.payload)
    def compensate(self,ctx,action,result,preflight): return self.request('DELETE','resources/'+quote(action.idempotency_key))
    def reconcile(self,ctx,action,phase,result,preflight):
        state=self.preflight(ctx,action)
        if phase=='forward':
            return (ReconcileOutcome.SUCCEEDED,state) if state['exists'] else (ReconcileOutcome.NOT_EXECUTED,None)
        return (ReconcileOutcome.SUCCEEDED,state) if not state['exists'] else (ReconcileOutcome.NOT_EXECUTED,None)
    def can_compensate(self,action): return action.effect==EffectClass.REVERSIBLE
    def capabilities(self,action): return ProviderCapabilities(True,True)


def engine():
    dsn=os.environ['AGENTTXN_POSTGRES_TEST_DSN']
    from psycopg.conninfo import conninfo_to_dict
    if not conninfo_to_dict(dsn).get('dbname','').endswith('_test'): raise ValueError('dedicated test database required')
    return Engine.open_postgres(dsn,policy=Policy(require_approval_for_reversible=False),adapters=[HTTPProvider()])


def wait_until(probe,timeout=90):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=probe()
        if result: return result
        time.sleep(.05)
    raise RuntimeError('container probe timed out')


def provider():
    database=sqlite3.connect('/tmp/agenttxn-provider.db')
    database.executescript('CREATE TABLE resources(key TEXT PRIMARY KEY); CREATE TABLE calls(key TEXT);')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,data):
            payload=json.dumps(data).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def do_GET(self):
            if self.path=='/health': return self.reply({'ready':True})
            if self.path=='/stats':
                return self.reply({'effects':database.execute('SELECT count(*) FROM resources').fetchone()[0],
                                   'calls':database.execute('SELECT count(*) FROM calls').fetchone()[0]})
            key=self.path.split('/resources/',1)[1]
            exists=database.execute('SELECT 1 FROM resources WHERE key=?',(key,)).fetchone() is not None
            self.reply({'exists':exists,'resource_id':key})
        def do_POST(self):
            key=self.path.split('/resources/',1)[1]
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if payload.get('wait_for_contender'):
                # Hold the winner's claim until the other worker actually tries it.
                # This is a test barrier, not a wall-clock scheduling assumption.
                import psycopg
                with psycopg.connect(os.environ['AGENTTXN_POSTGRES_TEST_DSN'],autocommit=True) as connection:
                    wait_until(lambda: connection.execute("SELECT count(*) FROM agenttxn.probe_workers WHERE outcome='BLOCKED'").fetchone()[0]==1,timeout=60)
            time.sleep(.4)
            with database:
                database.execute('INSERT INTO calls VALUES(?)',(key,))
                database.execute('INSERT INTO resources VALUES(?) ON CONFLICT DO NOTHING',(key,))
            if payload.get('lose_response'):
                self.close_connection=True
                return
            self.reply({'exists':True,'resource_id':key})
        def do_DELETE(self):
            with database: database.execute('DELETE FROM resources WHERE key=?',(self.path.split('/resources/',1)[1],))
            self.reply({'removed':True})
    HTTPServer(('0.0.0.0',8080),Handler).serve_forever()


def worker(identity):
    handle=engine()
    def table_ready():
        with handle.store._db() as connection:
            return connection.execute("SELECT to_regclass('agenttxn.probe_workers') AS name").fetchone()['name']
    wait_until(table_ready)
    with handle.store._db(True) as connection:
        connection.execute('INSERT INTO probe_workers(id,outcome) VALUES(?,?)',(identity,'READY'))
    def find_transaction():
        with handle.store._db() as connection:
            return connection.execute("SELECT id FROM transactions WHERE idempotency_key='network-probe:race' AND status IN ('PREPARED','COMMITTING')").fetchone()
    row=wait_until(find_transaction)
    with handle.store._db(True) as connection:
        connection.execute("UPDATE probe_workers SET outcome='ARMED' WHERE id=?",(identity,))
    def start_gate():
        with handle.store._db() as connection:
            return connection.execute('SELECT outcome FROM probe_workers WHERE id=?',(identity,)).fetchone()['outcome']=='START'
    wait_until(start_gate)
    try: outcome=handle.load(row['id']).commit().status.value
    except ConcurrentModificationError: outcome='BLOCKED'
    with handle.store._db(True) as connection:
        connection.execute('UPDATE probe_workers SET outcome=? WHERE id=?',(outcome,identity))
    # Keep workers alive until the coordinator has verified results and Compose stops the stack.
    while True: time.sleep(1)


def coordinate():
    handle=engine()
    with handle.store._db(True) as connection:
        connection.execute('CREATE TABLE probe_workers(id TEXT PRIMARY KEY,outcome TEXT NOT NULL)')
    def ready():
        with handle.store._db() as connection:
            return connection.execute("SELECT count(*) AS n FROM probe_workers WHERE outcome='READY'").fetchone()['n']==2
    wait_until(ready)
    tx=handle.create(intent='isolated container ownership test',idempotency_key='network-probe:race')
    for index in range(2): tx.add(ActionSpec('probe.create',{'wait_for_contender':index==0},EffectClass.REVERSIBLE,f'network-probe:{index}'))
    tx.prepare()
    def armed():
        with handle.store._db() as connection:
            return connection.execute("SELECT count(*) AS n FROM probe_workers WHERE outcome='ARMED'").fetchone()['n']==2
    wait_until(armed)
    with handle.store._db(True) as connection:
        connection.execute("UPDATE probe_workers SET outcome='START'")
    def outcomes():
        with handle.store._db() as connection:
            rows=connection.execute("SELECT id,outcome FROM probe_workers WHERE outcome IN ('BLOCKED','COMMITTED')").fetchall()
        return rows if len(rows)==2 else None
    results=wait_until(outcomes)
    assert sorted(row['outcome'] for row in results)==['BLOCKED','COMMITTED']
    assert tx.record.status==TransactionStatus.COMMITTED and tx.verify().valid
    lost=handle.create(intent='isolated network response loss',idempotency_key='network-probe:lost')
    lost.add(ActionSpec('probe.create',{'lose_response':True},EffectClass.REVERSIBLE,'network-probe:lost-action'))
    lost.prepare();assert lost.commit().status==TransactionStatus.NEEDS_RECONCILIATION
    assert lost.reconcile().status==TransactionStatus.COMMITTED and lost.verify().valid
    stats=HTTPProvider().request('GET','stats')
    assert stats=={'effects':3,'calls':3}
    print(json.dumps({'status':'PASSED','workers':results,'provider':stats,'audits_valid':True,
        'environment':'two separate worker containers, shared PostgreSQL, separate delayed HTTP provider',
        'boundary':'one CI host; not a physical multi-host availability or network-partition test'}),flush=True)


if __name__=='__main__':
    try:
        role=sys.argv[1]
        if role=='provider': provider()
        elif role=='worker': worker(sys.argv[2])
        elif role=='coordinate': coordinate()
        else: raise ValueError('unknown probe role')
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__}),flush=True)
        raise SystemExit(1)
