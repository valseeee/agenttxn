from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from agenttxn import Engine, Policy
from agenttxn.adapters import GitHubIssuesAdapter
from agenttxn.errors import ActionExecutionError, OutcomeUnknownError


class TransportTests(unittest.TestCase):
    def test_uncertain_http_writes_are_not_known_failures(self):
        adapter = GitHubIssuesAdapter('SENSITIVE_TEST_TOKEN')
        for code in (408, 429, 500, 502, 503):
            with self.subTest(code=code), patch('agenttxn.adapters.github.urlopen',
                    side_effect=HTTPError('https://example.invalid', code, 'SECRET_RESPONSE', {}, io.BytesIO())):
                with self.assertRaises(OutcomeUnknownError) as raised:
                    adapter._request('POST', '/repos/owner/repo/issues', {}, write=True)
                self.assertNotIn('SECRET', str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)

    def test_network_failures_and_chained_secret_text_are_redacted(self):
        adapter = GitHubIssuesAdapter('SENSITIVE_TEST_TOKEN')
        for error in (TimeoutError('SECRET_TIMEOUT'), URLError('SECRET_CONNECTION')):
            with patch('agenttxn.adapters.github.urlopen', side_effect=error):
                with self.assertRaises(OutcomeUnknownError) as raised:
                    adapter._request('POST', '/repos/owner/repo/issues', {}, write=True)
                self.assertNotIn('SECRET', str(raised.exception))
                self.assertTrue(raised.exception.__suppress_context__)

    def test_malformed_write_response_is_unknown(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'not-json-SECRET'
        with patch('agenttxn.adapters.github.urlopen', return_value=response):
            with self.assertRaises(OutcomeUnknownError):
                GitHubIssuesAdapter('fake')._request('POST', '/repos/owner/repo/issues', {}, write=True)

    def test_known_http_rejection_is_known_failure(self):
        with patch('agenttxn.adapters.github.urlopen', side_effect=HTTPError('https://example.invalid', 403, 'SECRET', {}, io.BytesIO())):
            with self.assertRaises(ActionExecutionError):
                GitHubIssuesAdapter('fake')._request('POST', '/repos/owner/repo/issues', {}, write=True)


class LockContentionTests(unittest.TestCase):
    def test_worker_claim_waits_for_busy_database_without_losing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.db'
            engine = Engine.open(path, policy=Policy.default_safe())
            tx = engine.create(intent='busy database', idempotency_key='busy')
            entered = threading.Event()
            with closing(sqlite3.connect(path)) as blocker:
                blocker.execute('BEGIN IMMEDIATE')
                def claim():
                    entered.set()
                    engine.store.claim_worker(tx.id, 'worker')
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(claim)
                    try:
                        self.assertTrue(entered.wait(5))
                        self.assertFalse(pending.done())
                    finally:
                        blocker.rollback()
                    pending.result(timeout=10)
            self.assertTrue(engine.store.worker_info(tx.id)['claimed'])
            engine.store.release_worker(tx.id, 'wrong-owner')
            self.assertTrue(engine.store.worker_info(tx.id)['claimed'])
            engine.store.release_worker(tx.id, 'worker')
            self.assertFalse(engine.store.worker_info(tx.id)['claimed'])
            self.assertTrue(tx.verify().valid)


if __name__ == '__main__':
    unittest.main()
