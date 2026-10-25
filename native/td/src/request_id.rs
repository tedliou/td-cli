//! New Request IDs: UUIDv7, laid out like the Python client's `_uuid7`.

use std::collections::hash_map::RandomState;
use std::hash::BuildHasher;
use std::time::{SystemTime, UNIX_EPOCH};

/// 48 bits of Unix milliseconds, version 7, 12 random bits, variant `10`, 62 random bits.
pub fn new() -> String {
    let milliseconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| since.as_millis());
    let random = (u128::from(random_u64()) << 64) | u128::from(random_u64());
    let mut value = (milliseconds & ((1 << 48) - 1)) << 80;
    value |= 0x7 << 76;
    value |= ((random >> 64) & 0xfff) << 64;
    value |= 0b10 << 62;
    value |= random & ((1 << 62) - 1);
    let hex = format!("{value:032x}");
    format!(
        "{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..]
    )
}

/// std seeds each `RandomState` from the operating system's random source, so hashing with
/// a fresh one gives unpredictable bits without another dependency.
fn random_u64() -> u64 {
    RandomState::new().hash_one(0u8)
}

#[cfg(test)]
mod tests {
    #[test]
    fn ids_are_distinct_version_7_uuids() {
        let first = super::new();
        let second = super::new();
        assert_ne!(first, second);
        assert_eq!(first.len(), 36);
        assert_eq!(&first[14..15], "7");
        assert!(matches!(&first[19..20], "8" | "9" | "a" | "b"));
    }
}
