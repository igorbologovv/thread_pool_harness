use std::fmt;

use crate::result::RunResult;

/// Summary statistics calculated from a series of measured benchmark runs.
pub struct RunSummary {
    runs: usize,
    mean_elapsed_ns: f64,
    median_elapsed_ns: f64,
    std_dev_elapsed_ns: Option<f64>,
    min_elapsed_ns: u64,
    max_elapsed_ns: u64,
    median_throughput: f64,
    checksum: u64,
}

impl RunSummary {
    pub fn from_results(results: &[RunResult]) -> Self {
        assert!(
            !results.is_empty(),
            "cannot calculate summary from empty benchmark results"
        );

        let runs = results.len();

        let mean_elapsed_ns = results
            .iter()
            .map(|result| result.elapsed_ns as f64)
            .sum::<f64>()
            / runs as f64;

        let std_dev_elapsed_ns = if runs > 1 {
            let variance = results
                .iter()
                .map(|result| {
                    let difference = result.elapsed_ns as f64 - mean_elapsed_ns;
                    difference * difference
                })
                .sum::<f64>()
                / (runs - 1) as f64;

            Some(variance.sqrt())
        } else {
            None
        };

        let mut elapsed_times: Vec<u64> = results.iter().map(|result| result.elapsed_ns).collect();

        elapsed_times.sort_unstable();

        let median_elapsed_ns = median_u64(&elapsed_times);
        let min_elapsed_ns = elapsed_times[0];
        let max_elapsed_ns = elapsed_times[runs - 1];

        let mut throughputs: Vec<f64> = results
            .iter()
            .map(|result| result.work_units_per_second)
            .collect();

        throughputs.sort_by(f64::total_cmp);

        let median_throughput = median_f64(&throughputs);

        Self {
            runs,
            mean_elapsed_ns,
            median_elapsed_ns,
            std_dev_elapsed_ns,
            min_elapsed_ns,
            max_elapsed_ns,
            median_throughput,
            checksum: results[0].checksum,
        }
    }
}

impl fmt::Display for RunSummary {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "Runs              : {}", self.runs)?;
        writeln!(
            f,
            "Mean elapsed      : {:.3} ms",
            self.mean_elapsed_ns / 1_000_000.0
        )?;
        writeln!(
            f,
            "Median elapsed    : {:.3} ms",
            self.median_elapsed_ns / 1_000_000.0
        )?;

        match self.std_dev_elapsed_ns {
            Some(std_dev) => {
                writeln!(f, "Std deviation     : {:.3} ms", std_dev / 1_000_000.0)?;
            }
            None => {
                writeln!(f, "Std deviation     : N/A")?;
            }
        }

        writeln!(
            f,
            "Min elapsed       : {:.3} ms",
            self.min_elapsed_ns as f64 / 1_000_000.0
        )?;
        writeln!(
            f,
            "Max elapsed       : {:.3} ms",
            self.max_elapsed_ns as f64 / 1_000_000.0
        )?;
        writeln!(
            f,
            "Median throughput : {:.2} work units/s",
            self.median_throughput
        )?;
        write!(f, "Checksum          : {}", self.checksum)
    }
}

fn median_u64(values: &[u64]) -> f64 {
    let middle = values.len() / 2;

    if values.len().is_multiple_of(2) {
        (values[middle - 1] as f64 + values[middle] as f64) / 2.0
    } else {
        values[middle] as f64
    }
}

fn median_f64(values: &[f64]) -> f64 {
    let middle = values.len() / 2;

    if values.len().is_multiple_of(2) {
        (values[middle - 1] + values[middle]) / 2.0
    } else {
        values[middle]
    }
}
