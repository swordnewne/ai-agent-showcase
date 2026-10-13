import argparse
import fcntl
import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import preopen_guard as guard
import preopen_runs as runs


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = [
            patch.object(runs, 'DB_PATH', self.root / 'runs.db'),
            patch.object(guard, 'LOCK_PATH', self.root / 'guard.lock'),
            patch.object(guard, 'today_str', return_value='2026-10-09'),
            patch.dict('os.environ', {'NIKO_FINANCE_PG_SHADOW_WRITE': '0'}),
        ]
        for p in self.patchers:
            p.start()
        self.path = self.root / 'report.md'
        self.path.write_text('# Test report\n\n## 七、数据缺失项\n- test\n', encoding='utf-8')
        self.args = argparse.Namespace(slot='22:00', force=False, ignore_calendar=False, deliver_only=False, run_id=None)

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.temp.cleanup()

    def row(self, status='complete', slot='08:15'):
        return runs.get_run(runs.record_run('2026-10-09', slot, status, self.path))

    def gateway(self, row):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        marker = f"finance-report:{row['id']}:{digest}"
        def call(method, params):
            if method == 'chat.history':
                return {'messages': []}
            if method == 'chat.inject':
                self.assertEqual(params['sessionKey'], 'agent:main:main')
                self.assertIn(marker, params['message'])
                return {'ok': True, 'messageId': 'message-1'}
            return {'ok': True, 'message': {'role': 'assistant', '__openclaw': {'id': 'message-1'}, 'content': [{'type': 'text', 'text': marker}]}}
        return call

    def test_generated_is_pending_and_exact_row_update(self):
        first = self.row()
        second = self.row('partial', '22:00')
        self.assertEqual(first['delivery_status'], 'pending')
        with patch.object(guard, '_gateway_call', side_effect=self.gateway(first)):
            guard.deliver(first)
        self.assertEqual(runs.get_run(first['id'])['delivery_status'], 'delivered')
        self.assertEqual(runs.get_run(second['id'])['delivery_status'], 'pending')

    def test_delivery_retry_never_recollects_and_keeps_message_id(self):
        row = self.row()
        good = self.gateway(row)
        def broken(method, params):
            if method == 'chat.message.get':
                raise RuntimeError('Gateway interrupted after write')
            return good(method, params)
        with patch.object(guard, '_gateway_call', side_effect=broken), patch.object(guard.preopen_report, 'run') as generate:
            self.assertEqual(guard.execute(self.args), 1)
            generate.assert_not_called()
        self.assertEqual(runs.get_run(row['id'])['delivery_message_id'], 'message-1')
        with patch.object(guard, '_gateway_call', side_effect=good) as call, patch.object(guard.preopen_report, 'run') as generate:
            self.assertEqual(guard.execute(self.args), 0)
            generate.assert_not_called()
            self.assertEqual([c.args[0] for c in call.call_args_list], ['chat.message.get'])

    def test_interrupted_receipt_recovers_existing_main_message(self):
        row = self.row()
        message = self.gateway(row)('chat.message.get', {})['message']
        def call(method, params):
            if method == 'chat.history':
                return {'messages': [message]}
            if method == 'chat.inject':
                self.fail('Must recover existing write, not duplicate it')
            return {'ok': True, 'message': message}
        with patch.object(guard, '_gateway_call', side_effect=call):
            self.assertEqual(guard.execute(self.args), 0)

    def test_false_readback_cannot_mark_delivered(self):
        row = self.row()
        good = self.gateway(row)
        def call(method, params):
            if method == 'chat.message.get':
                return {'ok': True, 'message': {'content': [{'type': 'text', 'text': 'wrong message'}]}}
            return good(method, params)
        with patch.object(guard, '_gateway_call', side_effect=call):
            self.assertEqual(guard.execute(self.args), 1)
        self.assertEqual(runs.get_run(row['id'])['delivery_status'], 'failed')

    def test_partial_evening_retry_runs_generation(self):
        self.row('partial')
        row = None
        def generate(**kwargs):
            nonlocal row
            row = self.row('complete', kwargs['slot'])
            return {'status': 'complete', 'run_id': row['id'], 'report_path': str(self.path)}
        def call(method, params):
            return self.gateway(row)(method, params)
        with patch.object(guard.preopen_report, 'run', side_effect=generate) as gen, patch.object(guard, '_gateway_call', side_effect=call):
            self.assertEqual(guard.execute(self.args), 0)
            gen.assert_called_once_with(slot='22:00', ignore_calendar=False)

    def test_missing_report_cannot_skip_generation(self):
        row = self.row()
        self.path.unlink()
        self.assertIsNone(runs.completed_run('2026-10-09'))

    def test_morning_refreshes_report_generated_before_scheduled_time(self):
        row = self.row()
        conn = runs._connect()
        conn.execute("UPDATE preopen_runs SET created_at='2026-10-08 16:32:44', delivery_status='delivered' WHERE id=?", (row['id'],))
        conn.commit()
        conn.close()
        self.args.slot = '08:15'
        def generate(**kwargs):
            latest = self.row('complete', kwargs['slot'])
            return {'status': 'complete', 'run_id': latest['id']}
        with patch.object(guard, 'datetime') as clock, patch.object(guard.preopen_report, 'run', side_effect=generate) as gen, patch.object(guard, 'deliver'):
            clock.fromisoformat.side_effect = datetime.fromisoformat
            clock.now.return_value = datetime(2026, 10, 9, 8, 15, tzinfo=runs.CST)
            self.assertEqual(guard.execute(self.args), 0)
            gen.assert_called_once_with(slot='08:15', ignore_calendar=False)

    def test_morning_delivery_retry_reuses_report_after_scheduled_time(self):
        row = self.row()
        conn = runs._connect()
        conn.execute("UPDATE preopen_runs SET created_at='2026-10-09 00:16:00', delivery_status='failed' WHERE id=?", (row['id'],))
        conn.commit()
        conn.close()
        self.args.slot = '08:15'
        with patch.object(guard, 'datetime') as clock, patch.object(guard.preopen_report, 'run') as gen, patch.object(guard, 'deliver') as deliver:
            clock.fromisoformat.side_effect = datetime.fromisoformat
            clock.now.return_value = datetime(2026, 10, 9, 8, 30, tzinfo=runs.CST)
            self.assertEqual(guard.execute(self.args), 0)
            gen.assert_not_called()
            self.assertEqual(deliver.call_args.args[0]['id'], row['id'])

    def test_generation_failure_is_recorded_and_retryable(self):
        with patch.object(guard.preopen_report, 'run', side_effect=RuntimeError('source unavailable')), patch.object(guard, 'deliver'):
            self.assertEqual(guard.execute(self.args), 1)
        self.assertEqual(runs.get_today_runs('2026-10-09')[0][1], 'failed')
        self.assertIsNone(runs.completed_run('2026-10-09'))

    def test_concurrent_entrypoint_does_not_generate(self):
        with guard.LOCK_PATH.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(guard, 'execute') as execute:
                self.assertEqual(guard.main(['--slot', '08:15']), 0)
                execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
