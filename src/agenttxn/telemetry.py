"""Read-only projections with fixed labels; never export arbitrary business payloads."""
from .models import StepStatus, TransactionStatus

EVENT_TYPES=frozenset({'TRANSACTION_CREATED','STEP_ADDED','WORKER_CLAIMED','WORKER_RELEASED',
    'TRANSACTION_STATUS_CHANGED','PLAN_FROZEN','STEP_PREFLIGHTED','APPROVAL_GRANTED',
    'ATTEMPT_STARTED','ATTEMPT_FINISHED','ATTEMPT_RECONCILED','STEP_EXECUTION_STARTED',
    'STEP_EXECUTION_SUCCEEDED','STEP_EXECUTION_FAILED','STEP_OUTCOME_UNKNOWN','COMPENSATION_STARTED',
    'COMPENSATION_SUCCEEDED','COMPENSATION_FAILED','COMPENSATION_OUTCOME_UNKNOWN',
    'RECONCILIATION_EVIDENCE','RECONCILIATION_QUERY_FAILED','RECONCILIATION_CONFIRMED_SUCCESS',
    'RECONCILIATION_CONFIRMED_NOT_EXECUTED','COMPENSATION_RECONCILED_SUCCESS',
    'COMPENSATION_RECONCILED_NOT_EXECUTED','RECONCILIATION_REQUIRED','PREPARATION_ERROR'})
ATTEMPT_STATUSES=frozenset({'STARTED','SUCCEEDED','FAILED','UNKNOWN','RECONCILED_SUCCEEDED','RECONCILED_NOT_EXECUTED'})


def metrics(store):
    transactions={item.value:0 for item in TransactionStatus}
    steps={item.value:0 for item in StepStatus}
    attempts=[]
    with store._db() as connection:
        for table,counts in (('transactions',transactions),('steps',steps)):
            for row in connection.execute(f'SELECT status,COUNT(*) AS n FROM {table} GROUP BY status'):
                label=row['status'] if row['status'] in counts else 'OTHER'
                counts[label]=counts.get(label,0)+row['n']
        for row in connection.execute('SELECT phase,status,COUNT(*) AS n FROM attempts GROUP BY phase,status'):
            attempts.append({'phase':row['phase'] if row['phase'] in {'forward','compensation'} else 'OTHER',
                'status':row['status'] if row['status'] in ATTEMPT_STATUSES else 'OTHER','count':row['n']})
        claimed=connection.execute('SELECT COUNT(*) AS n FROM transactions WHERE worker_token IS NOT NULL').fetchone()['n']
    return {'transactions':transactions,'steps':steps,'attempts':attempts,'claimed_workers':claimed}


def prometheus(report):
    lines=['# TYPE agenttxn_transactions gauge','# TYPE agenttxn_steps gauge',
           '# TYPE agenttxn_attempts gauge','# TYPE agenttxn_claimed_workers gauge']
    for group in ('transactions','steps'):
        lines.extend(f'agenttxn_{group}{{status="{status}"}} {count}' for status,count in sorted(report[group].items()))
    lines.extend(f'agenttxn_attempts{{phase="{row["phase"]}",status="{row["status"]}"}} {row["count"]}' for row in report['attempts'])
    lines.append(f'agenttxn_claimed_workers {report["claimed_workers"]}')
    return '\n'.join(lines)+'\n'


def alerts(store):
    with store._db() as connection:
        rows=connection.execute("SELECT id,status FROM transactions WHERE status IN ('NEEDS_RECONCILIATION','PARTIAL_FAILURE') ORDER BY created_at,id").fetchall()
    return [{'transaction_id':row['id'],'code':row['status'],
             'severity':'warning' if row['status']=='NEEDS_RECONCILIATION' else 'error'} for row in rows]


def events(store,txid):
    allowed_statuses={item.value for item in TransactionStatus}|{item.value for item in StepStatus}|ATTEMPT_STATUSES
    output=[]
    for event in store.get_events(txid):
        item={'transaction_id':event.transaction_id,'step_id':event.step_id,'sequence':event.sequence,
              'event_type':event.event_type if event.event_type in EVENT_TYPES else 'OTHER','created_at':event.created_at}
        for key,allowed in (('status',allowed_statuses),('phase',{'forward','compensation'}),
                            ('outcome',{'SUCCEEDED','NOT_EXECUTED','UNKNOWN'}),
                            ('evidence_level',{'CONFIRMED','INCONCLUSIVE'})):
            value=event.payload.get(key)
            if isinstance(value,str) and value in allowed: item[key]=value
        output.append(item)
    return output
