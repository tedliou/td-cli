//! `commands execute`: run a plan of independent Commands in order, one Request each, with
//! JSONL progress on stdout and a stop at the first failure.

use std::io::Read;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use super::options::invalid;
use super::{Context, fail_unless_succeeded, text};
use crate::args::Values;
use crate::client::{ClientError, DaemonClient, Result, Target};
use crate::json::compact;
use crate::output::{Stream, echo};

/// A plan is at most this much UTF-8, without a byte order mark.
const MAX_PLAN_BYTES: u64 = 1024 * 1024;

pub fn commands_execute(context: &Context, values: &Values) -> Result<()> {
    let path = text(values, "input_file").expect("required option");
    let mut client = context.client();
    let commands = read_plan(path, &mut client)?;
    run_plan(
        &commands,
        &mut client,
        context.instance.as_deref(),
        context.timeout,
    )
}

/// Read a bounded plan file and have the Daemon validate all of it before anything runs.
fn read_plan(path: &str, client: &mut DaemonClient) -> Result<Vec<Value>> {
    let mut document = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(MAX_PLAN_BYTES + 1).read_to_end(&mut document))
        .map_err(|_| invalid())?;
    if document.len() as u64 > MAX_PLAN_BYTES || document.starts_with(b"\xef\xbb\xbf") {
        return Err(invalid());
    }
    client.validate_plan(&document)
}

fn emit(event: Value) {
    echo(Stream::Out, &compact(&event));
}

fn stopped(
    index: usize,
    request_id: Option<&str>,
    error: &ClientError,
    succeeded: usize,
    unsubmitted: usize,
) {
    emit(json!({
        "event": "stopped",
        "index": index,
        "request_id": request_id,
        "error": {"code": error.code, "details": error.details},
        "succeeded": succeeded,
        "unsubmitted": unsubmitted,
    }));
}

fn run_plan(
    commands: &[Value],
    client: &mut DaemonClient,
    selector: Option<&str>,
    timeout: f64,
) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs_f64(timeout);
    let instance = client
        .select_instance(selector, true)
        .inspect_err(|error| {
            stopped(0, None, error, 0, commands.len());
        })?;
    let mut succeeded = 0;
    for (index, command) in commands.iter().enumerate() {
        let request_id = crate::request_id::new();
        let mut submitting = false;
        let mut step = || -> Result<()> {
            if Instant::now() >= deadline {
                return Err(ClientError::new("wait_timeout"));
            }
            emit(json!({
                "event": "submitting",
                "index": index,
                "request_id": request_id,
                "instance_id": instance["instance_id"],
            }));
            submitting = true;
            let target = Some(Target::Id(&instance["instance_id"]));
            client.submit(&request_id, command.clone(), target)?;
            let snapshot = client.wait(&request_id)?;
            emit(json!({"event": "completed", "index": index, "request": snapshot}));
            fail_unless_succeeded(&snapshot)
        };
        if let Err(mut error) = step() {
            if submitting {
                error = error.for_request(&request_id);
            }
            let unsubmitted = commands.len() - index - usize::from(submitting);
            let shown = submitting.then_some(request_id.as_str());
            stopped(index, shown, &error, succeeded, unsubmitted);
            return Err(error);
        }
        succeeded += 1;
    }
    emit(json!({"event": "finished", "succeeded": succeeded}));
    Ok(())
}
