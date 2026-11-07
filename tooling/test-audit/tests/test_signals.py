"""Interrupts are never lost: SIGINT inherited as ignored (a background job
of a non-interactive shell), signals the OS delivers to the output-reader
thread, and signals that arrive inside a deferred cleanup section."""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest

from ta_helpers import HERE, PY, TOOL, FixtureCase, hermetic_git_env, write
from test_hardening import CALC, calc_repo, keeper_cmd

sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "skills", "test-audit", "scripts")))
from test_audit import core  # noqa: E402


class InheritedIgnoreTest(FixtureCase):
    """`test-audit mutate ... &` from a script starts with SIGINT = SIG_IGN."""

    def _start(self, root, ignore):
        ready = os.path.join(self._tmp, "ready")
        child = os.path.join(self._tmp, "child.py")
        write(self._tmp, {"child.py": "import os, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                      "open(%r, 'w').write(str(os.getpid()))\ntime.sleep(10)\n"
                                      "open('src/calc.py', 'w').write('CLOBBERED\\n')\n" % ready})
        keeper = os.path.join(self._tmp, "keeper.py")
        write(self._tmp, {"keeper.py": "import subprocess, sys, time\nsubprocess.Popen([sys.executable, %r], "
                                       "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\ntime.sleep(60)\n" % child})

        def preexec():
            for sig in ignore:
                signal.signal(sig, signal.SIG_IGN)

        proc = subprocess.Popen([TOOL, "mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                                 "--test", "tests/test_add.py", "--test-cmd", keeper_cmd(keeper), "--skip-precheck"],
                                cwd=root, env=dict(hermetic_git_env(), TEST_AUDIT_PYTHON=PY), preexec_fn=preexec,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while not (os.path.exists(ready) and open(ready).read()):
            self.assertLess(time.time(), deadline, "grandchild never started")
            time.sleep(0.05)
        return proc, int(open(ready).read())

    def _assert_prompt(self, proc, pid, root, code, start):
        self.assertEqual(proc.wait(timeout=20), code)
        self.assertLess(time.monotonic() - start, 8)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with open(os.path.join(root, "src/calc.py")) as f:
            self.assertEqual(f.read(), CALC)

    def test_sigint_inherited_as_ignored_still_interrupts_and_restores(self):
        root = calc_repo(self)
        proc, pid = self._start(root, [signal.SIGINT])
        start = time.monotonic()
        proc.send_signal(signal.SIGINT)
        self._assert_prompt(proc, pid, root, 130, start)

    def test_sighup_ignored_by_nohup_stays_ignored_and_sighup_otherwise_exits_129(self):
        root = calc_repo(self)
        proc, pid = self._start(root, [signal.SIGHUP])  # nohup
        proc.send_signal(signal.SIGHUP)
        time.sleep(0.6)
        self.assertIsNone(proc.poll(), "an inherited SIG_IGN for SIGHUP must be respected")
        start = time.monotonic()
        proc.send_signal(signal.SIGINT)
        self._assert_prompt(proc, pid, root, 130, start)
        root2 = self.repo({"src/calc.py": CALC, "runner.py": "", "tests/test_add.py": "def test_add():\n    pass\n"}, name="r2")
        os.remove(os.path.join(self._tmp, "ready"))
        proc, pid = self._start(root2, [])
        start = time.monotonic()
        proc.send_signal(signal.SIGHUP)
        self._assert_prompt(proc, pid, root2, 129, start)


@unittest.skipUnless(hasattr(signal, "pthread_kill"), "needs pthread_kill")
class InProcessDeliveryTest(unittest.TestCase):
    """The OS may hand a process-directed signal to any thread; these send it
    to a chosen non-main thread with pthread_kill to make that deterministic."""

    def setUp(self):
        self.saved = {s: signal.getsignal(s) for s in core.INTERRUPT_SIGNALS}
        core.install_signal_handlers()

    def tearDown(self):
        for s, h in self.saved.items():
            signal.signal(s, h)

    def _poke_later(self, delay, pick, sig=signal.SIGINT):
        def poke():
            time.sleep(delay)
            for t in threading.enumerate():
                if pick(t):
                    signal.pthread_kill(t.ident, sig)

        threading.Thread(target=poke, name="poker", daemon=True).start()

    def test_signal_delivered_to_the_reader_thread_interrupts_promptly(self):
        marker = os.path.join(os.environ.get("TMPDIR", "/tmp"), "ta-reader-%d" % os.getpid())
        self._poke_later(0.5, lambda t: t.name == "test-audit-reader")
        start = time.monotonic()
        with self.assertRaises(KeyboardInterrupt):
            core.run_process([sys.executable, "-c", "import time, sys; time.sleep(30); open(sys.argv[1], 'w')", marker], "/")
        self.assertLess(time.monotonic() - start, 3)
        time.sleep(0.2)
        self.assertFalse(os.path.exists(marker), "the child's process group must be gone")

    def test_signal_inside_a_deferred_section_is_held_then_reraised(self):
        helper = threading.Thread(target=time.sleep, args=(2,), name="helper", daemon=True)
        helper.start()
        finished = []
        with self.assertRaises(KeyboardInterrupt):
            with core.deferred_signals():
                # first SIGINT on another thread (a thread signal mask in the
                # main thread could not hold this one), then SIGTERM
                signal.pthread_kill(helper.ident, signal.SIGINT)
                time.sleep(0.2)
                os.kill(os.getpid(), signal.SIGTERM)  # also held; the first one is re-raised
                time.sleep(0.3)
                finished.append(True)
        self.assertEqual(finished, [True], "cleanup must not be cut short")

    def test_terminated_carries_the_signal_exit_code(self):
        for sig, code in ((signal.SIGTERM, 143), (signal.SIGHUP, 129)):
            with self.assertRaises(core.Terminated) as cm:
                with core.deferred_signals():
                    os.kill(os.getpid(), sig)
                    time.sleep(0.2)
            self.assertEqual(cm.exception.exit_code, code)
