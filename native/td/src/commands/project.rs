//! The `project`, `binary`, `batch` and `events` commands that submit a Command.

use serde_json::{Map, Value, json};

use super::options::{Builder, Dedicated, Options, require};

pub fn command(group: &str, path: &[&str]) -> Option<(&'static str, Builder)> {
    let entry: (&'static str, Builder) = match (group, path) {
        ("project", ["metadata"]) => ("project.metadata", |_| Ok(Some(json!({})))),
        ("project", ["snapshot"]) => ("project.snapshot", snapshot),
        ("project", ["save"]) => ("project.save", save),
        ("binary", ["export"]) => ("binary.export", export),
        ("batch", ["execute"]) => ("batch.execute", |_| Ok(None)),
        ("events", ["read"]) => ("events.read", events),
        _ => return None,
    };
    Some(entry)
}

fn snapshot(options: &Options) -> Dedicated {
    let mut refinements = Map::new();
    if options.flag("paginate") {
        refinements.insert("paginate".into(), Value::Bool(true));
    }
    if options.given("cursor") {
        refinements.insert("cursor".into(), options.value("cursor"));
    }
    if options.flag("include_connections") {
        refinements.insert("include_connections".into(), Value::Bool(true));
    }
    let has_path = options.given("operator_path");
    require(has_path || refinements.is_empty())?;
    if !has_path {
        return Ok(None);
    }
    let mut input = options.object(&["operator_path", "max_depth", "max_operators"]);
    input.extend(refinements);
    Ok(Some(Value::Object(input)))
}

fn save(options: &Options) -> Dedicated {
    let fields = ["expected_path", "expected_sha256"];
    Ok(options
        .any_given(&fields)
        .then(|| Value::Object(options.object(&fields))))
}

/// Without both the Operator and the format, only `--input` can describe the export.
fn export(options: &Options) -> Dedicated {
    let complete = options.given("operator_path") && options.given("format");
    Ok(complete.then(|| Value::Object(options.object(&["operator_path", "format", "max_bytes"]))))
}

fn events(options: &Options) -> Dedicated {
    Ok(Some(Value::Object(options.object(&[
        "after",
        "limit",
        "include_errors",
    ]))))
}
