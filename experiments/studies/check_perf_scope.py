"""Fail fast if perf reports user-only scheduler software counters."""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> None:
    with open("/proc/sys/kernel/perf_event_paranoid") as stream:
        paranoid = int(stream.read().strip())
    if paranoid > 1:
        raise SystemExit(
            f"perf_event_paranoid={paranoid}: set to 1 or less before acquiring data"
        )

    # Force real voluntary scheduler context switches in the observed process.
    code = "import time\nfor _ in range(50): time.sleep(0.001)\n"
    cmd = ["perf", "stat", "-j", "-e", "context-switches,cpu-migrations",
           "--", sys.executable, "-c", code]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"perf failed (exit={proc.returncode}): {proc.stderr}")
    metrics = []
    for line in proc.stderr.splitlines():
        try:
            record = json.loads(line.strip().rstrip(","))
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and "event" in record:
            metrics.append(record)
    events = {row["event"]: row for row in metrics}
    for event in ("context-switches", "cpu-migrations"):
        matches = [name for name in events if name.split(":", 1)[0] == event]
        if len(matches) != 1:
            raise SystemExit(f"Missing/ambiguous perf event {event}: {list(events)}")
        name = matches[0]
        if ":" in name:
            raise SystemExit(f"Perf reported event {name}, not unrestricted {event}")
        raw = str(events[name].get("counter-value", ""))
        try:
            count = float(raw.replace(",", ""))
        except ValueError as exc:
            raise SystemExit(f"Unusable {event} count: {raw}") from exc
        if event == "context-switches" and count <= 0:
            raise SystemExit(f"Expected nonzero context-switches, got {count}")
        print(f"{name}: {count:g}")
    print(f"Perf kernel scheduler event preflight OK (perf_event_paranoid={paranoid})")


if __name__ == "__main__":
    main()
