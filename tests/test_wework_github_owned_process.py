"""Contain only owned descendants, including after the process leader exits."""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/wework-github/scripts"))
owned = importlib.import_module("github_connector.owned_process")
GitHubError = importlib.import_module("github_connector.errors").GitHubError


class OwnedProcessTests(unittest.TestCase):
    @unittest.skipIf(
        os.name == "nt", "Real Windows Job Object coverage runs on Windows CI"
    )
    def test_dead_leader_descendants_release_pipes_and_unrelated_process_survives(self):
        with tempfile.TemporaryDirectory(prefix="owned-gh-process-") as directory:
            marker = Path(directory) / "pids.json"
            descendant = (
                "import json,os,subprocess,sys,time;from pathlib import Path;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]));"
                "print('owned descendant ready',flush=True);time.sleep(60)"
            )
            leader = (
                "import subprocess,sys,time;from pathlib import Path;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
                "deadline=time.monotonic()+5;"
                "\nwhile not Path(sys.argv[2]).exists() and time.monotonic()<deadline:time.sleep(.01)"
            )
            unrelated = subprocess.Popen(
                [sys.executable, "-c", "import time;time.sleep(60)"]
            )
            process = owned.spawn(
                [sys.executable, "-c", leader, descendant, str(marker)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            streams = []
            readers = [
                threading.Thread(
                    target=lambda stream=stream: streams.append(stream.read()),
                    daemon=True,
                )
                for stream in (process.stdout, process.stderr)
            ]
            try:
                for reader in readers:
                    reader.start()
                self.assertEqual(process.wait(timeout=7), 0)
                self.assertTrue(marker.exists())
                self.assertTrue(any(reader.is_alive() for reader in readers))
                started = time.monotonic()
                owned.cleanup(process)
                self.assertLess(time.monotonic() - started, 3)
                owned.cleanup(process)
                for reader in readers:
                    reader.join(timeout=3)
                    self.assertFalse(
                        reader.is_alive(), "owned descendants still hold output pipes"
                    )
                self.assertIn(b"owned descendant ready", b"".join(streams))
                self.assertIsNone(
                    unrelated.poll(), "cleanup must never kill unrelated processes"
                )
                for pid in json.loads(marker.read_text()):
                    result = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(pid)],
                        check=False,
                        capture_output=True,
                        timeout=2,
                    )
                    self.assertTrue(
                        not result.stdout.strip()
                        or result.stdout.strip().startswith(b"Z")
                    )
            finally:
                owned.cleanup(process)
                unrelated.kill()
                unrelated.wait(timeout=3)
                for stream in (process.stdout, process.stderr):
                    stream.close()

    @unittest.skipIf(os.name == "nt", "POSIX process group contract")
    def test_live_leader_is_reaped_and_caller_cannot_disable_own_session(self):
        captured = {}

        def popen(arguments, **kwargs):
            captured.update(kwargs)
            return subprocess.Popen(arguments, **kwargs)

        process = owned.spawn(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            popen=popen,
            start_new_session=False,
        )
        try:
            self.assertTrue(captured["start_new_session"])
            self.assertEqual(os.getpgid(process.pid), process.pid)
            owned.cleanup(process)
            self.assertIsNotNone(process.poll())
            owned.cleanup(process)
        finally:
            owned.cleanup(process)

    def test_unknown_process_is_never_signalled(self):
        process = mock.Mock()
        with mock.patch.object(owned.os, "killpg", create=True) as killpg:
            owned.cleanup(process)
        killpg.assert_not_called()
        process.kill.assert_not_called()

    def test_windows_suspends_then_attaches_job_before_resume_and_closes_after_leader_exit(
        self,
    ):
        events = []

        class Process:
            pid = 123

            def wait(self, timeout):
                events.append("reap")
                return 0

        class Job:
            def __init__(self):
                events.append("job")

            def attach(self, process):
                events.append("attach")

            def resume(self, process):
                events.append("resume")

            def close(self):
                events.append("close")

        def popen(arguments, **kwargs):
            self.assertTrue(kwargs["creationflags"] & owned._CREATE_SUSPENDED)
            self.assertTrue(kwargs["creationflags"] & owned._CREATE_NO_WINDOW)
            self.assertTrue(kwargs["creationflags"] & 0x200)
            events.append("spawn")
            return Process()

        with (
            mock.patch.object(owned, "_WINDOWS", True),
            mock.patch.object(owned, "_WindowsJob", Job),
        ):
            process = owned.spawn(["fake-gh"], popen=popen, creationflags=0x200)
            self.assertEqual(events, ["spawn", "job", "attach", "resume"])
            owned.cleanup(process)
            owned.cleanup(process)
        self.assertEqual(events, ["spawn", "job", "attach", "resume", "close", "reap"])

    def test_windows_containment_failure_stops_suspended_process_and_is_redacted(self):
        for phase in ("attach", "resume", "cancel"):
            events = []

            class Process:
                pid = 123

                def kill(self, events=events):
                    events.append("kill")

                def wait(self, timeout, events=events):
                    events.append("reap")
                    return 0

            class Job:
                def attach(self, process, phase=phase):
                    if phase == "attach":
                        raise OSError("PRIVATE_PATH")

                def resume(self, process, phase=phase):
                    if phase == "cancel":
                        raise KeyboardInterrupt()
                    raise OSError("PRIVATE_PATH")

                def close(self, events=events):
                    events.append("close")

            with (
                self.subTest(phase=phase),
                mock.patch.object(owned, "_WINDOWS", True),
                mock.patch.object(owned, "_WindowsJob", Job),
            ):
                with self.assertRaises(
                    KeyboardInterrupt if phase == "cancel" else GitHubError
                ) as caught:
                    owned.spawn(["fake-gh"], popen=lambda *_args, **_kwargs: Process())
                if phase != "cancel":
                    self.assertEqual(caught.exception.code, "gh_login_failed")
                    self.assertNotIn("PRIVATE", caught.exception.message)
                self.assertEqual(events, ["close", "kill", "reap"])

    def test_missing_binary_and_os_errors_are_safe(self):
        for error, code in (
            (FileNotFoundError("PRIVATE_PATH"), "gh_missing"),
            (OSError("PRIVATE_PATH"), "gh_login_failed"),
        ):
            with self.subTest(code=code), self.assertRaises(GitHubError) as caught:
                owned.spawn(["fake-gh"], popen=mock.Mock(side_effect=error))
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("PRIVATE", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
