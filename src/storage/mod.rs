//! Persistent storage for benchmark configurations and results.
//!
//! Storage is performed outside the measured execution interval so that
//! database operations do not affect benchmark measurements.

pub mod sqlite;
