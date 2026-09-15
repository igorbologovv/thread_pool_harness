use std::hint::black_box;

use nalgebra::SMatrix;
use rand::{RngExt, SeedableRng, rngs::StdRng};

use super::{CommonWorkloadConfig, Workload};

const MATRIX_SIZE: usize = 8;

/// Scales matrix products to keep values numerically bounded across many rounds.
///
/// Each output element of an NxN matrix multiplication is a sum of N products,
/// so scaling by 1/N helps prevent values from growing toward infinity.
const MATRIX_SCALE: f64 = 1.0 / MATRIX_SIZE as f64;

type Matrix = SMatrix<f64, MATRIX_SIZE, MATRIX_SIZE>;

/// Configuration specific to the compute-heavy workload.
#[derive(Debug)]
pub struct ComputeHeavyConfig {
    /// Number of matrix-multiplication rounds performed by each work unit.
    ///
    /// Increasing this value increases the compute depth of each work unit
    /// without increasing its input size.
    pub rounds: usize,
}

/// Synthetic workload intended to produce compute-intensive CPU work.
///
/// The workload consists of independent work units operating on small dense
/// matrices. Repeated multiplication increases the amount of arithmetic work
/// performed on each work unit while keeping its input size fixed.
pub struct ComputeHeavyWorkload {
    work_units: Vec<ComputeHeavyWorkUnit>,
    rounds: usize,
}

/// One independently processable unit of compute-heavy work.
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
    // Clippy prefers value-based matrix operations here, but release assembly
    // showed that this introduces a full 512-byte copy of `unit.right` on
    // every round. References are used deliberately to avoid that hot-path copy.
    #[allow(clippy::op_ref)]
    fn execute(&self, unit: &Self::WorkUnit) -> u64 {
        let mut current = unit.left;

        for _ in 0..self.rounds {
            // Use reference-based matrix operations deliberately. Passing the
            // matrices by value can introduce full 512-byte matrix copies in
            // the generated hot loop.
            let product = &current * &unit.right;

            current = product * MATRIX_SCALE + &unit.left;
        }

        // Make the complete result observable before extracting the cheap
        // control value used by the scheduler-level checksum.
        let current = black_box(current);

        current[(0, 0)].to_bits()
    }
}

fn random_matrix(rng: &mut StdRng) -> Matrix {
    Matrix::from_fn(|_, _| rng.random_range(-1.0..1.0))
}
