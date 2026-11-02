"""Repeat archived 48 BLS paced campaigns with Bevy, four warmups and two PMU passes.

Archive-derived plan preserves *exact* scheduled arrival rates, work-unit
counts and workload seeds. No old campaign is rewritten. 2 measured runs
per pass; 2 passes; 4 warmups once per harness invocation. Pass order is
reversed for even-numbered randomized blocks by the existing runner.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition, verify_campaign_database
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign

STUDY = "bls-normalized-deep-final"
ARCHIVE = ROOT / "results/studies/bls-normalized-load/campaigns"
DESTINATION = ROOT / "results/studies" / STUDY / "campaigns"
GENERATED = ROOT / "experiments/generated" / STUDY
WORKERS = (2, 4, 8, 16)
MODES = ("fixed-arrivals", "bursty-arrivals")
ORIGINAL_CONFIGS = {
    "rayon", "mpmc-sp0", "mpmc-ab", "rtrb-sp0-b1", "rtrb-sp0-b2",
}
FINAL_CONFIGS = ORIGINAL_CONFIGS | {"bevy"}
PERF_SPEC = ROOT / "experiments/studies/bls_advanced_perf.toml"
BLOCKS = 20
WARMUPS = 4
REPETITIONS_PER_PASS = 2
PASS_COUNT = 2


def require(condition: bool, error: str) -> None:
    if not condition:
        raise ValueError(error)


def archived_campaigns(archive: Path = ARCHIVE) -> list[tuple[Path, dict]]:
    """Inspect all saved original definitions, not provisional generated estimates."""
    folders = sorted(p.parent for p in archive.glob("bls-normalized-*/experiment.toml"))
    require(len(folders) == 48, f"Expected 48 archived paced campaigns; found {len(folders)}")
    selected = []
    identities = set()
    for folder in folders:
        source = folder / "experiment.toml"
        data = read_definition(source)
        w, d = data["workload"], data["delivery"]
        name = data["experiment"]["name"]
        match = re.fullmatch(
            r"bls-normalized-(w(?:2|4|8|16)-load\d{5}-(?:fixed|bursty)-arrivals)",
            name,
        )
        require(bool(match), f"Unexpected archived experiment name: {name}")
        require(folder.name.startswith(name + "-"),
                f"Experiment folder/name mismatch: {folder}")
        workers, mode = int(w["workers"]), d["mode"]
        require(workers in WORKERS and mode in MODES,
                f"Unexpected workers/mode at {folder}")
        require(data["experiment"]["workload"] == "bls-aggregate-verify"
                and data["experiment"]["profile"] == "standard",
                f"Archived profile/workload mismatch: {folder}")
        require({cfg["id"] for cfg in data["configurations"]} == ORIGINAL_CONFIGS,
                f"Unexpected archived scheduler set: {folder}")
        require(tuple(w[k] for k in (
            "dataset_work_units", "validators", "signers_per_certificate",
            "certificates_per_work_unit", "workload_seed",
        )) == (2500, 2000, 1600, 1, 1), f"Archived BLS dataset differs: {folder}")
        require(int(data["repetitions"]["blocks"]) == 20,
                f"Archived randomized blocks differ: {folder}")
        require(len(d.get("arrival_rates", [])) == 1 and not d.get("rho"),
                f"Expected exactly one original arrival rate: {folder}")
        rate = float(d["arrival_rates"][0])
        require(rate > 0 and 0 < int(w["work_units"]) <= 2500,
                f"Invalid archived arrival/work-unit count: {folder}")
        key = (workers, mode, rate)
        require(key not in identities, f"Duplicate archived condition: {key}")
        identities.add(key)
        selected.append((source, data))
    for workers in WORKERS:
        for mode in MODES:
            require(sum(w == workers and m == mode for w, m, _ in identities) == 6,
                    f"Expected six archived rates for {workers}/{mode}")
    return selected


def adjusted(source: dict, events: list[dict]) -> dict:
    data = copy.deepcopy(source)
    data["experiment"]["name"] = data["experiment"]["name"].replace(
        "bls-normalized-", STUDY + "-", 1)
    data["experiment"]["profile"] = "deep"
    data["repetitions"].update(
        blocks=BLOCKS, warmup_runs_per_block=WARMUPS,
        measured_runs_per_block=REPETITIONS_PER_PASS,
    )
    data["randomization"]["seed"] = 20261103
    data["analysis"]["reference_configuration"] = "rayon"
    data["analysis"]["bootstrap_seed"] = 20261103
    data["execution"]["settle_seconds"] = 0.0
    data["artifacts"]["output_root"] = str(DESTINATION.relative_to(ROOT))
    data["perf"] = {"passes": copy.deepcopy(events)}
    original = data["configurations"]
    require({c["id"] for c in original} == ORIGINAL_CONFIGS,
            "Missing archived configurations")
    data["configurations"] = [
        next(c for c in original if c["id"] == "rayon"),
        {"id": "bevy", "scheduler": "bevy"},
        *(c for c in original if c["id"] != "rayon"),
    ]
    return data


def plan(archive: Path = ARCHIVE) -> tuple[dict, dict[Path, str]]:
    perf = tomllib.loads(PERF_SPEC.read_text())["perf"]["passes"]
    require(len(perf) == PASS_COUNT
            and [p["name"] for p in perf] == ["cpu-cache", "l1-branches"],
            "Unexpected advanced perf passes")
    configs = {}
    entries = []
    for archived, old in archived_campaigns(archive):
        data = adjusted(old, perf)
        name = data["experiment"]["name"]
        output_file = GENERATED / (name + ".toml")
        require(output_file not in configs, f"Duplicate target: {output_file}")
        require({c["id"] for c in data["configurations"]} == FINAL_CONFIGS,
                f"Bevy/Threadance configuration set incomplete: {name}")
        require(int(data["repetitions"]["warmup_runs_per_block"]) == 4
                and int(data["repetitions"]["measured_runs_per_block"]) == 2,
                f"Invalid repetition setup: {name}")
        read_definition_source = render_toml(data)
        require(tomllib.loads(read_definition_source) == data,
                f"TOML round trip failed: {name}")
        invocations = len(make_plan(data, None))
        require(invocations == BLOCKS * 6, f"Unexpected invocations: {name}")
        configs[output_file] = read_definition_source
        entries.append({
            "name": name,
            "file": output_file.name,
            "workers": int(data["workload"]["workers"]),
            "delivery_mode": data["delivery"]["mode"],
            "arrival_rate": data["delivery"]["arrival_rates"][0],
            "work_units": int(data["workload"]["work_units"]),
            "source_archived_definition": str(archived.resolve()),
            "source_sha256": hashlib.sha256(archived.read_bytes()).hexdigest(),
            "definition_sha256": hashlib.sha256(read_definition_source.encode()).hexdigest(),
            "invocations": invocations,
            "unprofiled_warmup_batches": invocations * WARMUPS,
            "physical_profiled_runs": invocations * REPETITIONS_PER_PASS * PASS_COUNT,
        })
    entries.sort(key=lambda e: (e["workers"], e["delivery_mode"], e["arrival_rate"]))
    report = {
        "study": STUDY, "source": "archived 48 actual fixed/bursty experiments",
        "campaigns": entries,
        "totals": {
            "campaigns": len(entries),
            "invocations": sum(e["invocations"] for e in entries),
            "warmups": sum(e["unprofiled_warmup_batches"] for e in entries),
            "measured": sum(e["physical_profiled_runs"] for e in entries),
        },
        "passes": perf,
        "warmups_per_invocation": WARMUPS,
        "runs_per_pass": REPETITIONS_PER_PASS,
        "notes": [
            "Original absolute scheduled rate and workload prefix are unchanged.",
            "Pass order is reversed in even blocks; no perf counters pooled across passes.",
            "Capacity denominators remain from the four-warmup saturation recheck.",
            "All paced configurations include Bevy. Previous results are not overwritten.",
        ],
    }
    require(report["totals"] == {
        "campaigns": 48, "invocations": 5760, "warmups": 23040,
        "measured": 23040,
    }, "Unexpected total acquisition size")
    return report, configs


def write_definitions(report: dict, configs: dict[Path, str]) -> None:
    manifest = GENERATED / "plan.json"
    targets = {**configs, manifest: json.dumps(report, indent=2) + "\n"}
    for path, content in targets.items():
        if path.exists() and path.read_text() != content:
            raise FileExistsError(f"Won't overwrite changed generated experiment: {path}")
    GENERATED.mkdir(parents=True, exist_ok=True)
    for path, content in targets.items():
        path.write_text(content)


def run(*, check: bool = False, smoke: bool = False,
        archive: Path = ARCHIVE) -> dict:
    report, configs = plan(archive)
    if check:
        return {
            "study": STUDY, "totals": report["totals"],
            "passes": [p["name"] for p in report["passes"]],
            "warmups_per_invocation": WARMUPS,
            "measured_runs_per_pass": REPETITIONS_PER_PASS,
            "includes_bevy": True, "sources": len(report["campaigns"]),
        }
    if os.geteuid() == 0:
        raise RuntimeError("Run as your normal user; do not sudo the benchmark")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError("Commit or discard tracked changes before benchmarking")
    # Check unrestricted process-level kernel scheduling counters.
    subprocess.run(
        [sys.executable, "-m", "experiments.studies.check_perf_scope"],
        cwd=ROOT, check=True,
    )
    write_definitions(report, configs)
    if smoke:
        original = next(e for e in report["campaigns"]
                        if e["workers"] == 2
                        and e["delivery_mode"] == "fixed-arrivals")
        config = read_definition(GENERATED / original["file"])
        config["experiment"]["name"] = STUDY + "-smoke"
        config["workload"]["work_units"] = 128
        config["repetitions"]["blocks"] = 1
        config["configurations"] = [
            c for c in config["configurations"]
            if c["id"] in ("rayon", "bevy", "mpmc-sp0", "rtrb-sp0-b1")
        ]
        config["artifacts"]["output_root"] = str(
            (ROOT / "results/studies" / STUDY / "smoke").relative_to(ROOT))
        definition = GENERATED / "smoke.toml"
        rendered = render_toml(config)
        if definition.is_file() and definition.read_text() != rendered:
            raise FileExistsError(f"Smoke definition differs: {definition}")
        definition.write_text(rendered)
        entry_list = [{"name": config["experiment"]["name"], "file": definition.name,
                       "invocations": len(make_plan(config, None))}]
        destination = ROOT / "results/studies" / STUDY / "smoke"
    else:
        entry_list = report["campaigns"]
        destination = DESTINATION

    destination.mkdir(parents=True, exist_ok=True)
    done = 0
    with (destination / ".study-run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another run of this final study is active") from exc
        for index, entry in enumerate(entry_list, 1):
            definition = GENERATED / entry["file"]
            data = read_definition(definition)
            name = entry["name"]
            expected = entry["invocations"]
            prior = existing_campaign(name, definition, destination)
            if prior is None and any(destination.glob(name + "-*")):
                raise RuntimeError(f"Conflicting archived experiment: {name}")
            print(f"[{index}/{len(entry_list)}] {name}: "
                  f"{'resume' if prior else 'new'}", flush=True)
            if prior is None or not completed(prior, expected):
                subprocess.run(command("prepare", definition), cwd=ROOT, check=True)
                subprocess.run(
                    command("resume" if prior else "collect", definition, prior),
                    cwd=ROOT, check=True,
                )
                prior = existing_campaign(name, definition, destination)
                if prior is None or not completed(prior, expected):
                    raise RuntimeError(f"Incomplete campaign: {name}")
            verify_campaign_database(prior, data, expected)
            audit_perf_captures(prior, data, expected)
            if not (prior / "paired_effects.csv").is_file():
                subprocess.run(command("analyze", definition, prior),
                               cwd=ROOT, check=True)
            done += 1

    return dict(study=STUDY, completed_campaigns=done, total=len(entry_list),
                output_root=str(destination))


def audit_perf_captures(folder: Path, data: dict, expected_invocations: int) -> None:
    """Require every perf event for every run, correct scope and no multiplexing."""
    import sqlite3
    passes = {p["name"]: tuple(e.split(":")[0] for e in p["events"])
              for p in data["perf"]["passes"]}
    required_runs = expected_invocations * REPETITIONS_PER_PASS * PASS_COUNT
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        observed = db.execute(
            "SELECT r.id, r.perf_pass, p.event_name, p.counter_value, "
            "p.percent_running, p.status, pc.perf_exit_code "
            "FROM run r JOIN perf_metric p ON p.run_id=r.id "
            "JOIN perf_capture pc ON pc.run_id=r.id"
        ).fetchall()
        grouped = {}
        for rid, name, event, value, running, status, exit_code in observed:
            grouped.setdefault((rid, name), {})[event.split(":")[0]] = (
                event, value, running, status, exit_code)
        require(len(grouped) == required_runs,
                f"{folder}: expected {required_runs} complete PMU runs; got {len(grouped)}")
        for (_, name), measurements in grouped.items():
            require(name in passes, f"Unknown PMU pass {name}")
            require(set(measurements) == set(passes[name]),
                    f"Missing/unexpected counters in PMU pass {name}: {set(measurements)}")
            for base, (event, value, running, status, exit_code) in measurements.items():
                require(event.split(":")[0] == base and value is not None
                        and status == "ok" and exit_code == 0
                        and (running is None or 99.5 <= running <= 100.01),
                        f"{folder}: unavailable or multiplexed event {event} in {name}")
                if base in ("context-switches", "cpu-migrations"):
                    require(event == base, f"Kernel scheduler event restricted: {event}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--check", action="store_true")
    commands.add_argument("--smoke", action="store_true")
    commands.add_argument("--run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(check=args.check, smoke=args.smoke), indent=2))


if __name__ == "__main__":
    main()
