//! The `td` commands. Each one reads its parsed values, talks to the Daemon through one
//! client, and prints through the shared printer.

use std::io::Read;

use serde_json::{Map, Value, json};

mod dat;
mod execute;
mod local;
mod ops;
mod options;
mod parameters;
mod project;

use options::{Builder, Options};

use crate::args::{Invocation, Values};
use crate::catalog;
use crate::client::{ClientError, DaemonClient, Result, Target};
use crate::output::Printer;
use crate::python;

pub struct Context {
    pub printer: Printer,
    pub timeout: f64,
    pub instance: Option<String>,
}

impl Context {
    fn client(&self) -> DaemonClient {
        DaemonClient::new(self.timeout, true)
    }

    fn reject_instance_on_query(&self) -> Result<()> {
        match self.instance {
            Some(_) => Err(ClientError::new("invalid_arguments")),
            None => Ok(()),
        }
    }
}

type Handler = Box<dyn Fn(&Context, &Values) -> Result<()>>;

pub fn run(invocation: Invocation) -> i32 {
    let globals = &invocation.globals;
    let context = Context {
        printer: Printer {
            json: globals["as_json"] == true,
        },
        timeout: globals["timeout"].as_f64().expect("timeout is a float"),
        instance: globals["instance"].as_str().map(String::from),
    };
    let path: Vec<&str> = invocation.path.iter().map(String::as_str).collect();
    let handler = handler(&path).expect("every catalog command has a handler");
    match handler(&context, &invocation.values) {
        Ok(()) => 0,
        Err(error) => context.printer.fail(error),
    }
}

/// What runs a command path: a query, a local command, or a Command submission.
fn handler(path: &[&str]) -> Option<Handler> {
    let fixed: fn(&Context, &Values) -> Result<()> = match path {
        ["version"] => version,
        ["instances", "list"] => instances_list,
        ["instances", "get"] => instances_get,
        ["requests", "get"] => requests_get,
        ["requests", "wait"] => requests_wait,
        ["commands", "schema"] => local::commands_schema,
        ["commands", "execute"] => execute::commands_execute,
        ["ops", "types"] => local::ops_types,
        ["project", "open"] => local::project_open,
        [group, rest @ ..] => {
            let (name, build) = submission(group, rest)?;
            return Some(Box::new(move |context, values| {
                build(&Options(values))
                    .and_then(|dedicated| submit_command(context, name, dedicated, values))
            }));
        }
        [] => return None,
    };
    Some(Box::new(fixed))
}

/// The Command a submitting `td` command sends, and how it builds the dedicated input.
fn submission(group: &str, rest: &[&str]) -> Option<(&'static str, Builder)> {
    match group {
        "ops" => ops::command(rest),
        "parameters" => parameters::command(rest),
        "dat" | "chop" => dat::command(group, rest),
        _ => project::command(group, rest),
    }
}

fn text<'a>(values: &'a Values, name: &str) -> Option<&'a str> {
    values.get(name).and_then(Value::as_str)
}

fn version(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let mut client = DaemonClient::new(context.timeout, false);
    let daemon_version = match client.health() {
        Ok(health) => health
            .get("release_version")
            .cloned()
            .unwrap_or(Value::Null),
        Err(error)
            if matches!(
                error.code.as_str(),
                "daemon_unavailable" | "transport_error"
            ) =>
        {
            Value::Null
        }
        Err(error) => return Err(error),
    };
    let data = json!({
        "release_version": catalog::release_version(),
        "protocol_versions": [catalog::protocol_version()],
        "daemon_release_version": daemon_version,
    });
    let printer = Printer {
        json: context.printer.json || values["as_json"] == true,
    };
    printer.emit(data, None);
    Ok(())
}

fn instances_list(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let mut items = context.client().instances()?;
    if let Some(status) = text(values, "status") {
        if !matches!(status, "online" | "offline" | "draining") {
            return Err(ClientError::new("invalid_arguments"));
        }
        let kept = items
            .as_array()
            .expect("instances are a list")
            .iter()
            .filter(|item| item["status"] == status);
        items = Value::Array(kept.cloned().collect());
    }
    context.printer.emit(items, None);
    Ok(())
}

fn instances_get(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let instance = context
        .client()
        .select_instance(text(values, "selector"), false)?;
    context.printer.emit(instance, None);
    Ok(())
}

fn requests_get(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let request_id = text(values, "request_id").expect("required argument");
    let snapshot = context.client().get_request(request_id, false)?;
    context.printer.emit(snapshot, None);
    Ok(())
}

fn requests_wait(context: &Context, values: &Values) -> Result<()> {
    context.reject_instance_on_query()?;
    let request_id = text(values, "request_id").expect("required argument");
    let snapshot = context.client().wait(request_id)?;
    fail_unless_succeeded(&snapshot)?;
    context.printer.emit(snapshot, None);
    Ok(())
}

/// A settled Request that did not succeed fails the command with its own error code.
fn fail_unless_succeeded(snapshot: &Value) -> Result<()> {
    if snapshot["status"] == "succeeded" {
        return Ok(());
    }
    let code = match snapshot
        .get("error")
        .filter(|error| !python::is_falsy(error))
    {
        Some(error) => match &error["code"] {
            Value::String(code) => code.clone(),
            other => crate::json::compact(other),
        },
        None => "internal_error".into(),
    };
    let mut details = Map::new();
    details.insert("request".into(), snapshot.clone());
    Err(ClientError::with(&code, details))
}

/// The Command input from exactly one of: the dedicated options, `--input`, `--input-file`.
fn command_input(dedicated: Option<Value>, values: &Values) -> Result<Value> {
    let inline = text(values, "input");
    let input_file = text(values, "input_file");
    let modes = usize::from(dedicated.is_some())
        + usize::from(inline.is_some())
        + usize::from(input_file.is_some());
    if modes != 1 {
        return Err(ClientError::new("invalid_arguments"));
    }
    if let Some(dedicated) = dedicated {
        return Ok(dedicated);
    }
    if let Some(inline) = inline {
        return json_document(inline);
    }
    let input_file = input_file.expect("one mode is set");
    let mut bytes = Vec::new();
    let read = if input_file == "-" {
        std::io::stdin().lock().read_to_end(&mut bytes).map(drop)
    } else {
        std::fs::read(input_file).map(|content| bytes = content)
    };
    read.map_err(|_| ClientError::new("invalid_arguments"))?;
    let text = String::from_utf8(bytes).map_err(|_| ClientError::new("invalid_arguments"))?;
    json_document(&text)
}

/// One JSON object starting at the first character, optionally followed by whitespace.
pub fn json_document(text: &str) -> Result<Value> {
    let invalid = || ClientError::new("invalid_arguments");
    if text.is_empty() || text.starts_with(['\u{feff}', ' ', '\t', '\n', '\r']) {
        return Err(invalid());
    }
    let mut stream = serde_json::Deserializer::from_str(text).into_iter::<Value>();
    let value = stream.next().ok_or_else(invalid)?.map_err(|_| invalid())?;
    let rest = &text[stream.byte_offset()..];
    if !rest.chars().all(python::is_space) || !value.is_object() {
        return Err(invalid());
    }
    Ok(value)
}

fn submit_command(
    context: &Context,
    name: &str,
    dedicated: Option<Value>,
    values: &Values,
) -> Result<()> {
    let payload = command_input(dedicated, values)?;
    let no_wait = values["no_wait"] == true;
    let request_id = text(values, "request_id").map_or_else(crate::request_id::new, String::from);
    let mut client = context.client();
    // The Daemon validates the Command before it resolves the Instance selector.
    let command = json!({"name": name, "input": payload});
    let target = context.instance.as_deref().map(Target::Selector);
    let mut snapshot = client.submit(&request_id, command, target)?;
    if !no_wait {
        let admitted = snapshot["request_id"]
            .as_str()
            .ok_or_else(|| ClientError::new("protocol_incompatible"))?
            .to_string();
        snapshot = client.wait(&admitted)?;
        fail_unless_succeeded(&snapshot)?;
    }
    let data = if no_wait {
        snapshot.clone()
    } else {
        snapshot.get("result").cloned().unwrap_or(Value::Null)
    };
    context.printer.emit(data, Some(&snapshot));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_documents_follow_python_raw_decode() {
        assert_eq!(json_document("{\"a\":1} \n").unwrap(), json!({"a": 1}));
        for text in ["", " {}", "\u{feff}{}", "[]", "{} x", "{", "{}{}"] {
            assert!(json_document(text).is_err(), "{text:?}");
        }
    }

    fn leaves(node: &Value, path: &mut Vec<String>, found: &mut Vec<Vec<String>>) {
        match node.get("commands").and_then(Value::as_object) {
            Some(commands) => {
                for (name, child) in commands {
                    path.push(name.clone());
                    leaves(child, path, found);
                    path.pop();
                }
            }
            None => found.push(path.clone()),
        }
    }

    #[test]
    fn every_catalog_command_has_a_handler() {
        let mut found = Vec::new();
        leaves(&catalog::catalog()["cli"], &mut Vec::new(), &mut found);
        assert!(found.len() > 40);
        for path in found {
            let path: Vec<&str> = path.iter().map(String::as_str).collect();
            assert!(handler(&path).is_some(), "{path:?}");
        }
    }
}
