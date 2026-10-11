use std::{
    fs::{self, File},
    io::{self, BufReader, BufWriter, Read, Write},
    ops::Range,
    path::{Path, PathBuf},
};

use rand::{RngExt, SeedableRng, rngs::StdRng, seq::SliceRandom};
use solana_bls_signatures::{
    keypair::Keypair,
    pubkey::{PopVerified, PubkeyAffine, PubkeyProjective, VerifySignature},
    signature::{Signature, SignatureProjective},
};

use super::{CommonWorkloadConfig, Workload};

const DATASET_CACHE_VERSION: u32 = 1;
const DATASET_MAGIC: &[u8; 8] = b"TBLSDS01";

/// Pure BLS aggregate-signature verification workload.
///
/// Dataset generation/loading happens outside the measured interval.
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

impl BlsAggregateVerifyWorkload {
    /// Load the exact deterministic dataset from disk if it already exists.
    ///
    /// On a cache miss, generate it once and atomically persist it.
    ///
    /// The cache key contains every workload-generation parameter plus an
    /// explicit format/generator version. If generation semantics change,
    /// DATASET_CACHE_VERSION must be incremented.
    pub fn load_or_generate_cached(
        common: &CommonWorkloadConfig,
        config: &BlsAggregateVerifyConfig,
        cache_dir: &Path,
    ) -> Self {
        validate_config(common, config);

        fs::create_dir_all(cache_dir).unwrap_or_else(|error| {
            panic!(
                "failed to create BLS dataset cache directory {}: {error}",
                cache_dir.display()
            )
        });

        let path = cache_path(cache_dir, common, config);

        if path.exists() {
            match load_cache(&path, common, config) {
                Ok(workload) => {
                    println!("BLS dataset cache hit:");
                    println!("  {}", path.display());
                    println!();

                    return workload;
                }

                Err(error) => {
                    eprintln!(
                        "WARNING: BLS dataset cache is invalid: {}: {error}",
                        path.display()
                    );
                    eprintln!("Regenerating it.");
                }
            }
        } else {
            println!("BLS dataset cache miss:");
            println!("  {}", path.display());
            println!("Generating dataset once...");
        }

        let (workload, validator_ikms) = generate_dataset(common, config);

        save_cache(&path, common, config, &validator_ikms, &workload.inputs).unwrap_or_else(
            |error| {
                panic!(
                    "failed to save BLS dataset cache {}: {error}",
                    path.display()
                )
            },
        );

        println!("Saved BLS dataset cache:");
        println!("  {}", path.display());
        println!();

        workload
    }
}

impl Workload for BlsAggregateVerifyWorkload {
    type WorkUnit = Range<usize>;
    type Config = BlsAggregateVerifyConfig;

    fn generate(common: &CommonWorkloadConfig, config: &Self::Config) -> Self {
        validate_config(common, config);
        generate_dataset(common, config).0
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

fn validate_config(common: &CommonWorkloadConfig, config: &BlsAggregateVerifyConfig) {
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
}

fn cache_path(
    cache_dir: &Path,
    common: &CommonWorkloadConfig,
    config: &BlsAggregateVerifyConfig,
) -> PathBuf {
    cache_dir.join(format!(
        "bls-v{}-v{}-s{}-c{}-wu{}-seed{}.bin",
        DATASET_CACHE_VERSION,
        config.validators,
        config.signers_per_certificate,
        config.certificates_per_work_unit,
        common.work_units,
        common.seed,
    ))
}

fn generate_dataset(
    common: &CommonWorkloadConfig,
    config: &BlsAggregateVerifyConfig,
) -> (BlsAggregateVerifyWorkload, Vec<[u8; 32]>) {
    let total_certificates = common
        .work_units
        .checked_mul(config.certificates_per_work_unit)
        .expect("total certificate count overflowed usize");

    let mut rng = StdRng::seed_from_u64(common.seed);

    // Keep the exact deterministic input key material so the same public keys
    // can be reconstructed cheaply when the cached dataset is loaded.
    let validator_ikms: Vec<[u8; 32]> = (0..config.validators)
        .map(|_| rng.random::<[u8; 32]>())
        .collect();

    let keypairs: Vec<Keypair> = validator_ikms
        .iter()
        .map(|ikm| Keypair::derive(ikm).expect("failed to derive deterministic BLS keypair"))
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

    let work_units = build_work_units(common, config);

    (
        BlsAggregateVerifyWorkload {
            validator_pubkeys,
            inputs,
            work_units,
        },
        validator_ikms,
    )
}

fn build_work_units(
    common: &CommonWorkloadConfig,
    config: &BlsAggregateVerifyConfig,
) -> Vec<Range<usize>> {
    (0..common.work_units)
        .map(|work_unit_index| {
            let start = work_unit_index * config.certificates_per_work_unit;

            let end = start + config.certificates_per_work_unit;

            start..end
        })
        .collect()
}

fn save_cache(
    path: &Path,
    common: &CommonWorkloadConfig,
    config: &BlsAggregateVerifyConfig,
    validator_ikms: &[[u8; 32]],
    inputs: &[BlsVerifyInput],
) -> io::Result<()> {
    let temporary_path = path.with_extension(format!("tmp-{}", std::process::id()));

    let file = File::create(&temporary_path)?;
    let mut writer = BufWriter::new(file);

    writer.write_all(DATASET_MAGIC)?;

    write_u32(&mut writer, DATASET_CACHE_VERSION)?;

    write_u64(&mut writer, usize_to_u64(config.validators)?)?;
    write_u64(&mut writer, usize_to_u64(config.signers_per_certificate)?)?;
    write_u64(
        &mut writer,
        usize_to_u64(config.certificates_per_work_unit)?,
    )?;
    write_u64(&mut writer, usize_to_u64(common.work_units)?)?;
    write_u64(&mut writer, common.seed)?;
    write_u64(&mut writer, usize_to_u64(inputs.len())?)?;

    for ikm in validator_ikms {
        writer.write_all(ikm)?;
    }

    for input in inputs {
        writer.write_all(&input.payload)?;

        // Signature is the crate's 192-byte serialized representation.
        writer.write_all(&input.signature.0)?;

        write_u64(&mut writer, usize_to_u64(input.signer_indices.len())?)?;

        for &index in &input.signer_indices {
            write_u64(&mut writer, usize_to_u64(index)?)?;
        }
    }

    writer.flush()?;

    let file = writer.into_inner().map_err(|error| error.into_error())?;

    file.sync_all()?;

    fs::rename(&temporary_path, path)?;

    Ok(())
}

fn load_cache(
    path: &Path,
    common: &CommonWorkloadConfig,
    config: &BlsAggregateVerifyConfig,
) -> io::Result<BlsAggregateVerifyWorkload> {
    let file = File::open(path)?;
    let mut reader = BufReader::new(file);

    let mut magic = [0_u8; 8];
    reader.read_exact(&mut magic)?;

    if &magic != DATASET_MAGIC {
        return Err(invalid_data("invalid dataset cache magic"));
    }

    let version = read_u32(&mut reader)?;

    if version != DATASET_CACHE_VERSION {
        return Err(invalid_data(format!("unsupported cache version {version}")));
    }

    let validators = u64_to_usize(read_u64(&mut reader)?)?;
    let signers_per_certificate = u64_to_usize(read_u64(&mut reader)?)?;
    let certificates_per_work_unit = u64_to_usize(read_u64(&mut reader)?)?;
    let work_units = u64_to_usize(read_u64(&mut reader)?)?;
    let seed = read_u64(&mut reader)?;
    let input_count = u64_to_usize(read_u64(&mut reader)?)?;

    if validators != config.validators
        || signers_per_certificate != config.signers_per_certificate
        || certificates_per_work_unit != config.certificates_per_work_unit
        || work_units != common.work_units
        || seed != common.seed
    {
        return Err(invalid_data(
            "dataset cache metadata does not match requested workload",
        ));
    }

    let expected_inputs = common
        .work_units
        .checked_mul(config.certificates_per_work_unit)
        .ok_or_else(|| invalid_data("input count overflow"))?;

    if input_count != expected_inputs {
        return Err(invalid_data(format!(
            "cached input count {input_count} != expected {expected_inputs}"
        )));
    }

    let mut validator_ikms = Vec::with_capacity(config.validators);

    for _ in 0..config.validators {
        let mut ikm = [0_u8; 32];
        reader.read_exact(&mut ikm)?;
        validator_ikms.push(ikm);
    }

    // Re-deriving only the validator keys is much cheaper than generating
    // every individual certificate signature again.
    let keypairs: Vec<Keypair> = validator_ikms
        .iter()
        .map(|ikm| Keypair::derive(ikm).expect("cached deterministic BLS key material is valid"))
        .collect();

    let validator_pubkeys = keypairs.iter().map(|keypair| keypair.public).collect();

    drop(keypairs);

    let mut inputs = Vec::with_capacity(input_count);

    for _ in 0..input_count {
        let mut payload = [0_u8; 32];
        reader.read_exact(&mut payload)?;

        let mut signature_bytes = [0_u8; 192];
        reader.read_exact(&mut signature_bytes)?;

        let signer_count = u64_to_usize(read_u64(&mut reader)?)?;

        if signer_count != config.signers_per_certificate {
            return Err(invalid_data(format!(
                "cached signer count {signer_count} != expected {}",
                config.signers_per_certificate
            )));
        }

        let mut signer_indices = Vec::with_capacity(signer_count);

        for _ in 0..signer_count {
            let index = u64_to_usize(read_u64(&mut reader)?)?;

            if index >= config.validators {
                return Err(invalid_data(format!(
                    "cached signer index {index} is out of range"
                )));
            }

            signer_indices.push(index);
        }

        inputs.push(BlsVerifyInput {
            payload,
            signature: Signature(signature_bytes),
            signer_indices,
        });
    }

    // Reject trailing bytes. This catches some accidental/corrupt cache files.
    let mut trailing = [0_u8; 1];

    if reader.read(&mut trailing)? != 0 {
        return Err(invalid_data(
            "dataset cache contains unexpected trailing bytes",
        ));
    }

    Ok(BlsAggregateVerifyWorkload {
        validator_pubkeys,
        inputs,
        work_units: build_work_units(common, config),
    })
}

fn write_u32<W: Write>(writer: &mut W, value: u32) -> io::Result<()> {
    writer.write_all(&value.to_le_bytes())
}

fn write_u64<W: Write>(writer: &mut W, value: u64) -> io::Result<()> {
    writer.write_all(&value.to_le_bytes())
}

fn read_u32<R: Read>(reader: &mut R) -> io::Result<u32> {
    let mut bytes = [0_u8; 4];
    reader.read_exact(&mut bytes)?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_u64<R: Read>(reader: &mut R) -> io::Result<u64> {
    let mut bytes = [0_u8; 8];
    reader.read_exact(&mut bytes)?;
    Ok(u64::from_le_bytes(bytes))
}

fn usize_to_u64(value: usize) -> io::Result<u64> {
    u64::try_from(value).map_err(|_| invalid_data("usize does not fit into u64"))
}

fn u64_to_usize(value: u64) -> io::Result<usize> {
    usize::try_from(value).map_err(|_| invalid_data("u64 does not fit into usize"))
}

fn invalid_data(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
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

    #[test]
    fn cached_dataset_round_trip_verifies() {
        let common = CommonWorkloadConfig {
            work_units: 3,
            seed: 456,
        };

        let config = BlsAggregateVerifyConfig {
            validators: 8,
            signers_per_certificate: 6,
            certificates_per_work_unit: 2,
        };

        let directory = std::env::temp_dir().join(format!(
            "threadance-bls-cache-test-{}-{}",
            std::process::id(),
            common.seed,
        ));

        let _ = fs::remove_dir_all(&directory);

        let generated =
            BlsAggregateVerifyWorkload::load_or_generate_cached(&common, &config, &directory);

        for unit in generated.work_units() {
            generated.execute(unit);
        }

        let loaded =
            BlsAggregateVerifyWorkload::load_or_generate_cached(&common, &config, &directory);

        for unit in loaded.work_units() {
            loaded.execute(unit);
        }

        let cache_file = cache_path(&directory, &common, &config);

        assert!(cache_file.exists());

        fs::remove_dir_all(directory).unwrap();
    }
}
