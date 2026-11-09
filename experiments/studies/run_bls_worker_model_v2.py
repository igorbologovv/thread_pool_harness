"""Independent BLS CPU-time model v2 experiment: 3,7,9,11,14 workers only.

--check     Validate frozen models and print plan, no benchmarks or filesystem writes.
--capacity  Rayon 2500-WU all-at-once calibration at only five missing worker counts.
--run       Ten paced campaigns, 50% normalized load and >=200 ms arrival window.
--evaluate  Compare unseen runs with predictions frozen before their acquisition.

All output is separate from the completed 1024-WU and 200-ms v1 campaigns.
"""
from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition, verify_campaign_database
from experiments.common.study import design, render_toml
from experiments.common.study_run import command, completed, existing_campaign
from experiments.studies.run_bls_normalized_deep_final import audit_perf_captures
from experiments.studies.bls_worker_model_v2 import TRAIN, HOLDOUT, load_frozen, validate_observations

STUDY = "bls-worker-model-v2"
RESULTS = ROOT / "results/studies" / STUDY
CALIB_SPEC = ROOT / "experiments/studies/bls_worker_model_v2_capacity.toml"
CALIB_GENERATED = ROOT / "experiments/generated/bls-worker-v2-capacity"
CALIB_RESULTS = RESULTS / "capacity/campaigns"
CAPACITY_FILE = RESULTS / "capacity_reference.json"
BASE = ROOT / "experiments/studies/bls_saturation_recheck_template.toml"
PERF_SPEC = ROOT / "experiments/studies/bls_advanced_perf.toml"
GENERATED = ROOT / "experiments/generated" / STUDY
OUTPUT = RESULTS / "campaigns"
OLD_DATA = ROOT / "results/studies/bls-normalized-deep-final/campaigns"
PREVIOUS_HOLDOUTS = ROOT / "results/studies/bls-worker-cubic-holdout-200ms/campaigns"
SOURCE_FROZEN = ROOT / "experiments/studies/bls_worker_model_v2_frozen.json"
BLOCKS = 40
MODES = ("fixed-arrivals", "bursty-arrivals")
CONFIGS = {"rayon","bevy","mpmc-sp0","mpmc-ab","rtrb-sp0-b1","rtrb-sp0-b2"}
SEED = 20261109
WINDOW = 0.2


def commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def preflight() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run without sudo; use sudo for kernel perf configuration only")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise RuntimeError("Tracked sources dirty; commit before benchmarking")
    subprocess.run([sys.executable,"-m","experiments.studies.check_perf_scope"],
                   cwd=ROOT,check=True)


def saturation(folder: Path, workers: int) -> float:
    values = defaultdict(list)
    with (folder/"analysis_raw_runs.csv").open(newline="") as file:
        for row in csv.DictReader(file):
            if row["configuration"] == "rayon":
                values[int(row["block"])].append(float(row["throughput"]))
    if set(values) != set(range(1,31)) or any(len(v)!=3 for v in values.values()):
        raise RuntimeError(f"Incomplete Rayon 30 blocks x3: {folder}")
    archived = read_definition(folder/"experiment.toml")
    if (archived["workload"]["workers"] != workers
        or archived["workload"]["work_units"] != 2500
        or archived["delivery"]["mode"] != "all-at-once"):
        raise RuntimeError(f"Wrong saturation definition at {folder}")
    return statistics.mean(statistics.mean(v) for v in values.values())


def capacity() -> dict:
    preflight()
    plan = design(CALIB_SPEC, write=True)
    if len(plan["campaigns"]) != len(HOLDOUT):
        raise RuntimeError("Expected exactly five calibrations")
    CALIB_RESULTS.mkdir(parents=True,exist_ok=True)
    with (CALIB_RESULTS/".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, entry in enumerate(plan["campaigns"],1):
            definition_path = CALIB_GENERATED / entry["file"]
            data = read_definition(definition_path)
            w = data["workload"]["workers"]
            if w not in HOLDOUT:
                raise RuntimeError("Refusing calibration outside frozen holdouts")
            previous = existing_campaign(entry["name"],definition_path,CALIB_RESULTS)
            if previous is None and any(CALIB_RESULTS.glob(entry["name"]+"-*")):
                raise RuntimeError(f"Conflicting campaign for {entry['name']}")
            print(f"[capacity {index}/5] {entry['name']}",flush=True)
            if previous is None or not completed(previous,entry["invocations"]):
                subprocess.run(command("prepare",definition_path),cwd=ROOT,check=True)
                subprocess.run(command("resume" if previous else "collect",
                                       definition_path,previous),cwd=ROOT,check=True)
                previous=existing_campaign(entry["name"],definition_path,CALIB_RESULTS)
            if previous is None or not completed(previous,entry["invocations"]):
                raise RuntimeError(f"Incomplete calibration {entry['name']}")
            verify_campaign_database(previous,data,entry["invocations"])
            if not (previous/"analysis_raw_runs.csv").exists():
                subprocess.run(command("analyze",definition_path,previous),
                               cwd=ROOT,check=True)
    rates={}
    for entry in plan["campaigns"]:
        path=CALIB_GENERATED/entry["file"]
        w=read_definition(path)["workload"]["workers"]
        folder=existing_campaign(entry["name"],path,CALIB_RESULTS)
        rates[str(w)]=saturation(folder,w)
    if set(map(int,rates))!=set(HOLDOUT):
        raise RuntimeError("Missing calibration for one of the five counts")
    record={
        "git_commit":commit(),
        "model_sha256":hashlib.sha256(SOURCE_FROZEN.read_bytes()).hexdigest(),
        "source":"Rayon 2500-WU all-at-once 30 blocks x3, 4 warmups",
        "new_calibration_workers":list(HOLDOUT),
        "rayon_wu_per_second":rates,
    }
    if CAPACITY_FILE.exists() and json.loads(CAPACITY_FILE.read_text()) != record:
        raise RuntimeError("Existing calibration reference differs; refusing overwrite")
    RESULTS.mkdir(parents=True,exist_ok=True)
    CAPACITY_FILE.write_text(json.dumps(record,indent=2)+"\n")
    return record


def load_capacity() -> dict[int,float]:
    record=json.loads(CAPACITY_FILE.read_text())
    if record["git_commit"] != commit():
        raise RuntimeError("Capacity was measured at another Git revision")
    if record["model_sha256"] != hashlib.sha256(SOURCE_FROZEN.read_bytes()).hexdigest():
        raise RuntimeError("Frozen model changed after calibration")
    rates={int(k):float(v) for k,v in record["rayon_wu_per_second"].items()}
    if set(rates)!=set(HOLDOUT) or any(not math.isfinite(v) or v<=0 for v in rates.values()):
        raise RuntimeError("Five finite positive capacity values required")
    return rates


def definitions(rates: dict[int,float]) -> list[tuple[dict,Path,str]]:
    base=read_definition(BASE)
    passes=tomllib.loads(PERF_SPEC.read_text())["perf"]["passes"]
    if [x["name"] for x in passes]!=["cpu-cache","l1-branches"]:
        raise RuntimeError("Wrong PMU passes")
    if {c["id"] for c in base["configurations"]} != CONFIGS:
        raise RuntimeError("Scheduler set differs from source training")
    if set(rates)!=set(HOLDOUT):
        raise ValueError("Need exactly all five new capacity values")
    entries=[]
    for w in HOLDOUT:
        if not math.isfinite(rates[w]) or rates[w]<=0:
            raise ValueError(f"Invalid saturation at w{w}")
        rate=rates[w]/2
        wu=math.ceil(WINDOW*rate)+1
        if wu>2500:
            raise ValueError(f"2500-WU dataset insufficient for w{w}")
        for mode in MODES:
            data=copy.deepcopy(base)
            name=f"{STUDY}-w{w}-{mode}"
            data["experiment"].update(name=name,profile="deep")
            data["workload"].update(workers=w,work_units=wu,
                                    dataset_work_units=2500,queue_capacity=64)
            data["delivery"]={"mode":mode,"arrival_rates":[round(rate,8)],
                              "arrival_seed_base":42}
            data["repetitions"]={"blocks":BLOCKS,"warmup_runs_per_block":4,
                                  "measured_runs_per_block":2}
            data["randomization"]["seed"]=SEED+w*2+MODES.index(mode)
            data["analysis"]["bootstrap_seed"]=SEED+w*2+MODES.index(mode)
            data["execution"]["settle_seconds"]=0.0
            data["artifacts"]["output_root"]=str(OUTPUT.relative_to(ROOT))
            data["perf"]={"passes":copy.deepcopy(passes)}
            path=GENERATED/(name+".toml")
            if len(make_plan(data,None))!=BLOCKS*len(CONFIGS):
                raise ValueError("Unexpected number of invocations")
            txt=render_toml(data)
            if tomllib.loads(txt)!=data:
                raise ValueError("TOML roundtrip error")
            entries.append((dict(name=name,workers=w,mode=mode,
                                 work_units=wu,offered_rate=rate,
                                 invocations=BLOCKS*len(CONFIGS)),path,txt))
    random.Random(SEED).shuffle(entries)
    return entries


def run() -> dict:
    load_frozen()
    preflight()
    entries=definitions(load_capacity())
    GENERATED.mkdir(parents=True,exist_ok=True)
    for _,path,txt in entries:
        if path.exists() and path.read_text()!=txt:
            raise RuntimeError(f"Generated campaign changed: {path}")
        path.write_text(txt)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    with (OUTPUT/".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index,(entry,path,_) in enumerate(entries,1):
            data=read_definition(path)
            previous=existing_campaign(entry["name"],path,OUTPUT)
            if previous is None and any(OUTPUT.glob(entry["name"]+"-*")):
                raise RuntimeError(f"Conflicting paced campaign {entry['name']}")
            print(f"[{index}/{len(entries)}] {entry['name']}",flush=True)
            if previous is None or not completed(previous,entry["invocations"]):
                subprocess.run(command("prepare",path),cwd=ROOT,check=True)
                subprocess.run(command("resume" if previous else "collect",path,previous),
                               cwd=ROOT,check=True)
                previous=existing_campaign(entry["name"],path,OUTPUT)
            if previous is None or not completed(previous,entry["invocations"]):
                raise RuntimeError(f"Incomplete paced campaign {entry['name']}")
            verify_campaign_database(previous,data,entry["invocations"])
            audit_perf_captures(previous,data,entry["invocations"])
            if not (previous/"analysis_raw_runs.csv").exists():
                subprocess.run(command("analyze",path,previous),cwd=ROOT,check=True)
    return {"new_campaigns":len(entries),"invocations":len(entries)*BLOCKS*len(CONFIGS),
            "physical_runs":len(entries)*BLOCKS*len(CONFIGS)*4,
            "output_root":str(OUTPUT)}


def _blocks(folder: Path, nblocks: int) -> dict[str,list[float]]:
    grouped=defaultdict(lambda:defaultdict(list))
    with (folder/"analysis_raw_runs.csv").open(newline="") as file:
        for row in csv.DictReader(file):
            if row["perf_pass"]=="cpu-cache":
                grouped[row["configuration"]][int(row["block"])].append(
                    float(row["task_clock_per_wu"]))
    if set(grouped)!=CONFIGS:
        raise RuntimeError(f"Missing configurations in {folder}")
    answer={}
    for scheduler,b in grouped.items():
        if set(b)!=set(range(1,nblocks+1)) or any(len(v)!=2 for v in b.values()):
            raise RuntimeError(f"Invalid randomized blocks at {folder}/{scheduler}")
        answer[scheduler]=[statistics.mean(b[i]) for i in range(1,nblocks+1)]
    return answer


def evaluate() -> dict:
    frozen=load_frozen()
    new_entries=definitions(load_capacity())
    series=defaultdict(dict)
    for entry,path,_ in new_entries:
        folder=existing_campaign(entry["name"],path,OUTPUT)
        if folder is None or not completed(folder,entry["invocations"]):
            raise RuntimeError(f"Missing complete holdout: {entry['name']}")
        for scheduler,blocks in _blocks(folder,BLOCKS).items():
            series[(entry["mode"],scheduler)][entry["workers"]]=blocks
    for w in (2,4,8,16):
        for mode in MODES:
            prefix=f"bls-normalized-deep-final-w{w}-load05000-{mode}"
            folders=sorted(p for p in OLD_DATA.glob(prefix+"-*")
                           if p.is_dir() and (p/"analysis_raw_runs.csv").is_file())
            if len(folders)!=1:
                raise RuntimeError(f"Expected original training campaign {prefix}; got {len(folders)}")
            for scheduler,blocks in _blocks(folders[0],20).items():
                series[(mode,scheduler)][w]=blocks
    for w in (6,10,12):
        for mode in MODES:
            prefix=f"bls-worker-cubic-holdout-200ms-w{w}-{mode}"
            folders=sorted(p for p in PREVIOUS_HOLDOUTS.glob(prefix+"-*")
                           if p.is_dir() and (p/"analysis_raw_runs.csv").is_file())
            if len(folders)!=1:
                raise RuntimeError(f"Expected v1 training campaign {prefix}; got {len(folders)}")
            for scheduler,blocks in _blocks(folders[0],40).items():
                series[(mode,scheduler)][w]=blocks
    results=[]
    for key,model in sorted(frozen["models"].items()):
        mode,scheduler=key.split("|")
        blocks=series[(mode,scheduler)]
        report=validate_observations(model,blocks,seed=SEED,repeats=4000)
        for entry in report:
            results.append(dict(mode=mode,scheduler=scheduler,model=model["model"],**entry))
    destination=RESULTS/"validation"
    destination.mkdir(parents=True,exist_ok=True)
    with (destination/"frozen_v2_holdout_predictions.csv").open("w",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    return {"predictions":len(results),"models":len(frozen["models"]),
            "validation_workers":list(HOLDOUT),
            "report":str(destination/"frozen_v2_holdout_predictions.csv")}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_mutually_exclusive_group(required=True)
    for action in ("check","capacity","run","evaluate"):
        actions.add_argument("--"+action,action="store_true")
    args=parser.parse_args()
    frozen=load_frozen()
    if args.check:
        result={"study":STUDY,"model_variants":{k:v["model"] for k,v in frozen["models"].items()},
                "training_workers":list(TRAIN),"holdouts":list(HOLDOUT),
                "new_capacity_campaigns":len(HOLDOUT),"new_paced_campaigns":len(HOLDOUT)*len(MODES),
                "new_paced_invocations":len(HOLDOUT)*len(MODES)*BLOCKS*len(CONFIGS),
                "new_physical_runs":len(HOLDOUT)*len(MODES)*BLOCKS*len(CONFIGS)*4,
                "capacity_ready":CAPACITY_FILE.exists()}
        if CAPACITY_FILE.exists():
            result["paced_conditions"]=[e for e,_,_ in definitions(load_capacity())]
    elif args.capacity:
        result=capacity()
    elif args.run:
        result=run()
    else:
        result=evaluate()
    print(json.dumps(result,indent=2),flush=True)


if __name__=="__main__":
    main()
