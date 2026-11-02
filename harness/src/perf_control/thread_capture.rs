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

/// A TID is marked inactive only if its task-clock says zero/no runtime AND
/// every event confirms either measured zero or <not counted> with zero runtime.
/// A missing PMU value on an active thread remains an error, never zero-filled.
fn is_inactive_tid(records: &[Value], indices: &[usize]) -> PerfResult<bool> {
    let clock = indices.iter()
        .map(|&index| &records[index])
        .find(|record| record["event"].as_str()
            .is_some_and(|name| name.split(':').next() == Some("task-clock")))
        .ok_or("missing task-clock record for per-thread measurement")?;
    let clock_metric = parse_metric(clock).ok_or("malformed task-clock record")?;
    let zero_cpu_time = clock_metric.counter_value == Some(0.0)
        || (clock_metric.status == "not-counted"
            && clock_metric.event_runtime_ns == Some(0));
    if !zero_cpu_time {
        return Ok(false);
    }
    for &index in indices {
        let metric = parse_metric(&records[index])
            .ok_or("malformed event record while checking inactive thread")?;
        // An exact zero CPU-time observation cannot coexist with a positive
        // scheduled hardware/software counter in the same measured interval.
        // Treat such a mismatch as a failed measurement, not as a live worker.
        if metric.counter_value.is_some_and(|value| value > 0.0) {
            return Err(format!(
                "inconsistent per-thread counters: zero task-clock with positive {}={:?}; raw={}",
                metric.event_name, metric.counter_value, records[index]
            ).into());
        }
        let zero_observation = metric.counter_value == Some(0.0)
            && metric.status == "ok";
        let no_runtime_observation = metric.status == "not-counted"
            && metric.counter_value.is_none()
            && metric.event_runtime_ns == Some(0);
        if !zero_observation && !no_runtime_observation {
            return Ok(false);
        }
    }
    Ok(true)
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
    let records = doc.get("records").and_then(Value::as_array)
        .ok_or("missing perf records array")?;
    if records.is_empty() {
        return Err("empty per-thread perf output".into());
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

    // First prove that all events were returned for all three known TIDs.
    // This must happen before any inactive-thread normalization.
    let mut seen: BTreeSet<(u64, String)> = BTreeSet::new();
    let mut indices_by_tid: BTreeMap<u64, Vec<usize>> = BTreeMap::new();
    for (index, record) in records.iter().enumerate() {
        let label = record["thread"].as_str()
            .ok_or("missing TID label in per-thread perf JSON")?;
        let tid = tid_from_label(label)?;
        if !expected_tids.contains(&tid) {
            return Err(format!("unexpected TID {tid} in perf record {record}").into());
        }
        let metric = parse_metric(record).ok_or("malformed perf event record")?;
        let base = metric.event_name.split(':').next()
            .ok_or("missing base event name")?.to_owned();
        if !expected_event_names.contains(&base) {
            return Err(format!("unexpected event {}", metric.event_name).into());
        }
        if !seen.insert((tid, base)) {
            return Err(format!("duplicate perf record for TID {tid}: {}", metric.event_name).into());
        }
        indices_by_tid.entry(tid).or_default().push(index);
    }
    let expected: BTreeSet<(u64, String)> = expected_tids.iter()
        .flat_map(|tid| expected_event_names.iter()
            .map(move |name| (*tid, name.clone()))).collect();
    if seen != expected {
        return Err(format!("missing per-thread records: {:?}",
            expected.difference(&seen).collect::<Vec<_>>()).into());
    }

    let mut inactive_tids = BTreeSet::new();
    for (&tid, indices) in &indices_by_tid {
        if is_inactive_tid(records, indices)? {
            if roles[&tid] == "pacer" {
                return Err("pacer had no counted CPU time: invalid measured run".into());
            }
            inactive_tids.insert(tid);
        }
    }

    let records = doc.get_mut("records").and_then(Value::as_array_mut)
        .expect("records validated above");
    let mut totals: BTreeMap<String, PerfMetric> = BTreeMap::new();
    for record in records {
        let label = record["thread"].as_str().expect("label checked above");
        let tid = tid_from_label(label)?;
        let metric = parse_metric(record).expect("record checked above");
        let inactive = inactive_tids.contains(&tid);
        let value = if inactive {
            // Strictly derived accounting contribution, not a measured PMU
            // value. The original counter-value remains unchanged in raw JSON.
            0.0
        } else {
            let value = metric.counter_value.ok_or_else(|| {
                format!(
                    "missing ACTIVE-thread counter: tid={tid} role={} event={} \
                     status={} event_runtime_ns={:?} raw_record={record}",
                    roles[&tid], metric.event_name, metric.status,
                    metric.event_runtime_ns,
                )
            })?;
            if metric.status != "ok" || !value.is_finite() || value < 0.0 ||
               metric.percent_running.is_some_and(|pct| !(99.5..=100.01).contains(&pct)) {
                return Err(format!(
                    "invalid ACTIVE-thread event: tid={tid} role={} \
                     event={} status={} raw_record={record}",
                    roles[&tid], metric.event_name, metric.status,
                ).into());
            }
            value
        };
        record["tid"] = json!(tid);
        record["role"] = json!(roles[&tid]);
        record["measurement_state"] = json!(
            if inactive { "inactive-no-scheduled-runtime" } else { "counted" }
        );
        // For inactive TIDs, this is a conditional accounting value.
        // Keep raw 'counter-value' and event-runtime for audit/reinspection.
        record["accounting_value"] = json!(value);

        let event_name = metric.event_name.clone();
        if let Some(total) = totals.get_mut(&event_name) {
            total.counter_value = Some(total.counter_value.unwrap() + value);
            if !inactive {
                total.percent_running = match (total.percent_running, metric.percent_running) {
                    (Some(a), Some(b)) => Some(a.min(b)),
                    (None, b) | (b, None) => b,
                };
            }
        } else {
            totals.insert(event_name, PerfMetric {
                counter_value: Some(value),
                status: "ok".to_owned(),
                // Derived perf metrics and runtime cannot be summed across TIDs.
                event_runtime_ns: None,
                metric_value: None,
                metric_unit: None,
                percent_running: if inactive { None } else { metric.percent_running },
                ..metric
            });
        }
    }
    if inactive_tids.len() == 2 {
        return Err("both workers inactive in a completed BLS run".into());
    }
    doc["thread_map"] = Value::Array(threads.to_vec());
    doc["inactive_tids"] = json!(inactive_tids);
    Ok((serde_json::to_string(&doc)?, totals.into_values().collect()))
}


#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (String, Vec<Value>, Vec<String>) {
        let threads = vec![
            json!({"tid": 100, "role": "pacer", "comm": "harness"}),
            json!({"tid": 101, "role": "worker", "comm": "threadance-00"}),
            json!({"tid": 102, "role": "worker", "comm": "threadance-01"}),
        ];
        let mut records = Vec::new();
        for tid in [100, 101, 102] {
            for event in ["task-clock", "cycles:uk"] {
                let idle = tid == 102;
                records.push(json!({
                    "thread": format!("threadance-{tid}"),
                    "event": event,
                    "counter-value": if idle { "<not counted>" } else { "123.5" },
                    "event-runtime": if idle { 0 } else { 400000 },
                    "pcnt-running": 100.0,
                    "unit": if event == "task-clock" { "msec" } else { "" },
                }));
            }
        }
        let raw = json!({"records": records, "unparsed": [], "stderr": ""}).to_string();
        (raw, threads, vec!["task-clock".to_owned(), "cycles:uk".to_owned()])
    }

    #[test]
    fn fully_inactive_worker_is_explicit_and_preserves_raw_not_counted() {
        let (raw, threads, events) = fixture();
        let (stored, totals) = validate_and_aggregate(raw, vec![], &threads, &events).unwrap();
        let doc: Value = serde_json::from_str(&stored).unwrap();
        assert_eq!(doc["inactive_tids"], json!([102]));
        let inactive = doc["records"].as_array().unwrap().iter()
            .filter(|record| record["tid"] == 102).collect::<Vec<_>>();
        assert_eq!(inactive.len(), 2);
        for record in inactive {
            assert_eq!(record["counter-value"], "<not counted>");
            assert_eq!(record["measurement_state"], "inactive-no-scheduled-runtime");
            assert_eq!(record["accounting_value"], 0.0);
        }
        assert_eq!(totals.len(), 2);
        for counter in totals {
            assert_eq!(counter.counter_value, Some(247.0));
            assert_eq!(counter.status, "ok");
        }
    }

    #[test]
    fn active_thread_missing_cycles_must_fail() {
        let (raw, threads, events) = fixture();
        let mut doc: Value = serde_json::from_str(&raw).unwrap();
        let cycles = doc["records"].as_array_mut().unwrap().iter_mut()
            .find(|record| record["thread"] == "threadance-101"
                && record["event"] == "cycles:uk").unwrap();
        cycles["counter-value"] = json!("<not counted>");
        cycles["event-runtime"] = json!(0);
        let result = validate_and_aggregate(doc.to_string(), vec![], &threads, &events);
        let message = result.unwrap_err().to_string();
        assert!(message.contains("missing ACTIVE-thread counter"), "{message}");
    }

    #[test]
    fn zero_task_clock_with_positive_cycles_is_not_inactive() {
        let (raw, threads, events) = fixture();
        let mut doc: Value = serde_json::from_str(&raw).unwrap();
        let clock = doc["records"].as_array_mut().unwrap().iter_mut()
            .find(|record| record["thread"] == "threadance-102"
                && record["event"] == "task-clock").unwrap();
        clock["counter-value"] = json!("0");
        let cycles = doc["records"].as_array_mut().unwrap().iter_mut()
            .find(|record| record["thread"] == "threadance-102"
                && record["event"] == "cycles:uk").unwrap();
        cycles["counter-value"] = json!("5");
        cycles["event-runtime"] = json!(400000);
        let result = validate_and_aggregate(doc.to_string(), vec![], &threads, &events);
        assert!(result.is_err());
    }

    #[test]
    fn missing_worker_event_must_fail() {
        let (raw, threads, events) = fixture();
        let mut doc: Value = serde_json::from_str(&raw).unwrap();
        doc["records"].as_array_mut().unwrap().retain(|record| {
            !(record["thread"] == "threadance-102" && record["event"] == "cycles:uk")
        });
        let result = validate_and_aggregate(doc.to_string(), vec![], &threads, &events);
        assert!(result.unwrap_err().to_string().contains("missing per-thread records"));
    }
}
