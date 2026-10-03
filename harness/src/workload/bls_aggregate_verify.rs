use rand::{RngExt, SeedableRng, rngs::StdRng, seq::SliceRandom};
use solana_bls_signatures::{
    keypair::Keypair,
    pubkey::{PopVerified, PubkeyAffine, PubkeyProjective, VerifySignature},
    signature::{Signature, SignatureProjective},
};

use super::{CommonWorkloadConfig, Workload};

/// Pure BLS aggregate-signature verification workload.
///
/// This workload intentionally excludes Agave-specific Bank, stake, certificate,
/// and bitmap-decoding logic.
///
/// Dataset generation happens outside the measured interval.
///
/// Each measured work unit:
///
/// 1. selects the public keys belonging to the certificate signers;
/// 2. aggregates those public keys;
/// 3. verifies one aggregate BLS signature against the payload.
pub struct BlsAggregateVerifyWorkload {
    validator_pubkeys: Vec<PopVerified<PubkeyAffine>>,
    inputs: Vec<BlsVerifyInput>,
    work_units: Vec<usize>,
}

#[derive(Debug)]
pub struct BlsAggregateVerifyConfig {
    /// Total validator set size.
    pub validators: usize,

    /// Number of validators represented in each aggregate signature.
    pub signers_per_certificate: usize,
}

struct BlsVerifyInput {
    payload: [u8; 32],
    signature: Signature,
    signer_indices: Vec<usize>,
}

impl Workload for BlsAggregateVerifyWorkload {
    type WorkUnit = usize;
    type Config = BlsAggregateVerifyConfig;

    fn generate(common: &CommonWorkloadConfig, config: &Self::Config) -> Self {
        assert!(
            common.work_units > 0,
            "number of work units must be greater than zero"
        );

        assert!(
            config.validators > 0,
            "validator count must be greater than zero"
        );

        assert!(
            config.signers_per_certificate > 0,
            "signer count must be greater than zero"
        );

        assert!(
            config.signers_per_certificate <= config.validators,
            "signer count cannot exceed validator count"
        );

        let mut rng = StdRng::seed_from_u64(common.seed);

        // Generate one deterministic validator set.
        //
        // Secret keys are needed only while constructing valid aggregate
        // signatures. They are discarded before benchmark execution.
        let keypairs: Vec<Keypair> = (0..config.validators)
            .map(|_| {
                let ikm = rng.random::<[u8; 32]>();

                Keypair::derive(&ikm).expect("failed to derive deterministic BLS keypair")
            })
            .collect();

        let validator_pubkeys = keypairs.iter().map(|keypair| keypair.public).collect();

        let inputs = (0..common.work_units)
            .map(|_| {
                let payload = rng.random::<[u8; 32]>();

                // Randomize the signer subset for every certificate.
                let mut signer_indices: Vec<usize> = (0..config.validators).collect();

                signer_indices.shuffle(&mut rng);
                signer_indices.truncate(config.signers_per_certificate);

                // A real signer bitmap is traversed in rank order.
                // Sorting gives the same stable ordering without introducing
                // bitmap decoding into this isolated crypto workload.
                signer_indices.sort_unstable();

                // Certificate creation happens outside the benchmark.
                //
                // Every selected validator signs the same payload and the
                // individual signatures are aggregated into one signature.
                let signatures: Vec<SignatureProjective> = signer_indices
                    .iter()
                    .map(|&index| keypairs[index].sign(&payload))
                    .collect();

                let aggregate_signature = SignatureProjective::aggregate(signatures.iter())
                    .expect("failed to aggregate BLS signatures");

                BlsVerifyInput {
                    payload,
                    signature: aggregate_signature.into(),
                    signer_indices,
                }
            })
            .collect();

        // Secret keys and individual signatures are dropped here.
        drop(keypairs);

        let work_units = (0..common.work_units).collect();

        Self {
            validator_pubkeys,
            inputs,
            work_units,
        }
    }

    fn work_units(&self) -> &[Self::WorkUnit] {
        &self.work_units
    }

    fn execute(&self, unit: &Self::WorkUnit) -> u64 {
        let input = &self.inputs[*unit];

        // This is intentionally sequential.
        //
        // The outer Scheduler under test is responsible for all parallelism.
        let aggregate_pubkey = PubkeyProjective::aggregate(
            input
                .signer_indices
                .iter()
                .map(|&index| &self.validator_pubkeys[index]),
        )
        .expect("failed to aggregate BLS public keys");

        let valid = aggregate_pubkey
            .verify_signature(&input.signature, &input.payload)
            .is_ok();

        assert!(valid, "generated BLS aggregate signature must be valid");

        // Deterministic control value. Using payload bytes rather than simply
        // returning 1 makes missing/duplicated work easier to detect.
        u64::from_le_bytes(
            input.payload[..8]
                .try_into()
                .expect("payload must contain at least 8 bytes"),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_aggregate_signatures_verify() {
        let common = CommonWorkloadConfig {
            work_units: 4,
            seed: 123,
        };

        let config = BlsAggregateVerifyConfig {
            validators: 8,
            signers_per_certificate: 6,
        };

        let workload = BlsAggregateVerifyWorkload::generate(&common, &config);

        assert_eq!(workload.work_units().len(), 4);

        for unit in workload.work_units() {
            workload.execute(unit);
        }
    }
}
