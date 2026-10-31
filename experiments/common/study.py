"""Expand a study specification into reproducible campaign TOMLs (no measurements).

The existing campaign engine remains responsible for calibration, collection,
resumption and statistical analysis. This module only designs experiments.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import re
import tomllib
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition

AXES = {
    "workers", "work_units", "operations_per_work_unit",
    "certificates_per_work_unit", "delivery_modes",
    "queue_capacities", "hot_first_backlogs",
    "schedulers", "wait_policies", "spin_durations_us",
}
OVERRIDES = {
    "blocks", "warmup_runs_per_block", "measured_runs_per_block",
    "settle_seconds", "profile",
}
NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def root_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Path must stay within repository: {value}")
    return path


def read_study(path: Path) -> dict:
    spec = tomllib.loads(path.read_text())
    meta = spec.get("study", {})
    if not NAME.fullmatch(str(meta.get("name", ""))):
        raise ValueError("study.name must contain only letters, digits, _ or -")
    for required in ("template", "output_dir"):
        if not isinstance(meta.get(required), str) or not meta[required]:
            raise ValueError(f"study.{required} is required")
        root_path(meta[required])
    root_path(meta.get("output_root", f"results/studies/{meta['name']}/campaigns"))
    if int(meta.get("max_invocations", 2000)) < 1:
        raise ValueError("study.max_invocations must be positive")
    axes = spec.get("axes", {})
    if not axes or set(axes) - AXES:
        raise ValueError(f"Specify supported axes only: {sorted(AXES)}")
    for key, values in axes.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"axes.{key} must be a non-empty array")
        if len(set(values)) != len(values):
            raise ValueError(f"axes.{key} contains duplicates")
        if key == "schedulers":
            if any(v not in ("rayon", "bevy", "threadance", "threadance-hot-first",
                             "threadance-hot-first-rtrb") for v in values):
                raise ValueError("Unknown scheduler")
        elif key == "wait_policies":
            if any(v not in ("crossbeam-wait", "spin-then-park", "agave-backoff")
                   for v in values):
                raise ValueError("Unknown wait policy")
        elif key == "spin_durations_us":
            if any(type(v) is not int or v < 0 for v in values):
                raise ValueError("spin durations must be nonnegative integers")
        elif key == "delivery_modes":
            if any(v not in (
                "all-at-once", "fixed-arrivals", "steady-arrivals",
                "variable-arrivals", "bursty-arrivals"
            ) for v in values):
                raise ValueError("Unknown delivery mode")
        elif any(type(v) is not int or v < 1 for v in values):
            raise ValueError(f"axes.{key} needs positive integers")
    perf_passes = spec.get("perf", {}).get("passes", [])
    if perf_passes:
        from experiments.common.campaign import read_definition
        if not isinstance(perf_passes, list) or not all(isinstance(p, dict) for p in perf_passes):
            raise ValueError("perf.passes must be an array of tables")
    overrides = spec.get("overrides", {})
    if set(overrides) - OVERRIDES:
        raise ValueError(f"Unknown overrides: {set(overrides) - OVERRIDES}")
    for key, value in overrides.items():
        if key == "profile":
            if value not in ("none", "standard", "deep"):
                raise ValueError("Unknown profile override")
        elif key == "settle_seconds":
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid settle_seconds")
        elif type(value) is not int or value < (0 if key == "warmup_runs_per_block" else 1):
            raise ValueError(f"Invalid {key}")
    return spec


def expand_configurations(base: dict, axes: dict, workers: int) -> tuple[list[dict], list[dict]]:
    """Cross compatible topology / wait policy / queue parameters only."""
    configs = []
    skipped = []
    if "schedulers" in axes:
        originals = [{"id": scheduler, "scheduler": scheduler}
                     for scheduler in axes["schedulers"]]
    else:
        originals = base["configurations"]

    for original in originals:
        scheduler = original["scheduler"]
        if scheduler in ("rayon", "bevy"):
            configs.append(copy.deepcopy(original))
            continue

        capacities = axes.get("queue_capacities", [
            original.get("queue_capacity", base["workload"].get("queue_capacity", 1024))
        ])
        hot = "hot-first" in scheduler
        backlogs = (axes.get("hot_first_backlogs", [original.get("hot_first_backlog", 1)])
                    if hot else [None])
        allowed_policies = (("spin-then-park", "agave-backoff")
                            if hot else ("crossbeam-wait", "spin-then-park", "agave-backoff"))
        policies = axes.get("wait_policies", [original.get("wait_policy", "spin-then-park")])
        for policy, capacity, backlog in itertools.product(policies, capacities, backlogs):
            if policy not in allowed_policies:
                skipped.append(dict(scheduler=scheduler, wait_policy=policy,
                                    reason="policy incompatible with queue topology"))
                continue
            if hot and backlog * workers > capacity:
                skipped.append(dict(scheduler=scheduler, workers=workers,
                                    queue_capacity=capacity, hot_first_backlog=backlog,
                                    reason="backlog * workers exceeds queue capacity"))
                continue
            if policy == "spin-then-park":
                spins = axes.get("spin_durations_us", original.get("spin_us", [0]))
                if not isinstance(spins, list):
                    spins = [spins]
            else:
                spins = [0]
            for spin in spins:
                config = copy.deepcopy(original)
                config["wait_policy"] = policy
                config["queue_capacity"] = capacity
                config["spin_us"] = spin
                if hot:
                    config["hot_first_backlog"] = backlog
                # Explicit complete condition identity, including independent axes.
                config["id"] = (f"{scheduler}__{policy}__q{capacity}"
                                + (f"__b{backlog}" if hot else "")
                                + (f"__s{spin}" if policy == "spin-then-park" else ""))
                configs.append(config)
    ids = [config["id"] for config in configs]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate expanded configuration IDs")
    if not configs:
        raise ValueError("No compatible configurations")
    reference = base.get("analysis", {}).get(
        "reference_configuration", base["configurations"][0]["id"])
    if reference not in ids:
        raise ValueError(
            f"Reference '{reference}' must identify an unexpanded baseline; "
            "use Rayon or Bevy for a full topology/policy study")
    return configs, skipped

def expand_study(spec: dict, base: dict) -> tuple[list[tuple[str, dict, dict]], list[dict]]:
    axes = spec["axes"]
    workload_kind = base["experiment"]["workload"]
    if workload_kind == "compute-heavy" and "certificates_per_work_unit" in axes:
        raise ValueError("BLS granularity axis incompatible with compute-heavy")
    if workload_kind == "bls-aggregate-verify" and "operations_per_work_unit" in axes:
        raise ValueError("Compute-heavy granularity axis incompatible with BLS")

    granularity_key = ("operations_per_work_unit" if workload_kind == "compute-heavy"
                       else "certificates_per_work_unit")
    w = base["workload"]
    matrix = itertools.product(
        axes.get("workers", [w["workers"]]),
        axes.get("work_units", [w["work_units"]]),
        axes.get(granularity_key, [w[granularity_key]]),
        axes.get("delivery_modes", [base["delivery"]["mode"]]),
    )
    campaigns = []
    skipped = []
    for workers, work_units, granularity, mode in matrix:
        concrete = copy.deepcopy(base)
        concrete["workload"]["workers"] = workers
        concrete["workload"]["work_units"] = work_units
        concrete["workload"][granularity_key] = granularity
        concrete["delivery"]["mode"] = mode
        if mode == "all-at-once":
            concrete["delivery"].pop("rho", None)
            concrete["delivery"].pop("arrival_rates", None)
        elif not (concrete["delivery"].get("rho") or concrete["delivery"].get("arrival_rates")):
            raise ValueError("Scheduled modes require rho or arrival_rates in the template")
        for key, value in spec.get("overrides", {}).items():
            section = ("execution" if key == "settle_seconds"
                       else "experiment" if key == "profile" else "repetitions")
            concrete.setdefault(section, {})[key] = value

        if spec.get("perf", {}).get("passes"):
            concrete["perf"] = copy.deepcopy(spec["perf"])
        concrete["configurations"], rejected = expand_configurations(base, axes, workers)
        skipped.extend(rejected)
        key = f"w{workers}-n{work_units}-g{granularity}-{mode}"
        name = f"{spec['study']['name']}-{key}"
        concrete["experiment"]["name"] = name
        concrete["artifacts"]["output_root"] = spec["study"].get(
            "output_root", f"results/studies/{spec['study']['name']}/campaigns"
        )
        service_us = 100.0 if concrete["delivery"].get("rho") else None
        plan = make_plan(concrete, service_us)
        passes = (len(concrete.get("perf", {}).get("passes") or [])
                  or (4 if concrete["experiment"]["profile"] == "deep" else 1))
        invocations = len(plan)
        repeats = concrete["repetitions"]
        stats = dict(
            name=name, file=f"{key}.toml",
            conditions=invocations // repeats["blocks"],
            invocations=invocations,
            measured_runs=invocations * repeats["measured_runs_per_block"] * passes,
            warmup_runs=(sum(row.get("warmup_runs", repeats.get("warmup_runs_per_block", 0))
                             for row in plan)
                         * (1 if concrete.get("perf", {}).get("passes") else passes)),
            settle_floor_seconds=invocations * concrete.get("execution", {}).get("settle_seconds", 3),
        )
        campaigns.append((stats["file"], concrete, stats))
    if not campaigns:
        raise ValueError("Study has no campaigns")
    if len({name for name, _, _ in campaigns}) != len(campaigns):
        raise ValueError("Study contains duplicate campaign filenames")
    total = sum(stats["invocations"] for _, _, stats in campaigns)
    maximum = int(spec["study"].get("max_invocations", 2000))
    if total > maximum:
        raise ValueError(f"Study requires {total} invocations; limit is {maximum}")
    return campaigns, skipped


def toml_literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {toml_literal(item)}" for key, item in value.items()) + " }"
    raise ValueError(f"Cannot serialize TOML value: {value!r}")


def render_toml(data: dict) -> str:
    lines = []
    for section, values in data.items():
        if section == "configurations":
            for config in values:
                lines += ["[[configurations]]"]
                lines += [f"{key} = {toml_literal(value)}" for key, value in config.items()]
                lines.append("")
        else:
            lines += [f"[{section}]"]
            lines += [f"{key} = {toml_literal(value)}" for key, value in values.items()]
            lines.append("")
    result = "\n".join(lines)
    # Fail before persisting a malformed TOML.
    if tomllib.loads(result) != data:
        raise ValueError("TOML round-trip validation failed")
    return result


def design(spec_path: Path, *, write: bool) -> dict:
    spec = read_study(spec_path)
    template = read_definition(root_path(spec["study"]["template"]))
    campaigns, skipped = expand_study(spec, template)
    output = root_path(spec["study"]["output_dir"])
    files = {filename: render_toml(data) for filename, data, _ in campaigns}
    report = {
        "study": spec["study"]["name"],
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "template": spec["study"]["template"],
        "output_dir": str(output.relative_to(ROOT)),
        "campaigns": [
            {**stats, "sha256": hashlib.sha256(files[filename].encode()).hexdigest()}
            for filename, _, stats in campaigns
        ],
        "skipped_incompatible_configurations": skipped,
    }
    report["totals"] = {
        key: sum(c[key] for c in report["campaigns"])
        for key in ("invocations", "measured_runs", "warmup_runs", "settle_floor_seconds")
    }
    if write:
        manifest = json.dumps(report, indent=2, sort_keys=True) + "\n"
        targets = {output / name: content for name, content in files.items()}
        targets[output / "study_manifest.json"] = manifest
        # Check conflicts before writing anything.
        for path, content in targets.items():
            if path.exists() and path.read_text() != content:
                raise FileExistsError(f"Refusing to overwrite modified study artifact: {path}")
        output.mkdir(parents=True, exist_ok=True)
        for path, content in targets.items():
            path.write_text(content)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Print plan without writing files")
    action.add_argument("--write", action="store_true", help="Save campaign TOMLs and manifest")
    args = parser.parse_args()
    print(json.dumps(design(args.spec, write=args.write), indent=2))


if __name__ == "__main__":
    main()
