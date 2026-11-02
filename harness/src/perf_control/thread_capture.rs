//! Strict per-TID perf capture for a pre-warmed 2-worker scheduler.
//! Existing process-wide perf behavior is unchanged unless explicitly enabled.
use std::{collections::{BTreeMap, BTreeSet, HashMap}, fs};

use serde_json::{json, Value};
use super::{parse_metric, PerfError, PerfMetric, PerfResult};

pub(super) fn snapshot_threads() -> PerfResult<Vec<Value>> {
    let main_tid = std::process::id();
    let mut threads = Vec::new();
    for entry in fs::read_dir("/proc/self/task")? {
        let entry = entry?;
        let tid: u32 = entry.file_name().to_string_lossy().parse()?;
        let comm = fs::read_to_string(entry.path().join("comm"))?
            .trim().to_owned();
        threads.push(json!({
            "tid": tid,
            "role": if tid == main_tid { "pacer" } else { "worker" },
            "comm": comm,
        }));
    }
    threads.sort_by_key(|row| row["tid"].as_u64().unwrap_or(0));
    if threads.len() != 3 ||
        !threads.iter().any(|row| row["tid"].as_u64() == Some(u64::from(main_tid))) {
        return Err(format!(
            "per-thread diagnostic requires exactly one pacer/main TID and two existing workers; got {threads:?}"
        ).into());
    }
    Ok(threads)
}

pub(super) fn tid_list(threads: &[Value]) -> PerfResult<String> {
    if threads.len() != 3 {
        return Err("expected exactly three thread IDs".into());
    }
    let tids: Result<Vec<_>, PerfError> = threads.iter()
        .map(|row| Ok(row["tid"].as_u64()
            .ok_or("missing thread ID")?.to_string()))
        .collect();
    Ok(tids?.join(","))
}

fn tid_from_label(label: &str) -> PerfResult<u64> {
    let suffix = label.rsplit('-').next().unwrap_or(label).trim();
    Ok(suffix.parse()?)
}

pub(super) fn validate_and_aggregate(
    raw: String,
    _parsed_metrics: Vec<PerfMetric>,
    threads: &[Value],
    expected_events: &[String],
) -> PerfResult<(String, Vec<PerfMetric>)> {
    let mut doc: Value = serde_json::from_str(&raw)?;
    if !doc["unparsed"].as_array().is_some_and(|v| v.is_empty()) {
        return Err("unparseable per-thread perf output".into());
    }
    let records = doc.get_mut("records").and_then(Value::as_array_mut)
        .ok_or("missing perf records array")?;
    if records.is_empty() {
        return Err("empty or unparseable per-thread perf output".into());
    }

    let expected_tids: BTreeSet<u64> = threads.iter()
        .map(|t| t["tid"].as_u64().expect("snapshot validated TID")).collect();
    let expected_event_names: BTreeSet<String> = expected_events.iter()
        .map(|s| s.split(':').next().unwrap_or(s).to_owned()).collect();
    if expected_event_names.len() != expected_events.len() {
        return Err("duplicate base event names requested".into());
    }
    let roles: HashMap<u64, String> = threads.iter().map(|t| {
        (t["tid"].as_u64().expect("snapshot validated TID"),
         t["role"].as_str().expect("snapshot validated role").to_owned())
    }).collect();

    let mut seen: BTreeSet<(u64, String)> = BTreeSet::new();
    let mut totals: BTreeMap<String, PerfMetric> = BTreeMap::new();
    for record in records.iter_mut() {
        let label = record["thread"].as_str()
            .ok_or("missing thread identifier in perf JSON")?;
        let tid = tid_from_label(label)?;
        if !expected_tids.contains(&tid) {
            return Err(format!("unexpected perf TID {tid}: {label}").into());
        }
        let metric = parse_metric(record)
            .ok_or("missing perf event in per-thread record")?;
        let base = metric.event_name.split(':').next()
            .ok_or("missing base event name")?.to_owned();
        if !expected_event_names.contains(&base) {
            return Err(format!("unexpected perf event {}", metric.event_name).into());
        }
        if !seen.insert((tid, base)) {
            return Err(format!("duplicate perf record: TID {tid}, event {}", metric.event_name).into());
        }
        let value = metric.counter_value
            .ok_or("missing per-thread counter value")?;
        if metric.status != "ok" || !value.is_finite() || value < 0.0 ||
           metric.percent_running.is_some_and(|pct| !(99.5..=100.01).contains(&pct)) {
            return Err(format!("invalid per-thread event {metric:?}").into());
        }
        record["tid"] = json!(tid);
        record["role"] = json!(roles[&tid]);

        if let Some(total) = totals.get_mut(&metric.event_name) {
            total.counter_value = Some(total.counter_value.unwrap() + value);
            total.percent_running = match (total.percent_running, metric.percent_running) {
                (Some(a), Some(b)) => Some(a.min(b)),
                _ => None,
            };
        } else {
            totals.insert(metric.event_name.clone(), PerfMetric {
                event_runtime_ns: None, metric_value: None, metric_unit: None, ..metric
            });
        }
    }
    let expected: BTreeSet<(u64, String)> = expected_tids.iter()
        .flat_map(|tid| expected_event_names.iter()
            .map(move |name| (*tid, name.clone()))).collect();
    if seen != expected {
        return Err(format!(
            "missing per-thread counters: {:?}",
            expected.difference(&seen).collect::<Vec<_>>()
        ).into());
    }
    doc["thread_map"] = Value::Array(threads.to_vec());
    Ok((serde_json::to_string(&doc)?, totals.into_values().collect()))
}
