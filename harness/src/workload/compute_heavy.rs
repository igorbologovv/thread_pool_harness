use std::{hint::black_box, ops::Range};

use nalgebra::SMatrix;
use rand::{RngExt, SeedableRng, rngs::StdRng};

use super::{CommonWorkloadConfig, Workload};

const MATRIX_SIZE: usize = 8;

type Matrix = SMatrix<f64, MATRIX_SIZE, MATRIX_SIZE>;

/// Configuration specific to the compute-heavy workload.
#[derive(Debug)]
pub struct ComputeHeavyConfig {
    /// Number of independent matrix multiplications contained in one work unit.
    ///
    /// Increasing this value increases work-unit granularity: each work unit
    /// contains more useful computation before the scheduler reaches the next
    /// independently processable unit.
    pub operations_per_work_unit: usize,
}

/// Synthetic workload intended to provide controlled CPU-intensive work.
///
/// Each useful operation multiplies one independent pair of dense 8x8 matrices.
///
/// Work units group several independent matrix multiplications together.
/// Therefore:
///
/// total_operations = work_units * operations_per_work_unit
///
/// The total number of matrix multiplications in one benchmark run is:
///
///     work_units * operations_per_work_unit
///
/// In granularity experiments, these two parameters can be varied together
/// so that the total amount of useful computation remains unchanged.
pub struct ComputeHeavyWorkload {
    matrix_pairs: Vec<MatrixPair>,
    work_units: Vec<Range<usize>>,
}

/// Input for one independent matrix-multiplication operation.
#[derive(Debug)]
struct MatrixPair {
    left: Matrix,
    right: Matrix,
}

impl Workload for ComputeHeavyWorkload {
    type WorkUnit = Range<usize>;
    type Config = ComputeHeavyConfig;

    fn generate(common: &CommonWorkloadConfig, config: &Self::Config) -> Self {
        assert!(
            common.work_units > 0,
            "number of work units must be greater than zero"
        );

        assert!(
            config.operations_per_work_unit > 0,
            "operations per work unit must be greater than zero"
        );

        let total_operations = common
            .work_units
            .checked_mul(config.operations_per_work_unit)
            .expect("total operation count overflowed usize");

        let mut rng = StdRng::seed_from_u64(common.seed);

        let matrix_pairs = (0..total_operations)
            .map(|_| MatrixPair {
                left: random_matrix(&mut rng),
                right: random_matrix(&mut rng),
            })
            .collect();

        let work_units = (0..common.work_units)
            .map(|work_unit_index| {
                let start = work_unit_index * config.operations_per_work_unit;

                let end = start + config.operations_per_work_unit;

                start..end
            })
            .collect();

        Self {
            matrix_pairs,
            work_units,
        }
    }

    fn work_units(&self) -> &[Self::WorkUnit] {
        &self.work_units
    }

    /// Executes every independent matrix multiplication belonging to one
    /// workload unit.
    ///
    /// Matrix operands are passed by reference deliberately. Previous release
    /// assembly inspection showed that value-based operations can introduce
    /// unnecessary full-matrix copies in the hot path.
    ///
    /// Every matrix product contributes to a full-matrix accumulator. The
    /// accumulator is passed through `black_box` once after all operations in the
    /// work unit, preventing dead-code elimination without forcing a full-matrix
    /// copy for every individual operation.
    #[allow(clippy::op_ref)]
    fn execute(&self, unit: &Self::WorkUnit) -> u64 {
        let mut accumulator = Matrix::zeros();
        let mut checksum = 0u64;

        for pair in &self.matrix_pairs[unit.clone()] {
            let product = &pair.left * &pair.right;

            checksum = checksum.wrapping_add(product[(0, 0)].to_bits());

            accumulator += &product;
        }

        black_box(&accumulator);

        checksum
    }
}

fn random_matrix(rng: &mut StdRng) -> Matrix {
    Matrix::from_fn(|_, _| rng.random_range(-1.0..1.0))
}
