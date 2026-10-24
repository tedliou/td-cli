//! What `td` prints: the Protocol v3 envelopes, the human JSON, and the error code line.
//! Lines end with CRLF on Windows, as Python's text-mode streams write them.

use std::io::Write;

use serde_json::{Map, Value};

use crate::catalog;
use crate::client::ClientError;
use crate::json::{compact, pretty};

pub enum Stream {
    Out,
    Err,
}

/// `typer.echo(text)`: the text and a newline, with every newline written as the platform's.
pub fn echo(stream: Stream, text: &str) {
    let mut line = text.to_string();
    line.push('\n');
    if cfg!(windows) {
        line = line.replace('\n', "\r\n");
    }
    // A closed pipe is the reader's choice; there is nobody left to tell.
    let _ = match stream {
        Stream::Out => std::io::stdout().lock().write_all(line.as_bytes()),
        Stream::Err => std::io::stderr().lock().write_all(line.as_bytes()),
    };
}

pub struct Printer {
    pub json: bool,
}

impl Printer {
    pub fn emit(&self, data: Value, request: Option<&Value>) {
        if !self.json {
            echo(Stream::Out, &pretty(&data));
            return;
        }
        let mut envelope = Map::new();
        envelope.insert(
            "protocol_version".into(),
            catalog::protocol_version().clone(),
        );
        envelope.insert("data".into(), data);
        if let Some(request) = request {
            envelope.insert("request".into(), request_summary(request));
        }
        echo(Stream::Out, &compact(&Value::Object(envelope)));
    }

    /// Print the failure and return its exit code.
    pub fn fail(&self, error: ClientError) -> i32 {
        let code = catalog::exit_code(&error.code);
        if self.json {
            echo(
                Stream::Out,
                &compact(&error_envelope(&error.code, error.details)),
            );
        }
        echo(Stream::Err, &error.code);
        code
    }
}

/// The Protocol v3 error envelope; a Request snapshot in the details is summarised beside it.
pub fn error_envelope(code: &str, details: Map<String, Value>) -> Value {
    let request = details
        .get("request")
        .filter(|request| request.is_object())
        .map(request_summary);
    let mut body = Map::new();
    body.insert("code".into(), Value::String(code.into()));
    body.insert("message".into(), Value::String(code.into()));
    body.insert("details".into(), Value::Object(details));
    body.insert("retryable".into(), Value::Bool(catalog::is_retryable(code)));
    let mut envelope = Map::new();
    envelope.insert(
        "protocol_version".into(),
        catalog::protocol_version().clone(),
    );
    envelope.insert("error".into(), Value::Object(body));
    if let Some(request) = request {
        envelope.insert("request".into(), request);
    }
    Value::Object(envelope)
}

fn request_summary(snapshot: &Value) -> Value {
    let mut summary = Map::new();
    summary.insert(
        "request_id".into(),
        snapshot.get("request_id").cloned().unwrap_or(Value::Null),
    );
    summary.insert(
        "status".into(),
        snapshot.get("status").cloned().unwrap_or(Value::Null),
    );
    Value::Object(summary)
}
