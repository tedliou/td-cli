//! The commands that never talk to the Daemon: the Command schemas, the Operator catalog and
//! opening a saved project.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use super::options::invalid;
use super::{Context, text};
use crate::args::Values;
use crate::catalog;
use crate::client::{ClientError, Result, details};
use crate::launch::launch_detached;

/// List Command names, or one Command's input JSON Schema.
pub fn commands_schema(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let commands = catalog::catalog()["commands"]
        .as_object()
        .expect("catalog commands");
    let Some(name) = text(values, "name") else {
        let names: Vec<&String> = commands.keys().collect();
        context.printer.emit(json!({"commands": names}), None);
        return Ok(());
    };
    let command = commands.get(name).ok_or_else(invalid)?;
    let data = json!({
        "name": name,
        "effect": command["effect"],
        "batchable": command["batchable"],
        "input_schema": command["input_schema"],
    });
    context.printer.emit(data, None);
    Ok(())
}

const FAMILIES: &[&str] = &["TOP", "CHOP", "DAT", "SOP", "POP", "COMP", "MAT"];

/// Query the bundled create-support catalog, sorted by Operator type.
pub fn ops_types(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let family = text(values, "family");
    if family.is_some_and(|family| !FAMILIES.contains(&family)) {
        return Err(invalid());
    }
    let manifest = catalog::operator_catalog();
    let by_type: BTreeMap<&str, &Value> = manifest["operators"]
        .as_array()
        .expect("catalog operators")
        .iter()
        .map(|entry| (entry["op_type"].as_str().expect("op_type"), entry))
        .collect();
    let name = text(values, "name");
    let entries: Vec<&Value> = by_type
        .into_iter()
        .filter(|(op_type, _)| name.is_none_or(|name| name == *op_type))
        .map(|(_, entry)| entry)
        .filter(|entry| family.is_none_or(|family| entry["family"] == family))
        .collect();
    let limit = values["limit"].as_u64().expect("limit has a default") as usize;
    let data = json!({
        "touchdesigner_build": manifest["touchdesigner_build"],
        "operators": entries.iter().take(limit).collect::<Vec<_>>(),
        "total": entries.len(),
        "truncated": entries.len() > limit,
    });
    context.printer.emit(data, None);
    Ok(())
}

/// Open a saved project independently; the PID returned does not mean the Agent is online.
pub fn project_open(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let path = Path::new(text(values, "path").expect("required argument"));
    let executable = Path::new(text(values, "executable").expect("required option"));
    let executable_name = executable.file_name().map(|name| name.to_string_lossy());
    if suffix(path).to_lowercase() != ".toe"
        || executable_name.is_none_or(|name| name.to_lowercase() != "touchdesigner.exe")
    {
        return Err(invalid());
    }
    let project = resolve(path);
    let command = [
        resolve(executable).to_string_lossy().into_owned(),
        project.to_string_lossy().into_owned(),
    ];
    let command: Vec<&str> = command.iter().map(String::as_str).collect();
    let cwd = project.parent().unwrap_or(&project);
    let pid = launch_detached(&command, cwd, context.timeout).map_err(|error| {
        ClientError::with(
            "process_launch_failed",
            details([("reason", Value::String(error.0))]),
        )
    })?;
    let data = json!({
        "pid": pid,
        "path": project.to_string_lossy(),
        "status": "launched",
    });
    context.printer.emit(data, None);
    Ok(())
}

/// `PurePath.suffix`: from the last dot of the name, unless the name starts or ends with it.
fn suffix(path: &Path) -> String {
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    match name.rfind('.') {
        Some(index) if index > 0 && index < name.len() - 1 => name[index..].to_string(),
        _ => String::new(),
    }
}

/// `Path.resolve()`: the final path of an existing file, without the `\\?\` prefix, or the
/// absolute path when it cannot be read.
fn resolve(path: &Path) -> PathBuf {
    let Ok(resolved) = std::fs::canonicalize(path) else {
        return std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    };
    let text = resolved.to_string_lossy();
    if let Some(share) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{share}"));
    }
    match text.strip_prefix(r"\\?\") {
        Some(local) => PathBuf::from(local),
        None => resolved,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suffixes_follow_pure_path() {
        for (path, expected) in [
            ("a.toe", ".toe"),
            (r"C:\x\b.TOE", ".TOE"),
            ("a.b.toe", ".toe"),
            (".toe", ""),
            ("a.", ""),
            ("a", ""),
            ("dir/a.toe/", ".toe"),
        ] {
            assert_eq!(suffix(Path::new(path)), expected, "{path}");
        }
    }
}
