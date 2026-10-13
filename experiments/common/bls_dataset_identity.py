#!/usr/bin/env python3
"""Resolve and fingerprint the cached BLS benchmark dataset.

The benchmark cache filename is deterministic from the workload-generation
parameters. This utility verifies the binary cache header and reports a SHA-256
of the exact bytes used by a campaign so dataset identity can be retained with
results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

DATASET_CACHE_VERSION = 1
DATASET_MAGIC = b"TBLSDS01"


@dataclass(frozen=True)
class DatasetConfig:
    validators: int
    signers_per_certificate: int
    certificates_per_work_unit: int
    work_units: int
    workload_seed: int


@dataclass(frozen=True)
class DatasetIdentity:
    path: str
    sha256: str
    size_bytes: int
    cache_version: int
    validators: int
    signers_per_certificate: int
    certificates_per_work_unit: int
    work_units: int
    workload_seed: int
    input_count: int


def expected_dataset_path(cache_dir: Path, config: DatasetConfig) -> Path:
    return cache_dir / (
        f"bls-v{DATASET_CACHE_VERSION}"
        f"-v{config.validators}"
        f"-s{config.signers_per_certificate}"
        f"-c{config.certificates_per_work_unit}"
        f"-wu{config.work_units}"
        f"-seed{config.workload_seed}.bin"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path: Path) -> tuple[int, int, int, int, int, int, int]:
    # Rust cache layout:
    # magic[8], version:u32, then six little-endian u64 fields.
    with path.open("rb") as handle:
        magic = handle.read(8)
        if magic != DATASET_MAGIC:
            raise ValueError(
                f"invalid dataset magic in {path}: {magic!r} != {DATASET_MAGIC!r}"
            )

        raw = handle.read(4 + 6 * 8)
        if len(raw) != 4 + 6 * 8:
            raise ValueError(f"dataset header is truncated: {path}")

    version, validators, signers, certificates, work_units, seed, input_count = (
        struct.unpack("<IQQQQQQ", raw)
    )
    return version, validators, signers, certificates, work_units, seed, input_count


def identify_dataset(path: Path, config: DatasetConfig) -> DatasetIdentity:
    if not path.is_file():
        raise FileNotFoundError(
            f"cached BLS dataset does not exist: {path}\n"
            "Run the calibration or a BLS harness invocation first."
        )

    (
        version,
        validators,
        signers,
        certificates,
        work_units,
        seed,
        input_count,
    ) = read_header(path)

    expected = {
        "cache_version": DATASET_CACHE_VERSION,
        "validators": config.validators,
        "signers_per_certificate": config.signers_per_certificate,
        "certificates_per_work_unit": config.certificates_per_work_unit,
        "work_units": config.work_units,
        "workload_seed": config.workload_seed,
        "input_count": config.work_units * config.certificates_per_work_unit,
    }
    actual = {
        "cache_version": version,
        "validators": validators,
        "signers_per_certificate": signers,
        "certificates_per_work_unit": certificates,
        "work_units": work_units,
        "workload_seed": seed,
        "input_count": input_count,
    }

    mismatches = [
        f"{key}: expected {expected[key]!r}, got {actual[key]!r}"
        for key in expected
        if expected[key] != actual[key]
    ]
    if mismatches:
        raise ValueError(
            "dataset header does not match experiment configuration:\n  - "
            + "\n  - ".join(mismatches)
        )

    resolved = path.resolve()
    return DatasetIdentity(
        path=str(resolved),
        sha256=sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
        cache_version=version,
        validators=validators,
        signers_per_certificate=signers,
        certificates_per_work_unit=certificates,
        work_units=work_units,
        workload_seed=seed,
        input_count=input_count,
    )


def config_from_experiment(path: Path) -> tuple[Path, DatasetConfig]:
    data = tomllib.loads(path.read_text())
    workload = data["workload"]
    artifacts = data["artifacts"]

    config = DatasetConfig(
        validators=int(workload["validators"]),
        signers_per_certificate=int(workload["signers_per_certificate"]),
        certificates_per_work_unit=int(workload["certificates_per_work_unit"]),
        work_units=int(workload["work_units"]),
        workload_seed=int(workload["workload_seed"]),
    )
    return Path(artifacts["dataset_cache_dir"]), config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify and fingerprint a cached BLS benchmark dataset"
    )
    parser.add_argument(
        "--experiment",
        type=Path,
        default=Path("experiments/fixed_wait.toml"),
        help="experiment TOML containing workload and dataset-cache settings",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional path to write the JSON identity artifact",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir, config = config_from_experiment(args.experiment)
    path = expected_dataset_path(cache_dir, config)
    identity = identify_dataset(path, config)
    text = json.dumps(asdict(identity), indent=2, sort_keys=True) + "\n"

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)

    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
