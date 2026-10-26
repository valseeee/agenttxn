"""Real spawned-process ownership and abrupt-exit recovery, without network calls."""
import multiprocessing
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import sqlite3
from unittest.mock import Mock

from agenttxn import ActionSpec, EffectClass, Engine, Policy, StepStatus, TransactionStatus
from agenttxn.adapters import DemoAdapter
from agenttxn.errors import ConcurrentModificationError, InvalidTransitionError


def open_engine(directory):
    directory = Path(directory)
    return Engine.open(directory / 'state.db', policy=Policy(require_approval_for_reversible=False),
                       adapters=[DemoAdapter(directory / 'provider.json')])


def claim_worker(directory, txid, token, ready, release, results):
    engine = open_engine(directory)
    ready.wait(15)
    try:
        engine.store.claim_worker(txid, token)
    except ConcurrentModificationError:
        results.put('blocked')
    else:
        results.put('claimed')
        release.wait(15)
        engine.store.release_worker(txid, token)


def crash_worker(directory, txid, boundary, phase):
    engine = open_engine(directory)
    adapter = engine._adapters['demo.create']
    original_execute, original_compensate = adapter.execute, adapter.compensate

    def logged_call(original, operation, *args):
        with (Path(directory) / 'calls.txt').open('a', encoding='utf-8') as log:
            log.write(operation + '\n')
        result = original(*args)
        if boundary == 'after_call' and operation == phase:
            os._exit(73)
        return result

    adapter.execute = lambda *args: logged_call(original_execute, 'forward', *args)
    adapter.compensate = lambda *args: logged_call(original_compensate, 'compensation', *args)
    original_claim = engine.store.claim_step
    original_update = engine.store.update_step
    original_worker = engine.store.claim_worker
    original_transition = engine._transition

    def worker_claimed(*args, **kwargs):
        original_worker(*args, **kwargs)
        if boundary == 'after_worker_claim':
            os._exit(73)

    def transitioned(*args, **kwargs):
        result = original_transition(*args, **kwargs)
        target = TransactionStatus.COMPENSATING if phase == 'compensation' else TransactionStatus.COMMITTING
        if boundary == 'after_transition' and args[1] == target:
            os._exit(73)
        return result

    def claimed(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        if result and boundary == 'after_claim' and kwargs['phase'] == phase:
            os._exit(73)
        return result

    def updated(*args, **kwargs):
        result = original_update(*args, **kwargs)
        target = StepStatus.SUCCEEDED if phase == 'forward' else StepStatus.COMPENSATED
        if boundary == 'after_step' and kwargs.get('status') == target:
            os._exit(73)
        return result

    engine.store.claim_step = claimed
    engine.store.update_step = updated
    engine.store.claim_worker = worker_claimed
    engine._transition = transitioned
    engine.load(txid).commit()
    os._exit(74)  # A missed injection must fail the parent assertion.


class ProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = multiprocessing.get_context('spawn')

    def transaction(self, directory, compensation=False):
        engine = open_engine(directory)
        tx = engine.create(intent='process recovery', idempotency_key='tx')
        tx.add(ActionSpec('demo.create', {'name': 'first'}, EffectClass.REVERSIBLE, 'first'))
        if compensation:
            tx.add(ActionSpec('demo.fail', {}, EffectClass.READ_ONLY, 'fail'))
        else:
            tx.add(ActionSpec('demo.create', {'name': 'second'}, EffectClass.REVERSIBLE, 'second'))
        tx.prepare()
        return tx

    def stop(self, process):
        if process.is_alive():
            process.terminate()
        process.join(10)

    def test_two_processes_cannot_claim_same_transaction(self):
        tx = self.transaction(self.temp.name)
        ready, release, results = self.context.Event(), self.context.Event(), self.context.Queue()
        self.addCleanup(results.close)
        processes = [self.context.Process(target=claim_worker,
                     args=(self.temp.name, tx.id, str(i), ready, release, results)) for i in range(2)]
        for process in processes:
            process.start()
            self.addCleanup(self.stop, process)
        ready.set()
        try:
            self.assertCountEqual([results.get(timeout=15), results.get(timeout=15)], ['claimed', 'blocked'])
        finally:
            release.set()
        for process in processes:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        self.assertFalse(tx.engine.store.worker_info(tx.id)['claimed'])
        self.assertTrue(tx.verify().valid)

    def check_crash(self, boundary, phase):
        directory = Path(self.temp.name) / (boundary + phase)
        directory.mkdir()
        tx = self.transaction(directory, compensation=phase == 'compensation')
        process = self.context.Process(target=crash_worker, args=(str(directory), tx.id, boundary, phase))
        process.start()
        self.addCleanup(self.stop, process)
        process.join(15)
        self.assertEqual(process.exitcode, 73)
        engine = open_engine(directory)
        tx = engine.load(tx.id)
        adapter = engine._adapters['demo.create']
        adapter.execute = Mock(wraps=adapter.execute)
        adapter.compensate = Mock(wraps=adapter.compensate)
        self.assertTrue(engine.store.worker_info(tx.id)['claimed'])
        with self.assertRaises(ConcurrentModificationError):
            tx.resume()
        with self.assertRaises(ConcurrentModificationError):
            tx.reconcile()
        if boundary in {'after_step', 'after_worker_claim', 'after_transition'}:
            report = tx.resume(confirm_worker_stopped=True)
        else:
            report = tx.reconcile(confirm_worker_stopped=True)
        expected = TransactionStatus.COMPENSATED if phase == 'compensation' else TransactionStatus.COMMITTED
        self.assertEqual(report.status, expected)
        self.assertTrue(tx.verify().valid, tx.verify().messages)
        self.assertFalse(engine.store.worker_info(tx.id)['claimed'])
        calls = (directory / 'calls.txt').read_text(encoding='utf-8').splitlines() if (directory / 'calls.txt').exists() else []
        self.assertEqual(calls.count('forward') + adapter.execute.call_count, 2)
        self.assertEqual(calls.count('compensation') + adapter.compensate.call_count,
                         1 if phase == 'compensation' else 0)
        with closing(sqlite3.connect(directory / 'state.db')) as connection:
            attempts = connection.execute('SELECT phase,status FROM attempts').fetchall()
        if boundary == 'after_claim':
            self.assertIn((phase, 'RECONCILED_NOT_EXECUTED'), attempts)
        self.assertEqual(len(attempts), (3 if phase == 'compensation' else 2) + (boundary == 'after_claim'))
        effects = engine._adapters['demo.create'].effects
        self.assertEqual(len(effects), 0 if phase == 'compensation' else 2)
        for _ in range(2):
            with self.assertRaises(InvalidTransitionError):
                tx.resume(confirm_worker_stopped=True)
        self.assertTrue(tx.verify().valid)

    def test_forward_crash_after_claim(self):
        self.check_crash('after_claim', 'forward')

    def test_forward_crash_after_worker_claim(self):
        self.check_crash('after_worker_claim', 'forward')

    def test_forward_crash_after_transaction_transition(self):
        self.check_crash('after_transition', 'forward')

    def test_compensation_crash_after_transaction_transition(self):
        self.check_crash('after_transition', 'compensation')

    def test_forward_crash_after_provider_write(self):
        self.check_crash('after_call', 'forward')

    def test_forward_crash_after_step_writeback(self):
        self.check_crash('after_step', 'forward')

    def test_compensation_crash_after_claim(self):
        self.check_crash('after_claim', 'compensation')

    def test_compensation_crash_after_provider_write(self):
        self.check_crash('after_call', 'compensation')

    def test_compensation_crash_after_step_writeback(self):
        self.check_crash('after_step', 'compensation')


if __name__ == '__main__':
    unittest.main()
