//! The `parameters` commands.

use serde_json::{Map, Value, json};

use super::dat::split_commas;
use super::options::{Builder, Dedicated, Options, invalid, json_option, list_of, require};

pub fn command(path: &[&str]) -> Option<(&'static str, Builder)> {
    let entry: (&'static str, Builder) = match path {
        ["get"] => ("parameters.get", parameter_only),
        ["list"] => ("parameters.list", list),
        ["pulse"] => ("parameters.pulse", parameter_only),
        ["set"] => ("parameters.set", set),
        ["page-create"] => ("parameters.page.create", |_| Ok(None)),
        ["menu-set"] => ("parameters.menu.set", menu_set),
        ["sequence-get"] => ("parameters.sequence.get", sequence_get),
        ["sequence-replace"] => ("parameters.sequence.replace", sequence_replace),
        _ => return None,
    };
    Some(entry)
}

fn parameter_only(options: &Options) -> Dedicated {
    let paired = options.together(&["operator_path", "parameter"])?;
    Ok(paired.then(|| Value::Object(options.object(&["operator_path", "parameter"]))))
}

fn list(options: &Options) -> Dedicated {
    let mut refinements = Map::new();
    if let Some(names) = options.0.get("names").and_then(Value::as_str) {
        refinements.insert("names".into(), split_commas(names));
    }
    if options.flag("include_values") {
        refinements.insert("include_values".into(), Value::Bool(true));
    }
    if options.flag("non_default_only") {
        refinements.insert("non_default_only".into(), Value::Bool(true));
    }
    if options.flag("no_menu_options") {
        refinements.insert("include_menu_options".into(), Value::Bool(false));
    }
    let has_path = options.given("operator_path");
    require(has_path || refinements.is_empty())?;
    if !has_path {
        return Ok(None);
    }
    let mut input = options.object(&["operator_path"]);
    input.extend(refinements);
    Ok(Some(Value::Object(input)))
}

const VALUE_OPTIONS: &[&str] = &[
    "bool_value",
    "integer",
    "number",
    "string",
    "operator_value",
    "operators_json",
    "expression",
    "export_source_operator",
    "export_channel",
    "bind_source_operator",
    "bind_parameter",
];

/// Exactly one value mode: a constant, an expression, an export or a bind.
fn set(options: &Options) -> Dedicated {
    let any_dedicated =
        options.any_given(&["operator_path", "parameter"]) || options.any_given(VALUE_OPTIONS);
    let mut values: Vec<(&str, Value)> = ["integer", "number", "string"]
        .iter()
        .filter(|name| options.given(name))
        .map(|name| ("constant", options.value(name)))
        .collect();
    if let Some(flag) = options.0.get("bool_value").and_then(Value::as_str) {
        require(matches!(flag, "true" | "false"))?;
        values.push(("constant", Value::Bool(flag == "true")));
    }
    if options.given("expression") {
        values.push(("expression", options.value("expression")));
    }
    if options.given("operator_value") {
        values.push(("constant", options.value("operator_value")));
    }
    if let Some(operators) = json_option(options, "operators_json", |value| {
        list_of(value, Value::is_string)
    })? {
        values.push(("constant", operators));
    }
    if options.any_given(&["export_source_operator", "export_channel"]) {
        require(options.given("export_source_operator") && options.given("export_channel"))?;
        let source = json!({
            "kind": "export_channel",
            "operator_path": options.value("export_source_operator"),
            "channel": options.value("export_channel"),
        });
        values.push(("export", source));
    }
    if options.any_given(&["bind_source_operator", "bind_parameter"]) {
        require(options.given("bind_source_operator") && options.given("bind_parameter"))?;
        let source = json!({
            "kind": "bind_parameter",
            "operator_path": options.value("bind_source_operator"),
            "parameter": options.value("bind_parameter"),
        });
        values.push(("bind", source));
    }
    let complete = options.given("operator_path") && options.given("parameter");
    if any_dedicated && !(complete && values.len() == 1) {
        return Err(invalid());
    }
    let Some((mode, value)) = values.pop().filter(|_| complete) else {
        return Ok(None);
    };
    let mut input = options.object(&["operator_path", "parameter"]);
    input.insert("mode".into(), Value::String(mode.into()));
    let key = if matches!(mode, "export" | "bind") {
        "source"
    } else {
        "value"
    };
    input.insert(key.into(), value);
    Ok(Some(Value::Object(input)))
}

fn menu_set(options: &Options) -> Dedicated {
    let strings = |value: &Value| list_of(value, Value::is_string);
    let names = json_option(options, "names_json", strings)?;
    let labels = json_option(options, "labels_json", strings)?;
    let mut fields = Map::new();
    fields.insert("operator_path".into(), options.value("operator_path"));
    fields.insert("parameter".into(), options.value("parameter"));
    fields.insert("menu_names".into(), names.unwrap_or(Value::Null));
    fields.insert("menu_labels".into(), labels.unwrap_or(Value::Null));
    fields.insert("preserve".into(), options.value("preserve"));
    let provided = fields.values().filter(|value| !value.is_null()).count();
    if provided == 0 {
        return Ok(None);
    }
    require(provided == fields.len())?;
    Ok(Some(Value::Object(fields)))
}

fn sequence_get(options: &Options) -> Dedicated {
    let paired = options.together(&["operator_path", "sequence"])?;
    Ok(paired.then(|| {
        Value::Object(options.object(&[
            "operator_path",
            "sequence",
            "max_blocks",
            "max_parameters",
        ]))
    }))
}

fn sequence_replace(options: &Options) -> Dedicated {
    let blocks = json_option(options, "blocks_json", |value| {
        list_of(value, Value::is_object)
    })?;
    let has_identity = options.any_given(&["operator_path", "sequence"]);
    let complete = options.given("operator_path") && options.given("sequence");
    if !has_identity && blocks.is_none() {
        return Ok(None);
    }
    let blocks = blocks.filter(|_| complete).ok_or_else(invalid)?;
    let mut input = options.object(&["operator_path", "sequence"]);
    input.insert("blocks".into(), blocks);
    Ok(Some(Value::Object(input)))
}
