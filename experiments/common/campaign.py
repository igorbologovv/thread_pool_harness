"""TOML-defined randomized-block campaigns for all benchmark workloads."""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ID = re.compile(r"Stored benchmark: session=\d+ experiment=(\d+) database=")
RUST = ROOT / "target/release/threadance-harness"
DELIVERY_FLAGS = {
    "all-at-once", "fixed-arrivals", "steady-arrivals",
    "variable-arrivals", "bursty-arrivals",
}


def capture(command: list[str], *, check: bool = True) -> str:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{command!r}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def save_csv(path: Path, records: list[dict]) -> None:
    if not records:
        raise ValueError("Cannot save an empty execution plan")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    temporary.replace(path)


def read_definition(path: Path) -> dict:
    data = tomllib.loads(path.read_text())
    required = ("experiment", "workload", "configurations", "delivery", "repetitions", "randomization", "artifacts")
    for section in required:
        if section not in data:
            raise ValueError(f"Missing TOML section: {section}")
    if data["experiment"]["profile"] not in ("none", "standard", "deep"):
        raise ValueError("Unknown profiling mode")
    passes = data.get("perf", {}).get("passes", [])
    if passes:
        if data["experiment"]["profile"] != "deep":
            raise ValueError("Custom perf passes require profile=deep")
        if not isinstance(passes, list) or len(passes) > 8:
            raise ValueError("Invalid custom perf passes")
        for entry in passes:
            if not isinstance(entry, dict) or set(entry) != {"name", "events"}:
                raise ValueError("Custom perf pass requires name and events")
            if not isinstance(entry["name"], str) or not re.fullmatch(r"[A-Za-z0-9-]+", entry["name"]):
                raise ValueError("Invalid perf pass name")
            events = entry["events"]
            if not isinstance(events, list) or not events or len(events) > 16 or any(
                not isinstance(event, str) or
                not re.fullmatch(r"[A-Za-z0-9_.:-]+", event)
                for event in events
            ):
                raise ValueError("Invalid perf events")
        if len({entry["name"] for entry in passes}) != len(passes):
            raise ValueError("Duplicate perf pass names")
    if data["experiment"]["workload"] not in ("bls-aggregate-verify", "compute-heavy"):
        raise ValueError("Unknown workload")
    if data["delivery"]["mode"] not in DELIVERY_FLAGS:
        raise ValueError("Unknown delivery mode")
    if not data["configurations"]:
        raise ValueError("No configurations")
    ids = [row["id"] for row in data["configurations"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate configuration IDs")
    for key in ("blocks", "measured_runs_per_block"):
        if int(data["repetitions"][key]) < 1:
            raise ValueError(f"{key} must be positive")
    if int(data["repetitions"].get("warmup_runs_per_block", 0)) < 0:
        raise ValueError("Negative warm-up count")
    w = data["workload"]
    if int(w["workers"]) < 1 or int(w["work_units"]) < 1:
        raise ValueError("Invalid workers/work_units")
    if data["experiment"]["workload"] == "compute-heavy":
        if int(w["operations_per_work_unit"]) < 1:
            raise ValueError("operations_per_work_unit must be positive")
    else:
        for key in ("validators", "signers_per_certificate", "certificates_per_work_unit"):
            if int(w[key]) < 1:
                raise ValueError(f"{key} must be positive")
    d = data["delivery"]
    if d["mode"] != "all-at-once" and not (d.get("arrival_rates") or d.get("rho")):
        raise ValueError("Scheduled delivery requires arrival_rates or rho")
    if d.get("arrival_rates") and d.get("rho"):
        raise ValueError("Use arrival_rates OR rho, not both")
    if any(float(v) <= 0 for v in d.get("arrival_rates", [])):
        raise ValueError("Arrival rates must be positive")
    if any(not 0 < float(v) < 1 for v in d.get("rho", [])):
        raise ValueError("Target rho must be between 0 and 1")
    for config in data["configurations"]:
        if config["scheduler"] not in (
            "threadance", "threadance-hot-first", "threadance-hot-first-rtrb", "rayon", "bevy"
        ):
            raise ValueError(f"Unknown scheduler in {config['id']}")
        policy = config.get("wait_policy", "spin-then-park")
        if policy not in ("spin-then-park", "agave-backoff", "crossbeam-wait"):
            raise ValueError(f"Unknown wait policy in {config['id']}")
        if policy == "crossbeam-wait" and config["scheduler"] != "threadance":
            raise ValueError(f"Crossbeam wait only supports MPMC Threadance in {config['id']}")
        if policy != "spin-then-park" and config.get("spin_us", 0) not in (0, [0]):
            raise ValueError(f"spin_us is meaningful only for spin-then-park in {config['id']}")
        spins = config.get("spin_us", [0])
        spins = spins if isinstance(spins, list) else [spins]
        if not spins or any(int(v) < 0 for v in spins):
            raise ValueError("Invalid spin_us")
        if "hot-first" in config["scheduler"]:
            backlog = int(config.get("hot_first_backlog", 1))
            if backlog < 1 or backlog * int(w["workers"]) > int(config.get("queue_capacity", w.get("queue_capacity", 1024))):
                raise ValueError(f"Invalid Hot-first backlog/capacity for {config['id']}")
    return data


def calibration_service_us(data: dict, definition: Path, *, create: bool) -> float | None:
    if not data["delivery"].get("rho"):
        return None
    from experiments.common.calibrate_service_time import ensure_calibration
    if data["experiment"]["workload"] != "bls-aggregate-verify":
        calibration = data["delivery"].get("calibration_file")
        if not calibration:
            cache = ROOT / data["artifacts"].get("calibration_cache_dir", "results/calibrations")
            calibration = cache / (
                "compute-heavy-ops-" + str(data["workload"]["operations_per_work_unit"])
                + "-wu-" + str(data["workload"]["work_units"]) + ".json"
            )
        record = json.loads((ROOT / calibration).read_text())
        if record.get("workload") != "compute-heavy":
            raise ValueError("Calibration workload mismatch")
        expected = int(data["workload"]["operations_per_work_unit"])
        if int(record["operations_per_work_unit"]) != expected:
            raise ValueError("Calibration task size mismatch")
        w = data["workload"]
        if (int(record["work_units"]) != int(w["work_units"]) or
                int(record["workload_seed"]) != int(w.get("workload_seed", 1))):
            raise ValueError("Calibration workload parameters mismatch")
        if record.get("git_commit") != capture(["git", "rev-parse", "HEAD"]):
            raise ValueError("Calibration git revision mismatch; run prepare again")
        if not (float(record["mean_service_time_us"]) > 0):
            raise ValueError("Calibration has invalid service time")
        return float(record["mean_service_time_us"])
    if not create:
        calibration = data["delivery"].get("calibration_file")
        if calibration:
            record = json.loads((ROOT / calibration).read_text())
            if record.get("workload") != "bls-aggregate-verify":
                raise ValueError("Calibration workload mismatch")
            return float(record["service_time"]["mean_us"])
        from experiments.common.calibrate_service_time import find_compatible_calibration
        cached = find_compatible_calibration(
            ROOT,
            ROOT / data["artifacts"].get("calibration_cache_dir", "results/calibrations"),
            data["workload"],
        )
        if cached:
            return float(json.loads(cached.read_text())["service_time"]["mean_us"])
        raise ValueError("rho needs calibration: run prepare first or specify delivery.calibration_file")
    path = ensure_calibration(ROOT, definition, allow_dirty=False, settle_seconds=10.0)
    record = json.loads(Path(path).read_text())
    return float(record["service_time"]["mean_us"])


def make_plan(data: dict, service_us: float | None) -> list[dict]:
    w = data["workload"]
    d = data["delivery"]
    r = data["repetitions"]
    rates = [(None, None)] if d["mode"] == "all-at-once" else []
    rates.extend((None, float(rate)) for rate in d.get("arrival_rates", []))
    if d.get("rho"):
        if not service_us or service_us <= 0:
            raise ValueError("rho requires a valid single-thread service-time calibration")
        rates.extend((float(rho), float(rho) * int(w["workers"]) * 1e6 / service_us)
                     for rho in d["rho"])
    base = []
    for config, (rho, rate) in itertools.product(data["configurations"], rates):
        spins = config.get("spin_us", [0])
        spins = spins if isinstance(spins, list) else [spins]
        for spin in spins:
            base.append(dict(configuration=config["id"], scheduler=config["scheduler"],
                             wait_policy=config.get("wait_policy", "spin-then-park"),
                             spin_us=int(spin),
                             queue_capacity=int(config.get("queue_capacity", w.get("queue_capacity", 1024))),
                             hot_first_backlog=int(config.get("hot_first_backlog", 1)),
                             rho=rho if rho is not None else "",
                             arrival_rate=rate if rate is not None else ""))
    if not base:
        raise ValueError("Empty condition grid")
    records = []
    for block in range(1, int(r["blocks"]) + 1):
        shuffled = list(base)
        random.Random(int(data["randomization"]["seed"]) + block).shuffle(shuffled)
        arrival_seed = int(d.get("arrival_seed_base", 42)) + block - 1
        for order, condition in enumerate(shuffled, 1):
            records.append(dict(block=block, order_in_block=order,
                                global_order=len(records) + 1,
                                arrival_seed=arrival_seed, **condition))
    return records


def command(data: dict, row: dict, database: Path, campaign: str) -> list[str]:
    w, r, d = data["workload"], data["repetitions"], data["delivery"]
    args = [str(RUST),
            "--workload", data["experiment"]["workload"],
            "--scheduler", row["scheduler"],
            "--workers", str(w["workers"]),
            "--work-units", str(w["work_units"]),
            "--queue-capacity", str(row["queue_capacity"]),
            "--hot-first-backlog", str(row["hot_first_backlog"]),
            "--threadance-wait-policy", row["wait_policy"],
            "--threadance-spin-us", str(row["spin_us"]),
            "--seed", str(w.get("workload_seed", 1)),
            "--warmup", str(r.get("warmup_runs_per_block", 0)),
            "--runs", str(r["measured_runs_per_block"]),
            "--profile", data["experiment"]["profile"],
            "--campaign-id", campaign,
            "--database", str(database.resolve())]
    if data.get("perf", {}).get("passes"):
        args += ["--perf-passes-json", json.dumps(data["perf"]["passes"], separators=(",", ":"))]
        if int(row["block"]) % 2 == 0:
            args.append("--reverse-perf-passes")
    if data["experiment"]["workload"] == "compute-heavy":
        args += ["--operations-per-work-unit", str(w["operations_per_work_unit"])]
    else:
        for key in ("validators", "signers_per_certificate", "certificates_per_work_unit"):
            args += ["--" + key.replace("_", "-"), str(w[key])]
        args += ["--dataset-cache-dir",
                 str((ROOT / data["artifacts"].get("dataset_cache_dir", "results/datasets")).resolve())]
    args += ["--" + d["mode"]]
    if d["mode"] != "all-at-once":
        args += ["--arrival-rate", str(row["arrival_rate"]), "--arrival-seed", str(row["arrival_seed"])]
    return args


def prepare(data: dict, definition: Path) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run prepare as a normal user, not with sudo")
    os.environ["RUSTFLAGS"] = "-C target-cpu=native"
    subprocess.run(["cargo", "build", "--release", "-p", "threadance-harness", "--bin", "threadance-harness"],
                   cwd=ROOT, check=True)
    w = data["workload"]
    if data["experiment"]["workload"] == "bls-aggregate-verify":
        prep = dict(block=0, order_in_block=0, global_order=0, configuration="prepare",
                    scheduler="rayon", wait_policy="spin-then-park", spin_us=0,
                    queue_capacity=int(w.get("queue_capacity", 1024)), hot_first_backlog=1,
                    rho="", arrival_rate="", arrival_seed=1)
        setup = json.loads(json.dumps(data))
        setup["delivery"]["mode"] = "all-at-once"
        setup["repetitions"]["warmup_runs_per_block"] = 0
        setup["repetitions"]["measured_runs_per_block"] = 1
        output = ROOT / data["artifacts"]["output_root"] / "preparation.sqlite3"
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(command(setup, prep, output, "preparation"), cwd=ROOT, check=True)
    if data["delivery"].get("rho"):
        if data["experiment"]["workload"] == "compute-heavy":
            cache = ROOT / data["artifacts"].get("calibration_cache_dir", "results/calibrations")
            cache.mkdir(parents=True, exist_ok=True)
            file = cache / ("compute-heavy-ops-" + str(w["operations_per_work_unit"])
                            + "-wu-" + str(w["work_units"]) + ".json")
            subprocess.run(["cargo", "build", "--release", "-p", "threadance-harness",
                            "--bin", "service_time"], cwd=ROOT, check=True)
            raw = cache / (file.stem + ".csv")
            output = capture([str(ROOT / "target/release/service_time"),
                              "--workload", "compute-heavy",
                              "--work-units", str(w["work_units"]),
                              "--operations-per-work-unit", str(w["operations_per_work_unit"]),
                              "--seed", str(w.get("workload_seed", 1)),
                              "--warmup", "5", "--runs", "30",
                              "--samples-csv", str(raw)])
            samples = list(csv.DictReader(raw.open()))
            mean_us = sum(float(x["service_time_ns"]) for x in samples) / len(samples) / 1000
            file.write_text(json.dumps(dict(workload="compute-heavy",
                                operations_per_work_unit=w["operations_per_work_unit"],
                                work_units=w["work_units"], workload_seed=w.get("workload_seed", 1),
                                mean_service_time_us=mean_us,
                                git_commit=capture(["git", "rev-parse", "HEAD"])),
                                indent=2) + "\n")
            print(output)
        else:
            calibration_service_us(data, definition, create=True)
    print("Preparation complete")


def load_and_verify_progress(folder: Path, definition: Path, plan: list[dict],
                             current_commit: str, binary_hash: str, profile: str,
                             work_units: int, runs: int) -> tuple[str, list[dict]]:
    """Validate the recorded prefix, then discard an orphaned interrupted invocation.

    A completed experiment is committed by Rust before its manifest row is
    atomically written. If interrupted between these steps, its unmanifested
    session is discarded and that condition is executed again.
    """
    if not folder.is_dir():
        raise ValueError(f"Campaign directory does not exist: {folder}")
    if (folder / "experiment.toml").read_bytes() != definition.read_bytes():
        raise ValueError("Archived experiment.toml differs from requested definition")
    meta = json.loads((folder / "environment.json").read_text())
    if meta["git_commit"] != current_commit or meta["binary_sha256"] != binary_hash:
        raise ValueError("Code revision or release binary differs from the original campaign")
    campaign = meta["campaign_id"]
    with (folder / "plan.csv").open(newline="") as handle:
        saved_plan = list(csv.DictReader(handle))
    normalized_plan = [{k: "" if v is None else str(v) for k, v in row.items()}
                       for row in plan]
    if saved_plan != normalized_plan:
        raise ValueError("Campaign plan no longer matches the original conditions")

    manifest_file = folder / "manifest.csv"
    if manifest_file.exists():
        with manifest_file.open(newline="") as handle:
            manifest = list(csv.DictReader(handle))
    else:
        manifest = []
    if len(manifest) > len(plan):
        raise ValueError("Manifest exceeds the planned number of conditions")
    ids = set()
    for index, row in enumerate(manifest):
        if any(row.get(k) != v for k, v in normalized_plan[index].items()):
            raise ValueError(f"Manifest diverges from randomized plan at row {index + 1}")
        eid = int(row["experiment_id"])
        if eid <= 0 or eid in ids:
            raise ValueError("Duplicate or invalid manifest experiment ID")
        ids.add(eid)

    db_path = folder / "benchmarks.sqlite3"
    if not db_path.exists():
        if manifest:
            raise ValueError("Campaign database missing despite manifest entries")
        return campaign, manifest

    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Campaign SQLite foreign-key integrity failed")
        recorded = db.execute(
            "SELECT e.id, e.work_units, e.profile_mode, s.id "
            "FROM experiment e JOIN benchmark_session s ON s.id=e.session_id "
            "WHERE s.campaign_id=?", (campaign,)).fetchall()
        all_ids = {row[0] for row in recorded}
        if not ids.issubset(all_ids):
            raise ValueError("Manifest references absent campaign experiments")
        stored_definition = read_definition(definition)
        expected_passes = (len(stored_definition.get("perf", {}).get("passes") or [])
                           or (4 if profile == "deep" else 1))
        for eid, unit_count, stored_profile, _ in recorded:
            if eid not in ids:
                continue
            if unit_count != work_units or stored_profile != profile:
                raise ValueError(f"Experiment {eid} has unexpected workload/profile")
            count, bad = db.execute(
                "SELECT COUNT(*), SUM(CASE WHEN success!=1 OR "
                "completed_work_units!=? THEN 1 ELSE 0 END) "
                "FROM run WHERE experiment_id=?", (work_units, eid)).fetchone()
            if count != runs * expected_passes or bad:
                raise ValueError(f"Experiment {eid} has incomplete/failed measured runs")
            if profile == "standard":
                present = db.execute(
                    "SELECT COUNT(*) FROM run r WHERE r.experiment_id=? "
                    "AND EXISTS (SELECT 1 FROM perf_metric p WHERE p.run_id=r.id "
                    "AND p.event_name IN ('task-clock', 'task-clock:u') "
                    "AND p.counter_value IS NOT NULL)",
                    (eid,),
                ).fetchone()[0]
                if present != count:
                    raise ValueError(f"Experiment {eid} is missing perf task-clock")
        # Orphaned invocations are safe to remove only within this exact campaign.
        orphan_sessions = [s for eid, _, _, s in recorded if eid not in ids]
        extra_sessions = db.execute(
            "SELECT s.id FROM benchmark_session s "
            "WHERE s.campaign_id=? AND NOT EXISTS "
            "(SELECT 1 FROM experiment e WHERE e.session_id=s.id)",
            (campaign,)).fetchall()
        orphan_sessions += [s for (s,) in extra_sessions]
        for session_id in set(orphan_sessions):
            db.execute("DELETE FROM benchmark_session WHERE id=?", (session_id,))
    return campaign, manifest



def verify_campaign_database(folder: Path, data: dict, expected_invocations: int) -> None:
    """Audit a completed campaign without rerunning or altering measurements.

    Print precise failure conditions rather than discarding an otherwise
    complete SQLite database behind a generic integrity-check error.
    """
    database = folder / "benchmarks.sqlite3"
    if not database.is_file():
        raise RuntimeError(f"Campaign database missing: {database}")
    repeats = int(data["repetitions"]["measured_runs_per_block"])
    passes = (len(data.get("perf", {}).get("passes") or [])
              or (4 if data["experiment"]["profile"] == "deep" else 1))
    expected_runs = expected_invocations * repeats * passes
    with sqlite3.connect(database) as connection:
        errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        n_experiments = connection.execute(
            "SELECT COUNT(*) FROM experiment"
        ).fetchone()[0]
        n_runs = connection.execute("SELECT COUNT(*) FROM run").fetchone()[0]
        invalid = connection.execute(
            "SELECT COUNT(*) FROM run WHERE success<>1 OR completed_work_units<>?",
            (int(data["workload"]["work_units"]),)
        ).fetchone()[0]
        missing = 0
        examples = []
        event_names = []
        if data["experiment"]["profile"] == "standard":
            missing = connection.execute(
                "SELECT COUNT(*) FROM run r WHERE NOT EXISTS "
                "(SELECT 1 FROM perf_metric p WHERE p.run_id=r.id "
                "AND p.event_name IN ('task-clock', 'task-clock:u') "
                "AND p.counter_value IS NOT NULL)"
            ).fetchone()[0]
            if missing:
                examples = connection.execute(
                    "SELECT r.id, e.scheduler, COALESCE("
                    "(SELECT GROUP_CONCAT(p.event_name || '=' || p.status) "
                    "FROM perf_metric p WHERE p.run_id=r.id "
                    "AND p.event_name IN ('task-clock', 'task-clock:u')), 'absent') "
                    "FROM run r JOIN experiment e ON e.id=r.experiment_id "
                    "WHERE NOT EXISTS (SELECT 1 FROM perf_metric p "
                    "WHERE p.run_id=r.id "
                    "AND p.event_name IN ('task-clock', 'task-clock:u') "
                    "AND p.counter_value IS NOT NULL) "
                    "ORDER BY r.id LIMIT 5"
                ).fetchall()
                event_names = connection.execute(
                    "SELECT event_name, COUNT(*) FROM perf_metric "
                    "GROUP BY event_name ORDER BY COUNT(*) DESC LIMIT 12"
                ).fetchall()
    problems = []
    if errors:
        problems.append(f"foreign-key violations={len(errors)}, examples={errors[:3]}")
    if n_experiments != expected_invocations:
        problems.append(
            f"experiment rows={n_experiments}, expected={expected_invocations}"
        )
    if n_runs != expected_runs:
        problems.append(f"measured run rows={n_runs}, expected={expected_runs}")
    if invalid:
        problems.append(f"failed/incomplete measured runs={invalid}")
    if missing:
        problems.append(
            f"missing/unavailable perf task-clock counters={missing}; "
            f"examples (run_id, scheduler, status)={examples}; "
            f"recorded event names={event_names}"
        )
    if problems:
        raise RuntimeError(
            f"Campaign database integrity failed: {database}\n"
            + "\n".join(f"  - {item}" for item in problems)
            + "\nMeasurements have been preserved; do not delete the database."
        )

def collect(data: dict, definition: Path, campaign_dir: Path | None = None) -> Path:
    os.chdir(ROOT)
    if capture(["git", "status", "--porcelain", "--untracked-files=no"]):
        raise RuntimeError("Tracked tree is dirty; commit before collecting data")
    if not RUST.is_file():
        raise RuntimeError("Release harness not built; run prepare")
    service_us = calibration_service_us(data, definition, create=False)
    plan = make_plan(data, service_us)
    if campaign_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        campaign = f"{data['experiment']['name']}-{stamp}"
        out = ROOT / data["artifacts"]["output_root"] / campaign
        out.mkdir(parents=True, exist_ok=False)
        (out / "logs").mkdir()
        shutil.copy2(definition, out / "experiment.toml")
        save_csv(out / "plan.csv", plan)
        if data["experiment"]["workload"] == "bls-aggregate-verify":
            from dataclasses import asdict
            from experiments.common.bls_dataset_identity import (
                config_from_experiment, expected_dataset_path, identify_dataset,
            )
            cache, cfg = config_from_experiment(definition)
            identity = identify_dataset(expected_dataset_path(ROOT / cache, cfg), cfg)
            (out / "dataset_identity.json").write_text(
                json.dumps(asdict(identity), indent=2, sort_keys=True) + "\n"
            )
        if data["delivery"].get("rho"):
            if data["experiment"]["workload"] == "compute-heavy":
                calibration = ROOT / data["artifacts"].get("calibration_cache_dir", "results/calibrations") / (
                    "compute-heavy-ops-" + str(data["workload"]["operations_per_work_unit"])
                    + "-wu-" + str(data["workload"]["work_units"]) + ".json"
                )
            else:
                from experiments.common.calibrate_service_time import find_compatible_calibration
                calibration = find_compatible_calibration(
                    ROOT, ROOT / data["artifacts"].get("calibration_cache_dir", "results/calibrations"),
                    data["workload"],
                )
            if calibration:
                shutil.copy2(calibration, out / "calibration.json")
        meta = dict(campaign_id=campaign, timestamp=datetime.now().astimezone().isoformat(),
                    git_commit=capture(["git", "rev-parse", "HEAD"]),
                    git_branch=capture(["git", "branch", "--show-current"]),
                    threadance_commit=capture(["git", "-C", str(ROOT.parent / "threadance"),
                                               "rev-parse", "HEAD"], check=False),
                    kernel=capture(["uname", "-r"]), cpu=capture(["lscpu"], check=False),
                    perf=capture(["perf", "--version"], check=False),
                    binary_sha256=hashlib.sha256(RUST.read_bytes()).hexdigest(),
                    service_time_us=service_us)
        (out / "environment.json").write_text(json.dumps(meta, indent=2) + "\n")
        db = out / "benchmarks.sqlite3"
        manifest = []
    else:
        out = campaign_dir.resolve()
        campaign, manifest = load_and_verify_progress(
            out, definition, plan, capture(["git", "rev-parse", "HEAD"]),
            hashlib.sha256(RUST.read_bytes()).hexdigest(),
            data["experiment"]["profile"], int(data["workload"]["work_units"]),
            int(data["repetitions"]["measured_runs_per_block"]),
        )
    db = out / "benchmarks.sqlite3"
    settling = float(data.get("execution", {}).get("settle_seconds", 3))
    try:
        for row in plan[len(manifest):]:
            if settling > 0:
                time.sleep(settling)
            print(f"Block {row['block']}, condition {row['configuration']}, "
                  f"spin={row['spin_us']}, rate={row['arrival_rate']}", flush=True)
            log = out / "logs" / f"block-{row['block']:02d}-order-{row['order_in_block']:03d}.log"
            result = subprocess.run(command(data, row, db, campaign), cwd=ROOT,
                                    capture_output=True, text=True)
            log.write_text(result.stdout + result.stderr)
            match = BENCHMARK_ID.search(result.stdout)
            if result.returncode or not match:
                raise RuntimeError(f"Benchmark failed; see {log}")
            manifest.append(dict(**row, experiment_id=int(match.group(1)),
                                 log=str(log.relative_to(out))))
            save_csv(out / "manifest.csv", manifest)
    except BaseException:
        print(f"Partial campaign retained: {out}", file=sys.stderr)
        raise
    verify_campaign_database(out, data, len(plan))
    print(f"Complete: {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "collect", "resume", "analyze"))
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--campaign", type=Path)
    args = parser.parse_args()
    definition = args.experiment.resolve()
    data = read_definition(definition)
    if args.action == "prepare":
        prepare(data, definition)
    elif args.action == "plan":
        service = calibration_service_us(data, definition, create=False)
        for row in make_plan(data, service):
            print(json.dumps(row))
    elif args.action in ("collect", "resume"):
        if args.action == "resume" and args.campaign is None:
            parser.error("resume requires --campaign")
        collect(data, definition, args.campaign if args.action == "resume" else None)
    else:
        if args.campaign is None:
            parser.error("analyze requires --campaign")
        from experiments.common.analysis import analyze
        analyze(args.campaign.resolve())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
