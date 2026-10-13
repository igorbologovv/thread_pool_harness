#!/usr/bin/env python3
"""Validate a scheduler-free BLS service-time calibration artifact.

This is the single source of truth for calibration compatibility checks used by
benchmark campaigns. It deliberately ignores scheduler-only and test-only
changes while rejecting changes that can affect the measured BLS execute path.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

BLS_DEPENDENCY = "solana-bls-signatures"
SERVICE_TIME_PATH = "harness/src/bin/service_time.rs"
WORKLOAD_MOD_PATH = "harness/src/workload/mod.rs"
WORKLOAD_PATH = "harness/src/workload/bls_aggregate_verify.rs"
HARNESS_MANIFEST_PATH = "harness/Cargo.toml"
LOCKFILE_PATH = "Cargo.lock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a BLS service-time calibration artifact"
    )
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--expect-work-units", type=int)
    parser.add_argument("--expect-validators", type=int)
    parser.add_argument("--expect-signers-per-certificate", type=int)
    parser.add_argument("--expect-certificates-per-work-unit", type=int)
    parser.add_argument("--expect-workload-seed", type=int)
    return parser.parse_args()


def command_output(args: list[str]) -> str:
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({' '.join(args)}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def git_show(commit: str, path: str) -> str:
    return command_output(["git", "show", f"{commit}:{path}"])


def strip_test_module(source: str) -> str:
    marker = "\n#[cfg(test)]"
    return source.split(marker, 1)[0]


def dependency_spec(commit: str, package_name: str) -> Any:
    manifest = tomllib.loads(git_show(commit, HARNESS_MANIFEST_PATH))
    dependencies = manifest.get("dependencies", {})
    if package_name not in dependencies:
        raise RuntimeError(
            f"{package_name!r} is missing from {HARNESS_MANIFEST_PATH}"
        )
    return dependencies[package_name]


def lock_dependency_closure(lock_text: str, root_name: str) -> dict[tuple[str, str, str], tuple[Any, tuple[str, ...]]]:
    lock = tomllib.loads(lock_text)
    packages = lock.get("package", [])
    by_name: dict[str, list[dict[str, Any]]] = {}

    for package in packages:
        by_name.setdefault(package["name"], []).append(package)

    roots = by_name.get(root_name, [])
    if len(roots) != 1:
        raise RuntimeError(
            f"expected exactly one {root_name!r} package in Cargo.lock, "
            f"found {len(roots)}"
        )

    def key(package: dict[str, Any]) -> tuple[str, str, str]:
        return (
            package["name"],
            package["version"],
            package.get("source", ""),
        )

    def resolve_dependency(spec: str) -> dict[str, Any]:
        parts = spec.split()
        if not parts:
            raise RuntimeError(f"invalid Cargo.lock dependency entry: {spec!r}")

        name = parts[0]
        version = None
        source = None

        if len(parts) >= 2 and parts[1] and parts[1][0].isdigit():
            version = parts[1]
        if len(parts) >= 3 and parts[2].startswith("(") and parts[2].endswith(")"):
            source = parts[2][1:-1]

        candidates = by_name.get(name, [])
        if version is not None:
            candidates = [p for p in candidates if p["version"] == version]
        if source is not None:
            candidates = [p for p in candidates if p.get("source") == source]

        if len(candidates) != 1:
            raise RuntimeError(
                f"could not uniquely resolve dependency {spec!r}: "
                f"found {len(candidates)} candidates"
            )
        return candidates[0]

    closure: dict[tuple[str, str, str], tuple[Any, tuple[str, ...]]] = {}
    pending = [roots[0]]

    while pending:
        package = pending.pop()
        package_key = key(package)
        if package_key in closure:
            continue

        dependencies = tuple(sorted(package.get("dependencies", [])))
        closure[package_key] = (package.get("checksum"), dependencies)
        pending.extend(resolve_dependency(dep) for dep in dependencies)

    return closure


def current_cpu_model() -> str:
    output = command_output(["lscpu"])
    for line in output.splitlines():
        if line.startswith("Model name:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not determine CPU model from lscpu")


def append_expected_mismatch(
    errors: list[str], config: dict[str, Any], key: str, expected: int | None
) -> None:
    if expected is None:
        return
    actual = config.get(key)
    if actual != expected:
        errors.append(
            f"{key} mismatch: calibration={actual!r}, experiment={expected!r}"
        )


def describe_closure_change(
    old: dict[tuple[str, str, str], tuple[Any, tuple[str, ...]]],
    new: dict[tuple[str, str, str], tuple[Any, tuple[str, ...]]],
) -> str:
    old_keys = set(old)
    new_keys = set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(key for key in old_keys & new_keys if old[key] != new[key])

    parts: list[str] = []
    if added:
        parts.append("added=" + ", ".join(f"{n}-{v}" for n, v, _ in added))
    if removed:
        parts.append("removed=" + ", ".join(f"{n}-{v}" for n, v, _ in removed))
    if changed:
        parts.append("changed=" + ", ".join(f"{n}-{v}" for n, v, _ in changed))
    return "; ".join(parts)


def main() -> int:
    args = parse_args()
    calibration_path = args.calibration.resolve()

    try:
        data = json.loads(calibration_path.read_text())
    except Exception as exc:
        print(f"ERROR: could not read calibration JSON: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []

    if data.get("schema_version") != 1:
        errors.append(
            f"unsupported calibration schema_version {data.get('schema_version')!r}"
        )

    if data.get("workload") != "bls-aggregate-verify":
        errors.append(
            "workload mismatch: expected 'bls-aggregate-verify', got "
            f"{data.get('workload')!r}"
        )

    config = data.get("workload_config", {})
    required_config = (
        "calibration_work_units",
        "validators",
        "signers_per_certificate",
        "certificates_per_work_unit",
        "workload_seed",
    )
    for key in required_config:
        if key not in config:
            errors.append(f"workload_config.{key} is missing")

    append_expected_mismatch(
        errors, config, "calibration_work_units", args.expect_work_units
    )
    append_expected_mismatch(errors, config, "validators", args.expect_validators)
    append_expected_mismatch(
        errors,
        config,
        "signers_per_certificate",
        args.expect_signers_per_certificate,
    )
    append_expected_mismatch(
        errors,
        config,
        "certificates_per_work_unit",
        args.expect_certificates_per_work_unit,
    )
    append_expected_mismatch(
        errors, config, "workload_seed", args.expect_workload_seed
    )

    service = data.get("service_time", {})
    try:
        mean_us = float(service["mean_us"])
    except Exception:
        mean_us = math.nan
        errors.append("service_time.mean_us is missing or invalid")

    if not math.isfinite(mean_us) or mean_us <= 0:
        errors.append(f"service_time.mean_us must be finite and > 0, got {mean_us!r}")

    try:
        median_us = float(service["median_us"])
    except Exception:
        median_us = math.nan
        errors.append("service_time.median_us is missing or invalid")

    if not math.isfinite(median_us) or median_us <= 0:
        errors.append(
            f"service_time.median_us must be finite and > 0, got {median_us!r}"
        )

    samples = service.get("samples")
    if not isinstance(samples, int) or samples <= 0:
        errors.append(f"service_time.samples must be an integer > 0, got {samples!r}")

    provenance = data.get("provenance", {})
    calibration_commit = provenance.get("git_commit")
    current_commit = command_output(["git", "rev-parse", "HEAD"])

    if not calibration_commit:
        errors.append("calibration provenance.git_commit is missing")
    else:
        try:
            for path in (SERVICE_TIME_PATH, WORKLOAD_MOD_PATH):
                if git_show(calibration_commit, path) != git_show(current_commit, path):
                    errors.append(
                        f"calibration-relevant source changed since calibration: {path}"
                    )

            old_workload = strip_test_module(
                git_show(calibration_commit, WORKLOAD_PATH)
            )
            new_workload = strip_test_module(git_show(current_commit, WORKLOAD_PATH))
            if old_workload != new_workload:
                errors.append(
                    "calibration-relevant source changed since calibration: "
                    f"{WORKLOAD_PATH} (non-test code)"
                )

            old_spec = dependency_spec(calibration_commit, BLS_DEPENDENCY)
            new_spec = dependency_spec(current_commit, BLS_DEPENDENCY)
            if old_spec != new_spec:
                errors.append(
                    f"{BLS_DEPENDENCY} dependency specification changed since calibration"
                )

            old_closure = lock_dependency_closure(
                git_show(calibration_commit, LOCKFILE_PATH), BLS_DEPENDENCY
            )
            new_closure = lock_dependency_closure(
                git_show(current_commit, LOCKFILE_PATH), BLS_DEPENDENCY
            )
            if old_closure != new_closure:
                details = describe_closure_change(old_closure, new_closure)
                suffix = f": {details}" if details else ""
                errors.append(
                    "resolved BLS dependency closure changed since calibration"
                    f"{suffix}"
                )
        except Exception as exc:
            errors.append(
                f"could not validate calibration source compatibility: {exc}"
            )

    current_arch = platform.machine()
    if provenance.get("architecture") != current_arch:
        errors.append(
            "architecture mismatch: "
            f"calibration={provenance.get('architecture')!r}, "
            f"current={current_arch!r}"
        )

    try:
        cpu_model = current_cpu_model()
        if provenance.get("cpu_model") != cpu_model:
            errors.append(
                "CPU model mismatch: "
                f"calibration={provenance.get('cpu_model')!r}, "
                f"current={cpu_model!r}"
            )
    except Exception as exc:
        errors.append(f"could not validate CPU model: {exc}")

    if errors:
        print("ERROR: calibration is not valid for this experiment.", file=sys.stderr)
        print(file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(file=sys.stderr)
        print("Generate a fresh calibration with:", file=sys.stderr)
        print("  ./scripts/calibrate_service_time.sh", file=sys.stderr)
        return 1

    print(f"{mean_us:.9f}")
    print(data.get("calibration_id", "unknown"))
    print(samples)
    print(f"{median_us:.9f}")
    print(config["calibration_work_units"])
    print(config["validators"])
    print(config["signers_per_certificate"])
    print(config["certificates_per_work_unit"])
    print(config["workload_seed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
