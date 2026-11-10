"""Offline exit/recovery regression tests; never touches a real application."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import worker


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class Guard:
    def __init__(self):
        self.reject = False
        self.checks = 0
        self.closed = False

    def check(self):
        self.checks += 1
        if self.reject:
            raise worker.RetryLater('workbuddy_became_foreground_stop')

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, owner, pid, created, path=None):
        self.owner = owner
        self.pid = pid
        self.created = created
        self.path = str(worker.EXE) if path is None else path
        self.alive = True
        self.child_ids = []
        self.terminated = 0
        self.killed = 0

    def create_time(self):
        return self.created

    def exe(self):
        return self.path

    def is_running(self):
        return self.alive

    def children(self, recursive=True):
        return [self.owner.process(pid) for pid in self.child_ids
                if pid in self.owner.processes and self.owner.processes[pid].alive]

    def terminate(self):
        self.terminated += 1
        self.alive = False

    def kill(self):
        self.killed += 1
        self.alive = False

    def wait(self, timeout=None):
        if self.alive:
            raise worker.psutil.TimeoutExpired(timeout or 0, self.pid)
        return 0


class ExitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbuddy-close-test-')
        self.store = worker.Store(Path(self.temp.name))
        self.clock = FakeClock()
        self.guard = Guard()
        self.processes = {}
        self.wire_calls = []
        self.endpoint_hook = None
        self.close_hook = None
        self.identity = {'pid': 100, 'created': 1.0, 'family': {'100': 1.0}}
        self.exe_patch=patch.object(worker,'EXE',Path(self.temp.name)/'WorkBuddy.exe')
        self.exe_patch.start()

        parent = self

        class Wire:
            def __init__(self, target):
                self.target = target

            def call(self, method, params=None):
                parent.wire_calls.append(method)
                if method == 'Browser.close' and parent.close_hook:
                    parent.close_hook()
                return {}

            def close(self):
                pass

        self.patches = [
            patch.object(worker.psutil, 'Process', side_effect=self.process),
            patch.object(worker, 'validate_endpoint', side_effect=self.endpoint),
            patch.object(worker, 'CDP', Wire),
            patch.object(worker.time, 'monotonic', side_effect=self.clock.monotonic),
            patch.object(worker.time, 'sleep', side_effect=self.clock.sleep),
            patch.object(worker.psutil, 'wait_procs', side_effect=self.wait_procs),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.exe_patch.stop()
        self.store.close()
        self.temp.cleanup()

    def add(self, pid=100, created=1.0, path=None):
        process = FakeProcess(self, pid, created, path)
        self.processes[pid] = process
        return process

    def process(self, pid):
        proc = self.processes.get(pid)
        if proc is None or not proc.alive:
            raise worker.psutil.NoSuchProcess(pid)
        return proc

    def endpoint(self, allowed_pids=None, port=None):
        if self.endpoint_hook:
            self.endpoint_hook()
        root = self.process(100)
        return {'_owner_pid': root.pid, '_owner_created': root.created}

    def wait_procs(self, procs, timeout=None, callback=None):
        return ([p for p in procs if not p.alive],
                [p for p in procs if p.alive])

    def remember_success(self):
        self.store.success(worker.day_key(), close_target=self.identity)

    def test_close_failure_retains_success_and_pending_exit(self):
        self.add()
        self.remember_success()
        with self.assertRaises(worker.RetryLater):
            worker.close_existing(self.store, self.identity, self.guard)
        self.assertEqual(self.store.get(worker.day_key())['status'], 'success')
        self.assertIsNotNone(self.store.pending_close())
        self.assertEqual(self.wire_calls, ['Browser.close'])

    def test_cached_success_retries_exit_without_claiming(self):
        self.add()
        self.remember_success()
        with patch.object(worker, 'FocusGuard', return_value=self.guard), \
             patch.object(worker, 'PageUI', side_effect=AssertionError('must not claim')):
            self.assertEqual(worker.run(self.store), 2)
        self.assertIsNotNone(self.store.pending_close())
        self.close_hook = lambda: setattr(self.processes[100], 'alive', False)
        with patch.object(worker, 'FocusGuard', return_value=Guard()), \
             patch.object(worker, 'PageUI', side_effect=AssertionError('must not claim')):
            self.assertEqual(worker.run(self.store), 0)
        self.assertIsNone(self.store.pending_close())
        self.assertEqual(self.wire_calls, ['Browser.close', 'Browser.close'])

    def test_missing_root_still_handles_bound_old_child(self):
        child = self.add(101, 2.0)
        self.identity['family']['101'] = 2.0
        self.remember_success()
        worker.close_existing(self.store, self.identity, self.guard)
        self.assertFalse(child.alive)
        self.assertGreaterEqual(child.terminated + child.killed, 1)
        self.assertIsNone(self.store.pending_close())
        self.assertEqual(self.wire_calls, [])

    def test_reused_root_pid_does_not_close_new_instance(self):
        replacement = self.add(100, 99.0)
        self.remember_success()
        worker.close_existing(self.store, self.identity, self.guard)
        self.assertTrue(replacement.alive)
        self.assertEqual(replacement.terminated + replacement.killed, 0)
        self.assertIsNone(self.store.pending_close())
        self.assertEqual(self.wire_calls, [])

    def test_reused_child_pid_is_not_terminated(self):
        replacement = self.add(101, 99.0)
        self.identity['family']['101'] = 2.0
        self.remember_success()
        worker.close_existing(self.store, self.identity, self.guard)
        self.assertTrue(replacement.alive)
        self.assertEqual(replacement.terminated + replacement.killed, 0)
        self.assertIsNone(self.store.pending_close())
        self.assertEqual(self.wire_calls, [])

    def test_bound_pid_with_other_executable_is_not_terminated(self):
        unrelated = self.add(101, 2.0, path=r'C:\Windows\notepad.exe')
        self.identity['family']['101'] = 2.0
        self.remember_success()
        with self.assertRaises(worker.RetryLater):
            worker.close_existing(self.store, self.identity, self.guard)
        self.assertTrue(unrelated.alive)
        self.assertEqual(unrelated.terminated + unrelated.killed, 0)
        self.assertIsNotNone(self.store.pending_close())
        self.assertEqual(self.wire_calls, [])

    def test_foreground_change_before_dispatch_prevents_browser_close(self):
        self.add()
        self.remember_success()
        self.endpoint_hook = lambda: setattr(self.guard, 'reject', True)
        with self.assertRaises(worker.RetryLater):
            worker.close_existing(self.store, self.identity, self.guard)
        self.assertEqual(self.wire_calls, [])
        self.assertIsNotNone(self.store.pending_close())

    def test_child_identity_is_durable_before_close_request(self):
        root = self.add()
        self.add(101, 2.0)
        root.child_ids = [101]
        self.remember_success()
        observed = []
        self.close_hook = lambda: observed.append(self.store.pending_close())
        with self.assertRaises(worker.RetryLater):
            worker.close_existing(self.store, self.identity, self.guard)
        self.assertEqual(observed[0]['family']['101'], 2.0)
        self.assertEqual(self.store.pending_close()['family']['101'], 2.0)

    def test_root_replaced_during_last_guard_is_not_closed(self):
        root = self.add()
        self.remember_success()
        outer = self

        class ReplacingGuard:
            def check(self):
                outer.processes[100] = FakeProcess(outer, 100, 99.0)

        with self.assertRaises(worker.RetryLater):
            worker.close_existing(self.store, self.identity, ReplacingGuard())
        self.assertTrue(self.processes[100].alive)
        self.assertEqual(self.wire_calls, [])
        self.assertIsNotNone(self.store.pending_close())

    def test_lost_close_response_is_confirmed_by_process_exit(self):
        root = self.add()
        self.remember_success()

        def exited_but_reply_lost():
            root.alive = False
            raise OSError('simulated disconnected socket')

        self.close_hook = exited_but_reply_lost
        worker.close_existing(self.store, self.identity, self.guard)
        self.assertEqual(self.wire_calls, ['Browser.close'])
        self.assertIsNone(self.store.pending_close())


if __name__ == '__main__':
    unittest.main(verbosity=2)
