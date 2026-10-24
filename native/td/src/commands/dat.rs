//! The `dat` and `chop` commands: read and write Operator data.

use serde_json::{Map, Value};

use super::options::{Builder, Dedicated, Options, Parsed, input_default, json_option, require};

pub fn command(group: &str, path: &[&str]) -> Option<(&'static str, Builder)> {
    let entry: (&'static str, Builder) = match (group, path) {
        ("dat", ["text", "get"]) => ("dat.text.get", text_get),
        ("dat", ["text", "set"]) => ("dat.text.set", text_set),
        ("dat", ["table", "get"]) => ("dat.table.get", table_get),
        ("dat", ["table", "replace"]) => ("dat.table.replace", table_replace),
        ("dat", ["table", "patch"]) => ("dat.table.patch", table_patch),
        ("chop", ["values", "get"]) => ("chop.values.get", values_get),
        _ => return None,
    };
    Some(entry)
}

fn with_path(options: &Options) -> Map<String, Value> {
    options.object(&["operator_path"])
}

fn text_get(options: &Options) -> Dedicated {
    let has_path = options.given("operator_path");
    require(has_path || !options.any_given(&["max_bytes", "offset"]))?;
    if !has_path {
        return Ok(None);
    }
    let mut input = with_path(options);
    input.insert(
        "max_bytes".into(),
        options.or("max_bytes", input_default("max_dat_content_bytes")),
    );
    if options.given("offset") {
        input.insert("offset".into(), options.value("offset"));
    }
    Ok(Some(Value::Object(input)))
}

fn text_set(options: &Options) -> Dedicated {
    let paired = options.together(&["operator_path", "text"])?;
    Ok(paired.then(|| Value::Object(options.object(&["operator_path", "text"]))))
}

const TABLE_WINDOW: &[&str] = &[
    "row_offset",
    "column_offset",
    "row_count",
    "column_count",
    "max_bytes",
];

fn table_get(options: &Options) -> Dedicated {
    let has_path = options.given("operator_path");
    require(has_path || !options.any_given(TABLE_WINDOW))?;
    if !has_path {
        return Ok(None);
    }
    let mut input = with_path(options);
    input.insert("row_offset".into(), options.or("row_offset", 0));
    input.insert("column_offset".into(), options.or("column_offset", 0));
    input.insert("row_count".into(), options.or("row_count", 64));
    input.insert("column_count".into(), options.or("column_count", 64));
    input.insert(
        "max_bytes".into(),
        options.or("max_bytes", input_default("max_dat_content_bytes")),
    );
    Ok(Some(Value::Object(input)))
}

/// `--rows` as JSON: any list, as the Python command checks it.
fn rows(options: &Options) -> Parsed {
    json_option(options, "rows", Value::is_array)
}

fn table_replace(options: &Options) -> Dedicated {
    options.together(&["operator_path", "rows"])?;
    let rows = rows(options)?;
    let Some(rows) = rows.filter(|_| options.given("operator_path")) else {
        return Ok(None);
    };
    let mut input = with_path(options);
    input.insert("rows".into(), rows);
    Ok(Some(Value::Object(input)))
}

fn table_patch(options: &Options) -> Dedicated {
    let paired = options.given("operator_path") == options.given("rows");
    let refined = options.any_given(&["row_offset", "column_offset"]);
    require(paired && (options.given("operator_path") || !refined))?;
    let rows = rows(options)?;
    let Some(rows) = rows.filter(|_| options.given("operator_path")) else {
        return Ok(None);
    };
    let mut input = with_path(options);
    input.insert("row_offset".into(), options.or("row_offset", 0));
    input.insert("column_offset".into(), options.or("column_offset", 0));
    input.insert("rows".into(), rows);
    Ok(Some(Value::Object(input)))
}

fn values_get(options: &Options) -> Dedicated {
    let has_path = options.given("operator_path");
    require(has_path || !options.any_given(&["channels", "sample_offset", "sample_count"]))?;
    if !has_path {
        return Ok(None);
    }
    let mut input = with_path(options);
    if let Some(channels) = options.0.get("channels").and_then(Value::as_str) {
        input.insert("channels".into(), split_commas(channels));
    }
    for name in ["sample_offset", "sample_count"] {
        if options.given(name) {
            input.insert(name.into(), options.value(name));
        }
    }
    Ok(Some(Value::Object(input)))
}

/// Python `text.split(",")`.
pub fn split_commas(text: &str) -> Value {
    Value::Array(text.split(',').map(Value::from).collect())
}
