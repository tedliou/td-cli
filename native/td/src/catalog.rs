//! The command tree, Command schemas and error codes exported from the Python authority.

use std::sync::OnceLock;

use serde_json::Value;

const CATALOG: &str = include_str!("../catalog.json");

pub fn catalog() -> &'static Value {
    static PARSED: OnceLock<Value> = OnceLock::new();
    PARSED.get_or_init(|| serde_json::from_str(CATALOG).expect("embedded catalog is valid JSON"))
}

/// The locked-build create-support catalog, embedded from the Python package itself.
const OPERATORS: &str =
    include_str!("../../../src/td_cli/data/touchdesigner-2025.32050-operators.json");

pub fn operator_catalog() -> &'static Value {
    static PARSED: OnceLock<Value> = OnceLock::new();
    PARSED.get_or_init(|| {
        serde_json::from_str(OPERATORS).expect("embedded operator catalog is valid JSON")
    })
}

pub fn release_version() -> &'static str {
    catalog()["release_version"]
        .as_str()
        .expect("release_version")
}

pub fn protocol_version() -> &'static Value {
    &catalog()["protocol_version"]
}

fn listed(list: &Value, item: &str) -> bool {
    list.as_array()
        .into_iter()
        .flatten()
        .any(|known| known == item)
}

pub fn is_known_error(code: &str) -> bool {
    listed(&catalog()["errors"]["codes"], code)
}

pub fn is_retryable(code: &str) -> bool {
    listed(&catalog()["errors"]["retryable"], code)
}

pub fn exit_code(code: &str) -> i32 {
    let errors = &catalog()["errors"];
    let code = errors["exit_codes"][code]
        .as_i64()
        .or_else(|| errors["default_exit_code"].as_i64());
    code.expect("default_exit_code") as i32
}

pub fn is_request_status(status: &str) -> bool {
    listed(&catalog()["request_statuses"], status)
}

pub fn is_terminal_status(status: &str) -> bool {
    listed(&catalog()["terminal_request_statuses"], status)
}
