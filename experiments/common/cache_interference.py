"""Supervise the optional CPU-pinned cache/memory interference process.

This process is deliberately outside the benchmark process tree, so perf's
per-process task-clock accounting for a benchmark does not include it.  It is
not an L2-only eviction mechanism: its effects include CPU, scheduling, cache
and memory-system contention.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
import tomllib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


READY_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.05
STOP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class CacheInterferenceConfig:
    cpus: tuple[int, ...]
    working_set_mib: int
    touch_mib: int
    period_us: int

    def command(self, binary: Path) -> list[str]:
        return [
            str(binary), "--cpus", ",".join(str(cpu) for cpu in self.cpus),
            "--working-set-mib", str(self.working_set_mib),
            "--touch-mib", str(self.touch_mib),
            "--period-us", str(self.period_us),
            "--seconds", "0",
        ]


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"cache_interference.{name} must be a positive integer")
    return value


def load_config(path: Path, *, source: str | None = None) -> CacheInterferenceConfig:
    """Parse the one campaign-level cache interference configuration."""
    try:
        raw = tomllib.loads(path.read_text() if source is None else source)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Cannot read cache interference config {path}: {exc}") from exc
    section = raw.get("cache_interference")
    if not isinstance(section, dict):
        raise ValueError("Missing [cache_interference] TOML section")
    required = {"cpus", "working_set_mib", "touch_mib", "period_us"}
    if set(section) != required:
        raise ValueError("cache_interference config must contain exactly: "
                         + ", ".join(sorted(required)))
    cpus = section["cpus"]
    if not isinstance(cpus, list) or not cpus:
        raise ValueError("cache_interference.cpus must be a non-empty integer list")
    if any(type(cpu) is not int or cpu < 0 or cpu >= 1024 for cpu in cpus):
        raise ValueError("cache_interference.cpus must contain CPU IDs from 0 to 1023")
    if len(set(cpus)) != len(cpus):
        raise ValueError("cache_interference.cpus contains a duplicate CPU")
    working_set_mib = _positive_int(section["working_set_mib"], "working_set_mib")
    touch_mib = _positive_int(section["touch_mib"], "touch_mib")
    period_us = _positive_int(section["period_us"], "period_us")
    if period_us > 2**64 - 1:
        raise ValueError("cache_interference.period_us exceeds Rust u64")
    # The Rust worker wraps its cache-line cursor with a bit mask.
    if (working_set_mib & (working_set_mib - 1)) != 0 or working_set_mib > 1024:
        raise ValueError("cache_interference.working_set_mib must be a power of two <= 1024")
    if touch_mib > working_set_mib:
        raise ValueError("cache_interference.touch_mib must not exceed working_set_mib")
    return CacheInterferenceConfig(tuple(cpus), working_set_mib, touch_mib, period_us)


def verify_mode(folder: Path, config: CacheInterferenceConfig | None,
                binary: Path | None = None, *, completed: bool = False) -> None:
    """Reject condition changes BEFORE resume can alter the database."""
    archive = folder / "cache_interference.toml"
    if archive.exists() != (config is not None):
        raise ValueError("Cache interference mode differs from the original campaign; "
                         "use a separate output root for clean and interfered campaigns")
    if config is None:
        return
    if load_config(archive) != config:
        raise ValueError("Archived cache interference config differs from requested configuration")
    identity = json.loads((folder / "cache_interference_identity.json").read_text())
    if binary is not None and identity["binary_sha256"] != hashlib.sha256(binary.read_bytes()).hexdigest():
        raise ValueError("Cache interferer binary differs from the original campaign")
    if completed:
        status = json.loads((folder / "cache_interference.json").read_text())
        if not status.get("successful"):
            raise ValueError("Completed manifest has no successful interference shutdown; "
                             "measurements retained for inspection")


@contextmanager
def campaign_signals():
    """Raise on the first interruption; further signals cannot interrupt cleanup."""
    handlers = {}
    interrupted = False

    def handler(signum, _frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise RuntimeError(f"Campaign interrupted by {signal.Signals(signum).name}")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, handler)
        yield
    finally:
        for signum, old in handlers.items():
            signal.signal(signum, old)


def terminate_group(process: subprocess.Popen, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
    """Reap the harness and terminate its perf descendants on cancellation."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_supervised(argv: list[str], *, cwd: Path, check_running=lambda: None,
                   log: Path | None = None,
                   stop_timeout: float = STOP_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Poll interference throughout a benchmark, including every warmup/perf pass.

    File-backed output also retains partial diagnostics on interruption and
    avoids pipe deadlocks. The benchmark and interferer are sibling processes.
    """
    handle = log.open("w+") if log is not None else None
    process = None
    try:
        check_running()
        process = subprocess.Popen(argv, cwd=cwd, stdout=handle,
                                   stderr=subprocess.STDOUT if handle else None,
                                   start_new_session=True)
        while process.poll() is None:
            check_running()
            time.sleep(POLL_SECONDS)
        check_running()
        if process.returncode:
            terminate_group(process, stop_timeout)
        output = ""
        if handle:
            handle.seek(0)
            output = handle.read()
        return subprocess.CompletedProcess(argv, process.returncode, output, "")
    except BaseException:
        if process is not None:
            terminate_group(process, stop_timeout)
        raise
    finally:
        if handle:
            handle.close()


class CacheInterferer:
    """One background process, held for the complete collection invocation."""

    def __init__(self, config: CacheInterferenceConfig, binary: Path, output: Path):
        self.config = config
        self.binary = binary
        self.output = output
        # Preserve every collection/resume attempt and its original logs.
        self.attempt = output / "cache_interference" / uuid4().hex
        self.attempt.mkdir(parents=True)
        self.stdout_path = self.attempt / "stdout.log"
        self.stderr_path = self.attempt / "stderr.log"
        self.status_path = self.attempt / "status.json"
        self.process: subprocess.Popen[bytes] | None = None
        self._stdout: Any = None
        self._stderr: Any = None
        self._started_at: float | None = None
        self._started_monotonic: float | None = None
        self._stopped = False
        self.status: dict[str, Any] = {
            "enabled": True,
            "configuration": asdict(config),
            "command": config.command(binary),
            "ready": False,
            "successful": False,
            "attempt": str(self.attempt.relative_to(output)),
            "ready_timeout_seconds": READY_TIMEOUT_SECONDS,
            "timing_note": (
                "The generator causes CPU execution, scheduling, cache and memory-system "
                "contention; it is not an exclusive measure of L2 cache eviction."
            ),
        }

    def _write_status(self) -> None:
        for path in (self.status_path, self.output / "cache_interference.json"):
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.status, indent=2, sort_keys=True) + "\n")
            temporary.replace(path)

    def __enter__(self) -> "CacheInterferer":
        self._signals = campaign_signals()
        self._signals.__enter__()
        try:
            self.start()
        except BaseException as exc:
            self.status["startup_error"] = str(exc)
            try:
                self.stop()
            finally:
                self._signals.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        failure = _value
        try:
            if failure is None:
                try:
                    self.check_running()
                except BaseException as exc:
                    failure = exc
            if failure is not None:
                self.status["campaign_error"] = str(failure)
            self.stop()
            if failure is None:
                if self.status.get("exit_code") != 0 or not self.status.get("timing_statistics"):
                    failure = RuntimeError("Cache interferer shutdown failed or timing statistics missing")
                elif self.status.get("unexpected_exit"):
                    failure = RuntimeError("Cache interferer exited unexpectedly at shutdown")
            self.status["successful"] = failure is None
            self._write_status()
            if failure is not None and _value is None:
                raise failure
            stats = self.status.get("timing_statistics")
            if stats:
                threads = stats["threads"]
                print(f"Cache interference: {sum(t['iterations'] for t in threads)} sweeps, "
                      f"{sum(t['overruns'] for t in threads)} work overruns, "
                      f"{sum(t['deadline_misses'] for t in threads)} deadline misses, "
                      f"{sum(t['skipped_intervals'] for t in threads)} skipped intervals; "
                      f"all sweeps within {self.config.period_us} us: "
                      f"{all(t['overruns'] == 0 for t in threads)}", flush=True)
        finally:
            self._signals.__exit__(None, None, None)

    def start(self) -> None:
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            error = (
                f"Cache interferer is not built: {self.binary}. "
                "Run campaign prepare with --cache-interference first."
            )
            self.status["startup_error"] = error
            self._write_status()
            raise RuntimeError(error)
        self._stdout = self.stdout_path.open("wb")
        self._stderr = self.stderr_path.open("wb")
        self._started_at = time.time()
        self._started_monotonic = time.monotonic()
        self.status["started_at"] = self._started_at
        self.status["pid"] = None
        self._write_status()
        self.process = subprocess.Popen(
            self.config.command(self.binary), cwd=self.output,
            stdout=self._stdout, stderr=self._stderr, start_new_session=True,
        )
        self.status["pid"] = self.process.pid
        self._write_status()
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                self.status["startup_error"] = f"interferer exited before READY (exit {code})"
                self.status["exit_code"] = code
                self._write_status()
                raise RuntimeError(self.status["startup_error"])
            try:
                output = self.stdout_path.read_text(errors="replace")
            except FileNotFoundError:
                output = ""
            ready_lines = [line for line in output.splitlines() if line.startswith("READY ")]
            expected = (f"READY threads={len(self.config.cpus)} cpus="
                        + ",".join(map(str, sorted(self.config.cpus))))
            if ready_lines and ready_lines != [expected]:
                raise RuntimeError(f"Invalid interferer READY: {ready_lines!r}; expected {expected!r}")
            if ready_lines:
                self.check_running()
                self.status["ready"] = True
                self.status["ready_message"] = expected
                self.status["ready_at"] = time.time()
                self.status["ready_wait_seconds"] = time.monotonic() - self._started_monotonic
                self._write_status()
                return
            time.sleep(0.02)
        self.status["startup_error"] = "timed out waiting for interferer READY"
        self._write_status()
        raise RuntimeError(self.status["startup_error"])

    def check_running(self) -> None:
        if self.process is None:
            raise RuntimeError("Cache interferer was not started")
        code = self.process.poll()
        if code is not None:
            self.status["unexpected_exit"] = True
            self.status["exit_code"] = code
            self._write_status()
            raise RuntimeError(f"Cache interferer exited unexpectedly (exit {code}); "
                               f"see {self.stdout_path} and {self.stderr_path}")

    def settle(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_running()
            time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
        self.check_running()

    def _read_timing_statistics(self) -> None:
        try:
            for line in self.stdout_path.read_text(errors="replace").splitlines():
                if line.startswith("CACHE_INTERFERER_STATS "):
                    stats = json.loads(
                        line.removeprefix("CACHE_INTERFERER_STATS ")
                    )
                    threads = stats["threads"]
                    if (stats["period_us"] != self.config.period_us
                            or sorted(t["cpu"] for t in threads) != sorted(self.config.cpus)
                            or any(t["iterations"] <= 0 for t in threads)):
                        raise ValueError("Incomplete or mismatched interference timing statistics")
                    for thread in threads:
                        counts = [thread[key] for key in (
                            "iterations", "overruns", "deadline_misses", "skipped_intervals",
                            "completed_within_period",
                        )]
                        if (any(type(v) is not int or v < 0 for v in counts)
                                or thread["completed_within_period"] + thread["overruns"] != thread["iterations"]
                                or thread["deadline_misses"] > thread["iterations"]):
                            raise ValueError("Invalid interference iteration counts")
                    self.status["timing_statistics"] = stats
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.pop("timing_statistics", None)
            self.status["timing_statistics_error"] = str(exc)

    def stop(self) -> None:
        if self._stopped:
            return
        # Block repeated cancellation during bounded shutdown, including when
        # the first signal arrives while normal completion is already stopping.
        handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            if self.process is not None:
                code = self.process.poll()
                if code is None:
                    self.status["stop_requested_at"] = time.time()
                    self.process.terminate()
                    try:
                        code = self.process.wait(timeout=STOP_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        code = self.process.wait()
                        self.status["forced_kill"] = True
                else:
                    self.status["unexpected_exit"] = True
                self.status["exit_code"] = code
        finally:
            for handle in (self._stdout, self._stderr):
                if handle is not None and not handle.closed:
                    handle.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        self._stopped = True
        self.status["stopped_at"] = time.time()
        if self._started_monotonic is not None:
            self.status["runtime_seconds"] = time.monotonic() - self._started_monotonic
        self._read_timing_statistics()
        self._write_status()
