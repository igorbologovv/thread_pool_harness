use std::ops::Range;

use rand::{RngExt, SeedableRng, rngs::StdRng, seq::SliceRandom};
use solana_bls_signatures::{
    keypair::Keypair,
    pubkey::{PopVerified, PubkeyAffine, PubkeyProjective, VerifySignature},
    signature::{Signature, SignatureProjective},
};

use super::{CommonWorkloadConfig, Workload};

/// Pure BLS aggregate-signature verification workload.
///
/// Dataset generation happens outside the measured interval.
///
/// One work unit contains one or more independent certificate verifications.
/// This allows task granularity to be varied without changing the scheduler
/// implementations.
pub struct BlsAggregateVerifyWorkload {
    validator_pubkeys: Vec<PopVerified<PubkeyAffine>>,
    inputs: Vec<BlsVerifyInput>,
    work_units: Vec<Range<usize>>,
}

#[derive(Debug)]
pub struct BlsAggregateVerifyConfig {
    /// Total validator set size.
    pub validators: usize,

    /// Number of validators represented in each aggregate signature.
    pub signers_per_certificate: usize,

    /// Number of certificate verifications grouped into one work unit.
    pub certificates_per_work_unit: usize,
}

struct BlsVerifyInput {
    payload: [u8; 32],
    signature: Signature,
    signer_indices: Vec<usize>,
}

impl Workload for BlsAggregateVerifyWorkload {
    type WorkUnit = Range<usize>;
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
        assert!(
            config.certificates_per_work_unit > 0,
            "certificates per work unit must be greater than zero"
        );

        let total_certificates = common
            .work_units
            .checked_mul(config.certificates_per_work_unit)
            .expect("total certificate count overflowed usize");

        let mut rng = StdRng::seed_from_u64(common.seed);

        let keypairs: Vec<Keypair> = (0..config.validators)
            .map(|_| {
                let ikm = rng.random::<[u8; 32]>();

                Keypair::derive(&ikm).expect("failed to derive deterministic BLS keypair")
            })
            .collect();

        let validator_pubkeys = keypairs.iter().map(|keypair| keypair.public).collect();

        let inputs = (0..total_certificates)
            .map(|_| {
                let payload = rng.random::<[u8; 32]>();

                let mut signer_indices: Vec<usize> = (0..config.validators).collect();

                signer_indices.shuffle(&mut rng);
                signer_indices.truncate(config.signers_per_certificate);
                signer_indices.sort_unstable();

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

        drop(keypairs);

        let work_units = (0..common.work_units)
            .map(|work_unit_index| {
                let start = work_unit_index * config.certificates_per_work_unit;
                let end = start + config.certificates_per_work_unit;

                start..end
            })
            .collect();

        Self {
            validator_pubkeys,
            inputs,
            work_units,
        }
    }

    fn work_units(&self) -> &[Self::WorkUnit] {
        &self.work_units
    }

    fn execute(&self, unit: &Self::WorkUnit) {
        for input_index in unit.clone() {
            let input = &self.inputs[input_index];

            // Internal BLS aggregation remains sequential. The scheduler under
            // test is responsible for parallelism between work units.
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
        }
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
            certificates_per_work_unit: 2,
        };

        let workload = BlsAggregateVerifyWorkload::generate(&common, &config);

        assert_eq!(workload.work_units().len(), 4);
        assert_eq!(workload.work_units(), &[0..2, 2..4, 4..6, 6..8]);

        for unit in workload.work_units() {
            workload.execute(unit);
        }
    }
}
