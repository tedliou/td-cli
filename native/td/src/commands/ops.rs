//! The `ops` commands: each builds its dedicated Command input as the Python command does.
//! `None` means no dedicated option was given, so `--input` or `--input-file` must be.

use serde_json::{Map, Value, json};

use super::options::{Builder, Dedicated, Options, input_default, invalid, refined, require};

/// The Command and input builder for an `ops` command path.
pub fn command(path: &[&str]) -> Option<(&'static str, Builder)> {
    let entry: (&'static str, Builder) = match path {
        ["get"] => ("ops.get", operator_only),
        ["inspect"] => ("ops.inspect", inspect),
        ["state", "get"] => ("ops.state.get", operator_only),
        ["state", "set"] => ("ops.state.set", state_set),
        ["children"] => ("ops.children", children),
        ["connections"] => ("ops.connections", connections),
        ["hierarchy", "connections"] => ("ops.hierarchy.connections", connections),
        ["hierarchy", "connect"] => ("ops.hierarchy.connect", connect),
        ["hierarchy", "disconnect"] => ("ops.hierarchy.disconnect", disconnect),
        ["create"] => ("ops.create", create),
        ["connect"] => ("ops.connect", connect),
        ["rename"] => ("ops.rename", rename),
        ["destroy"] => ("ops.destroy", destroy),
        ["copy"] => ("ops.copy", |options| destination(options, "include_docked")),
        ["move"] => ("ops.move", |options| {
            destination(options, "allow_connected")
        }),
        ["tox", "import"] => ("ops.tox.import", tox_import),
        ["disconnect"] => ("ops.disconnect", disconnect),
        _ => return None,
    };
    Some(entry)
}

fn object(map: Map<String, Value>) -> Option<Value> {
    Some(Value::Object(map))
}

fn operator_only(options: &Options) -> Dedicated {
    Ok(options
        .given("operator_path")
        .then(|| json!({"operator_path": options.value("operator_path")})))
}

fn inspect(options: &Options) -> Dedicated {
    if !refined(options, &["max_items"])? {
        return Ok(None);
    }
    Ok(Some(json!({
        "operator_path": options.value("operator_path"),
        "max_items": options.or("max_items", input_default("max_inspection_items")),
    })))
}

const STATE_FIELDS: &[&str] = &[
    "node_x",
    "node_y",
    "node_width",
    "node_height",
    "color",
    "comment",
    "bypass",
    "lock",
    "viewer",
    "expose",
    "display",
    "render",
];

fn state_set(options: &Options) -> Dedicated {
    if !refined(options, STATE_FIELDS)? {
        return Ok(None);
    }
    let mut input = options.object(&["operator_path"]);
    input.extend(options.object(STATE_FIELDS));
    if let Value::Array(color) = options.value("color") {
        input["color"] = json!({"red": color[0], "green": color[1], "blue": color[2]});
    }
    Ok(object(input))
}

fn children(options: &Options) -> Dedicated {
    if !refined(options, &["op_type"])? {
        return Ok(None);
    }
    Ok(object(options.object(&["operator_path", "op_type"])))
}

fn connections(options: &Options) -> Dedicated {
    if !refined(options, &["max_connections"])? {
        return Ok(None);
    }
    Ok(Some(json!({
        "operator_path": options.value("operator_path"),
        "max_connections": options.or("max_connections", 256),
    })))
}

/// Source and target come together; indices and `--replace` need them.
fn connector(options: &Options, with_replace: bool) -> Dedicated {
    let paired = options.together(&["source_path", "target_path"])?;
    let replace = with_replace && options.flag("replace");
    require(paired || !(options.any_given(&["output_index", "input_index"]) || replace))?;
    if !paired {
        return Ok(None);
    }
    let mut input = options.object(&["source_path", "target_path"]);
    input.insert("output_index".into(), options.or_zero("output_index"));
    input.insert("input_index".into(), options.or_zero("input_index"));
    if with_replace {
        input.insert("replace".into(), Value::Bool(replace));
    }
    Ok(object(input))
}

fn connect(options: &Options) -> Dedicated {
    connector(options, true)
}

fn disconnect(options: &Options) -> Dedicated {
    connector(options, false)
}

fn create(options: &Options) -> Dedicated {
    let complete = options.together(&["parent_path", "op_type", "name"])?;
    let refined = options.any_given(&["node_x", "node_y"]) || options.flag("allow_conditional");
    require(options.given("parent_path") || !refined)?;
    if !complete {
        return Ok(None);
    }
    let mut input = options.object(&["parent_path", "op_type", "name"]);
    input.insert("node_x".into(), options.or_zero("node_x"));
    input.insert("node_y".into(), options.or_zero("node_y"));
    input.insert(
        "allow_conditional".into(),
        Value::Bool(options.flag("allow_conditional")),
    );
    Ok(object(input))
}

fn rename(options: &Options) -> Dedicated {
    let paired = options.together(&["operator_path", "new_name"])?;
    Ok(paired.then(|| Value::Object(options.object(&["operator_path", "new_name"]))))
}

fn destroy(options: &Options) -> Dedicated {
    let refined = options.flag("recursive")
        || options.flag("allow_connected")
        || options.any_given(&["max_operators", "inventory"]);
    require(options.given("operator_path") || !refined)?;
    if !options.given("operator_path") {
        return Ok(None);
    }
    let mut input = options.object(&["operator_path"]);
    input.insert("recursive".into(), Value::Bool(options.flag("recursive")));
    input.insert(
        "allow_connected".into(),
        Value::Bool(options.flag("allow_connected")),
    );
    input.insert("max_operators".into(), options.or("max_operators", 256));
    if options.given("inventory") {
        input.insert("inventory".into(), options.value("inventory"));
    }
    Ok(object(input))
}

/// `ops copy` and `ops move`: the destination fields come together, the rest refine them.
fn destination(options: &Options, specific: &str) -> Dedicated {
    let complete = options.together(&["source_path", "target_parent_path", "new_name"])?;
    let refined = options.any_given(&["node_x", "node_y", "max_operators", "inventory"])
        || options.flag(specific);
    require(options.given("source_path") || !refined)?;
    if !complete {
        return Ok(None);
    }
    let mut input = options.object(&[
        "source_path",
        "target_parent_path",
        "new_name",
        "node_x",
        "node_y",
    ]);
    input.insert("max_operators".into(), options.or("max_operators", 256));
    if options.given("inventory") {
        input.insert("inventory".into(), options.value("inventory"));
    }
    input.insert(specific.into(), Value::Bool(options.flag(specific)));
    Ok(object(input))
}

fn tox_import(options: &Options) -> Dedicated {
    let identity = ["parent_path", "tox_path", "allowlist_root", "target_name"];
    let complete = options.together(&identity)?;
    let refined = options.flag("trusted")
        || options.flag("replace")
        || options.any_given(&["max_file_bytes", "max_operators", "root_child", "inventory"]);
    if refined && !options.given("parent_path") {
        return Err(invalid());
    }
    if !complete {
        return Ok(None);
    }
    let mut input = options.object(&identity);
    input.insert("trusted".into(), Value::Bool(options.flag("trusted")));
    input.insert("replace".into(), Value::Bool(options.flag("replace")));
    input.insert(
        "max_file_bytes".into(),
        options.or("max_file_bytes", input_default("max_tox_file_bytes")),
    );
    input.insert("max_operators".into(), options.or("max_operators", 256));
    input.insert("root_child".into(), options.value("root_child"));
    input.insert("inventory".into(), options.or("inventory", "full"));
    Ok(object(input))
}
