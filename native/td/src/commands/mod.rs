//! The `td` commands. Each one reads its parsed values, talks to the Daemon through one
//! client, and prints through the shared printer.

use std::io::Read;

use serde_json::{Map, Value, json};

use crate::args::{Invocation, Values};
use crate::catalog;
use crate::client::{ClientError, DaemonClient, Result};
use crate::output::Printer;

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

pub fn run(invocation: Invocation) -> i32 {
    let globals = &invocation.globals;
    let mut context = Context {
        printer: Printer {
            json: globals["as_json"] == true,
        },
        timeout: globals["timeout"].as_f64().expect("timeout is a float"),
        instance: globals["instance"].as_str().map(String::from),
    };
    let path: Vec<&str> = invocation.path.iter().map(String::as_str).collect();
    let values = &invocation.values;
    let outcome = match path.as_slice() {
        ["version"] => version(&mut context, values),
        ["instances", "list"] => instances_list(&context, values),
        ["instances", "get"] => instances_get(&context, values),
        ["requests", "get"] => requests_get(&context, values),
        ["requests", "wait"] => requests_wait(&context, values),
        ["ops", "get"] => {
            let dedicated =
                text(values, "operator_path").map(|path| json!({"operator_path": path}));
            submit_command(&context, "ops.get", dedicated, values)
        }
        _ => {
            crate::output::echo(
                crate::output::Stream::Err,
                "not yet available in the native td",
            );
            return 5;
        }
    };
    match outcome {
        Ok(()) => 0,
        Err(error) => context.printer.fail(error),
    }
}

fn text<'a>(values: &'a Values, name: &str) -> Option<&'a str> {
    values.get(name).and_then(Value::as_str)
}

fn version(context: &mut Context, values: &Values) -> Result<()> {
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
    context.printer.json |= values["as_json"] == true;
    context.printer.emit(data, None);
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
    succeeded(&snapshot)?;
    context.printer.emit(snapshot, None);
    Ok(())
}

/// A settled Request that did not succeed fails the command with its own error code.
fn succeeded(snapshot: &Value) -> Result<()> {
    if snapshot["status"] == "succeeded" {
        return Ok(());
    }
    let code = match snapshot.get("error").filter(|error| !is_falsy(error)) {
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

/// Python truthiness, for `snapshot.get("error") or default`.
fn is_falsy(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Bool(flag) => !flag,
        Value::Number(number) => number.as_f64() == Some(0.0),
        Value::String(text) => text.is_empty(),
        Value::Array(items) => items.is_empty(),
        Value::Object(entries) => entries.is_empty(),
    }
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
    if !rest.chars().all(python_space) || !value.is_object() {
        return Err(invalid());
    }
    Ok(value)
}

/// `str.isspace`, which also counts the ASCII file, group, record and unit separators.
fn python_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
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
    let mut snapshot = client.submit(&request_id, command, context.instance.as_deref())?;
    if !no_wait {
        let admitted = snapshot["request_id"]
            .as_str()
            .ok_or_else(|| ClientError::new("protocol_incompatible"))?
            .to_string();
        snapshot = client.wait(&admitted)?;
        succeeded(&snapshot)?;
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
}
