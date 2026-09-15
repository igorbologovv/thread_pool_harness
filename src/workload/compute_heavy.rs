use std::hint::black_box;

use nalgebra::SMatrix;
use rand::{RngExt, SeedableRng, rngs::StdRng};

use super::{CommonWorkloadConfig, Workload};

const MATRIX_SIZE: usize = 8;
const MATRIX_SCALE: f64 = 1.0 / MATRIX_SIZE as f64;

type Matrix = SMatrix<f64, MATRIX_SIZE, MATRIX_SIZE>;

/// Configuration specific to the compute-heavy workload.
#[derive(Debug)]
pub struct ComputeHeavyConfig {
    /// Number of matrix-multiplication rounds performed by each work unit.
    ///
    /// Increasing this value increases work-unit granularity without
    /// increasing the working-set size.
    pub rounds: usize,
}

/// Synthetic compute-heavy workload.
///
/// The workload consists of independent work units operating on small dense
/// matrices. The matrices are intentionally small so that the working set
/// remains cache-resident while repeated multiplication produces a large
/// amount of arithmetic work.
pub struct ComputeHeavyWorkload {
    work_units: Vec<ComputeHeavyWorkUnit>,
    rounds: usize,
}

/// One independently schedulable unit of compute-heavy work.
#[derive(Debug)]
pub struct ComputeHeavyWorkUnit {
    left: Matrix,
    right: Matrix,
}

impl Workload for ComputeHeavyWorkload {
    type WorkUnit = ComputeHeavyWorkUnit;
    type Config = ComputeHeavyConfig;

    fn generate(common: &CommonWorkloadConfig, config: &Self::Config) -> Self {
        assert!(
            common.work_units > 0,
            "number of work units must be greater than zero"
        );

        assert!(
            config.rounds > 0,
            "number of compute rounds must be greater than zero"
        );

        let mut rng = StdRng::seed_from_u64(common.seed);

        let work_units = (0..common.work_units)
            .map(|_| ComputeHeavyWorkUnit {
                left: random_matrix(&mut rng),
                right: random_matrix(&mut rng),
            })
            .collect();

        Self {
            work_units,
            rounds: config.rounds,
        }
    }

    fn work_units(&self) -> &[Self::WorkUnit] {
        &self.work_units
    }

    fn execute(&self, unit: &Self::WorkUnit) -> u64 {
        let mut current = unit.left;

        for _ in 0..self.rounds {
            let product = current * unit.right;
            current = product * MATRIX_SCALE + unit.left;
        }

        let current = black_box(current);

        current[(0, 0)].to_bits()
    }
}

fn random_matrix(rng: &mut StdRng) -> Matrix {
    Matrix::from_fn(|_, _| rng.random_range(-1.0..1.0))
}
