"""Focused unit tests for optional cache-interference campaign supervision."""
from __future__ import annotations

import json
import csv
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.common import campaign
from experiments.common.cache_interference import (
    CacheInterferenceConfig,
    CacheInterferer,
    load_config,
    run_supervised,
    verify_mode,
)


class CacheInterferenceTests(unittest.TestCase):
    def write_interferer(self, directory: Path, *, exit_after_ready: bool = False,
                        ready="READY threads=1 cpus=0", delay=0, ignore_term=False,
                        stats=True) -> Path:
        executable = directory / "cache_interferer"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, signal, sys, time\n"
            "stopped = [False]\n"
            "def stop(*_): stopped[0] = True\n"
            + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else
               "signal.signal(signal.SIGTERM, stop)\n")
            + f"time.sleep({delay})\n"
            + (f"print({ready!r}, flush=True)\n" if ready else "")
            + ("time.sleep(0.15)\nsys.exit(7)\n" if exit_after_ready else "")
            + "while not stopped[0]: time.sleep(0.005)\n"
            + ("print('CACHE_INTERFERER_STATS ' + json.dumps({'period_us': 1000, 'threads': [{'cpu': 0, 'iterations': 3, 'completed_within_period': 2, 'overruns': 1, 'skipped_intervals': 1, 'deadline_misses': 1}]}), flush=True)\n" if stats else "")
        )
        executable.chmod(0o755)
        return executable

    def test_default_toml_parses(self):
        config = load_config(campaign.CACHE_INTERFERENCE_CONFIG)
        self.assertEqual(config.cpus, tuple(range(8)))
        self.assertEqual((config.working_set_mib, config.touch_mib, config.period_us), (1, 1, 1000))
        self.assertIn("--seconds", config.command(Path("/bin/cache_interferer")))
        self.assertEqual(config.command(Path("/bin/cache_interferer"))[-1], "0")

    def test_invalid_toml_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.toml"
            path.write_text("[cache_interference]\ncpus=[0, 0]\nworking_set_mib=1\ntouch_mib=1\nperiod_us=1000\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_config(path)
            for override in ("cpus=[]", "cpus=[true]", "cpus=[-1]", "cpus=[1024]",
                             "working_set_mib=3", "working_set_mib=2048",
                             "touch_mib=2", "touch_mib=0", "period_us=0",
                             "period_us=true", "period_us=1.5", "extra=1"):
                fields = dict(cpus="[0]", working_set_mib="1", touch_mib="1", period_us="1000")
                key, value = override.split("=")
                fields[key] = value
                path.write_text("[cache_interference]\n" + "\n".join(f"{k}={v}" for k, v in fields.items()))
                with self.subTest(override=override), self.assertRaises(ValueError):
                    load_config(path)

    def test_ready_start_stop_records_timing_statistics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            interferer = CacheInterferer(CacheInterferenceConfig((0,), 1, 1, 1000),
                                         self.write_interferer(root), root)
            with interferer:
                self.assertTrue(interferer.status["ready"])
                interferer.check_running()
            status = json.loads((root / "cache_interference.json").read_text())
            self.assertTrue(status["ready"])
            self.assertEqual(status["exit_code"], 0)
            self.assertEqual(status["timing_statistics"]["threads"][0]["overruns"], 1)

    def test_unexpected_exit_is_explicit_and_cleanup_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            interferer = CacheInterferer(CacheInterferenceConfig((0,), 1, 1, 1000),
                                         self.write_interferer(root, exit_after_ready=True), root)
            with self.assertRaisesRegex(RuntimeError, "unexpectedly"):
                with interferer:
                    time.sleep(0.25)
                    interferer.check_running()
            status = json.loads((root / "cache_interference.json").read_text())
            self.assertTrue(status["unexpected_exit"])
            self.assertEqual(status["exit_code"], 7)

    def test_cleanup_after_campaign_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            interferer = CacheInterferer(CacheInterferenceConfig((0,), 1, 1, 1000),
                                         self.write_interferer(root), root)
            with self.assertRaisesRegex(RuntimeError, "benchmark failed"):
                with interferer:
                    raise RuntimeError("benchmark failed")
            self.assertIsNotNone(interferer.process)
            self.assertIsNotNone(interferer.process.poll())

    def test_campaign_cli_leaves_interference_disabled_by_default(self):
        with patch.object(campaign, "read_definition", return_value={}), \
             patch.object(campaign, "prepare") as prepare, \
             patch.object(sys, "argv", ["campaign", "prepare", "--experiment", "x.toml"]):
            campaign.main()
        prepare.assert_called_once()
        self.assertFalse(prepare.call_args.kwargs["cache_interference"])

    def test_study_runner_forwards_interference_only_when_requested(self):
        from experiments.common.study_run import command
        base = command("collect", Path("example.toml"))
        stressed = command("collect", Path("example.toml"), cache_interference=True)
        self.assertNotIn("--cache-interference", base)
        self.assertEqual(stressed[-1], "--cache-interference")

    def test_ready_waits_and_rejects_wrong_or_missing_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = CacheInterferenceConfig((0,), 1, 1, 1000)
            before = time.monotonic()
            with CacheInterferer(config, self.write_interferer(root, delay=0.15), root):
                self.assertGreaterEqual(time.monotonic() - before, 0.15)
            for ready in (None, "READY threads=2 cpus=0,1", "READY threads=1 cpus=1"):
                obj = CacheInterferer(config, self.write_interferer(root, ready=ready), root)
                with self.subTest(ready=ready), patch(
                    "experiments.common.cache_interference.READY_TIMEOUT_SECONDS", 0.2
                ), self.assertRaisesRegex(RuntimeError, "READY"):
                    with obj:
                        self.fail("Benchmark must never start")
                self.assertIsNotNone(obj.process.poll())
                self.assertFalse(obj.status["successful"])

    def test_spawn_error_closes_logs_and_restores_handlers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            obj = CacheInterferer(CacheInterferenceConfig((0,), 1, 1, 1000),
                                  self.write_interferer(root), root)
            old = signal.getsignal(signal.SIGTERM)
            with patch("experiments.common.cache_interference.subprocess.Popen",
                       side_effect=OSError("spawn failed")), self.assertRaises(OSError):
                with obj:
                    self.fail("started")
            self.assertTrue(obj._stdout.closed)
            self.assertTrue(obj._stderr.closed)
            self.assertEqual(signal.getsignal(signal.SIGTERM), old)
            self.assertIn("spawn failed", obj.status["startup_error"])

    def test_shutdown_failure_is_not_success_and_logs_survive_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = CacheInterferenceConfig((0,), 1, 1, 1000)
            with CacheInterferer(config, self.write_interferer(root), root) as first:
                pass
            original = first.stdout_path.read_bytes()
            for options in ({"ignore_term": True}, {"stats": False}):
                obj = CacheInterferer(config, self.write_interferer(root, **options), root)
                with patch("experiments.common.cache_interference.STOP_TIMEOUT_SECONDS", 0.1), \
                     self.assertRaisesRegex(RuntimeError, "shutdown failed"):
                    with obj:
                        pass
                self.assertFalse(obj.status["successful"])
                self.assertIsNotNone(obj.process.poll())
            self.assertEqual(first.stdout_path.read_bytes(), original)
            self.assertEqual(len(list((root / "cache_interference").iterdir())), 3)

    def test_unexpected_exit_interrupts_active_benchmark(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            obj = CacheInterferer(CacheInterferenceConfig((0,), 1, 1, 1000),
                                  self.write_interferer(root, exit_after_ready=True), root)
            log = root / "benchmark.log"
            with self.assertRaisesRegex(RuntimeError, "unexpectedly"):
                with obj:
                    run_supervised([sys.executable, "-c",
                                    "import os,time; print(os.getpid(), flush=True); time.sleep(60)"],
                                   cwd=root, check_running=obj.check_running, log=log)
            pid = int(log.read_text().strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_cancellation_cleans_benchmark_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pidfile = root / "child.pid"
            code = textwrap.dedent(f'''
                import pathlib, signal, subprocess, sys, time
                child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
                def stop(*_):
                    child.wait(timeout=2)
                    sys.exit(0)
                signal.signal(signal.SIGTERM, stop)
                pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))
                time.sleep(60)
            ''')

            def monitor():
                if pidfile.exists():
                    raise RuntimeError("interferer failed")

            with self.assertRaisesRegex(RuntimeError, "interferer failed"):
                run_supervised([sys.executable, "-c", code], cwd=root, check_running=monitor)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)

    def test_resume_mode_config_and_binary_must_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = load_config(campaign.CACHE_INTERFERENCE_CONFIG)
            with self.assertRaisesRegex(ValueError, "mode differs"):
                verify_mode(root, config)
            (root / "cache_interference.toml").write_bytes(campaign.CACHE_INTERFERENCE_CONFIG.read_bytes())
            with self.assertRaisesRegex(ValueError, "mode differs"):
                verify_mode(root, None)
            with self.assertRaisesRegex(ValueError, "config differs"):
                verify_mode(root, CacheInterferenceConfig((0,), 1, 1, 1000))
            binary = self.write_interferer(root)
            (root / "cache_interference_identity.json").write_text(json.dumps({"binary_sha256": "bad"}))
            with self.assertRaisesRegex(ValueError, "binary differs"):
                verify_mode(root, config, binary)
            (root / "cache_interference_identity.json").write_text(json.dumps({
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}))
            (root / "cache_interference.json").write_text('{"successful": false}')
            with self.assertRaisesRegex(ValueError, "no successful"):
                verify_mode(root, config, binary, completed=True)

    def test_mode_mismatch_is_rejected_before_database_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "cache_interference.toml").write_bytes(campaign.CACHE_INTERFERENCE_CONFIG.read_bytes())
            with patch.object(campaign, "capture", return_value=""), \
                 patch.object(campaign, "RUST", Path(sys.executable)), \
                 patch.object(campaign, "load_and_verify_progress") as resume, \
                 self.assertRaisesRegex(ValueError, "mode differs"):
                campaign.collect({}, root / "experiment.toml", root)
            resume.assert_not_called()

    def test_sigint_and_sigterm_stop_benchmark_and_interferer(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                binary = self.write_interferer(root)
                # Exercise the outer study wrapper as well as the campaign.
                benchmark_code = (
                    "import os,pathlib,time; "
                    f"pathlib.Path({str(root / 'benchmark.pid')!r}).write_text(str(os.getpid())); "
                    "time.sleep(60)"
                )
                collector = textwrap.dedent(f'''
                    from pathlib import Path
                    from experiments.common.cache_interference import *
                    with CacheInterferer(CacheInterferenceConfig((0,), 1, 1, 1000),
                                         Path({str(binary)!r}), Path({str(root)!r})) as obj:
                        run_supervised([{sys.executable!r}, '-c', {benchmark_code!r}],
                                       cwd=Path({str(root)!r}), check_running=obj.check_running)
                ''')
                wrapper = textwrap.dedent(f'''
                    from pathlib import Path
                    from experiments.common.cache_interference import *
                    with campaign_signals():
                        run_supervised([{sys.executable!r}, '-c', {collector!r}],
                                       cwd=Path({str(campaign.ROOT)!r}), stop_timeout=15).check_returncode()
                ''')
                process = subprocess.Popen([sys.executable, "-c", wrapper], cwd=campaign.ROOT,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not (root / "benchmark.pid").exists() and time.monotonic() < deadline:
                        if process.poll() is not None:
                            self.fail(process.communicate())
                        time.sleep(.01)
                    self.assertTrue((root / "benchmark.pid").exists())
                    process.send_signal(signum)
                    _, stderr = process.communicate(timeout=10)
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn("interrupted", stderr)
                    status = json.loads((root / "cache_interference.json").read_text())
                    self.assertFalse(status["successful"])
                    self.assertEqual(status["exit_code"], 0)
                    for pid in (status["pid"], int((root / "benchmark.pid").read_text())):
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=20)

    def test_campaign_keeps_one_process_across_all_invocations(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config_file = root / "config.toml"
                config_file.write_text("[cache_interference]\ncpus=[0]\nworking_set_mib=1\ntouch_mib=1\nperiod_us=1000\n")
                benchmark = root / "benchmark"
                benchmark.write_text("#!/usr/bin/env python3\n" + textwrap.dedent('''
                    import json, os, pathlib, sys, time
                    folder = pathlib.Path(sys.argv[sys.argv.index('--database') + 1]).parent
                    for phase in ('warmup', 'perf-1', 'perf-2'):
                        status = folder / 'cache_interference.json'
                        if status.exists():
                            info = json.loads(status.read_text())
                            assert info['ready']
                            os.kill(info['pid'], 0)
                            print(phase, info['pid'], flush=True)
                        time.sleep(.01)
                    print(f'Stored benchmark: session=1 experiment={os.getpid()} database=x')
                '''))
                benchmark.chmod(0o755)
                data = campaign.read_definition(campaign.ROOT / "experiments/compute_heavy/experiment.toml")
                data["artifacts"]["output_root"] = str(root / "output")
                data["delivery"] = {"mode": "all-at-once"}
                data["repetitions"]["blocks"] = 2
                data["execution"]["settle_seconds"] = 0
                data["configurations"] = data["configurations"][:2]
                definition = root / "experiment.toml"
                definition.write_text("test definition")
                with patch.object(campaign, "capture", return_value=""), \
                     patch.object(campaign, "RUST", benchmark), \
                     patch.object(campaign, "CACHE_INTERFERER", self.write_interferer(root)), \
                     patch.object(campaign, "CACHE_INTERFERENCE_CONFIG", config_file), \
                     patch.object(campaign, "verify_campaign_database"):
                    out = campaign.collect(data, definition, cache_interference=enabled)
                with (out / "manifest.csv").open() as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 4)
                if enabled:
                    status = json.loads((out / "cache_interference.json").read_text())
                    self.assertTrue(status["successful"])
                    self.assertEqual(len(list((out / "cache_interference").iterdir())), 1)
                    self.assertEqual({r["cache_interference_attempt"] for r in rows}, {status["attempt"]})
                    for row in rows:
                        text = (out / row["log"]).read_text()
                        for phase in ("warmup", "perf-1", "perf-2"):
                            self.assertIn(f"{phase} {status['pid']}", text)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(status["pid"], 0)
                else:
                    self.assertFalse((out / "cache_interference").exists())
                    self.assertNotIn("cache_interference_attempt", rows[0])


if __name__ == "__main__":
    unittest.main()
