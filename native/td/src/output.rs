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
            let request = error
                .details
                .get("request")
                .filter(|request| request.is_object())
                .map(request_summary);
            let mut body = Map::new();
            body.insert("code".into(), Value::String(error.code.clone()));
            body.insert("message".into(), Value::String(error.code.clone()));
            body.insert("details".into(), Value::Object(error.details));
            body.insert(
                "retryable".into(),
                Value::Bool(catalog::is_retryable(&error.code)),
            );
            let mut envelope = Map::new();
            envelope.insert(
                "protocol_version".into(),
                catalog::protocol_version().clone(),
            );
            envelope.insert("error".into(), Value::Object(body));
            if let Some(request) = request {
                envelope.insert("request".into(), request);
            }
            echo(Stream::Out, &compact(&Value::Object(envelope)));
        }
        echo(Stream::Err, &error.code);
        code
    }
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
