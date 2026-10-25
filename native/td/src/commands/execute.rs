//! `commands execute`: run a plan of independent Commands in order, one Request each, with
//! JSONL progress on stdout and a stop at the first failure. With `--frame-batch`, consecutive
//! Commands that may share a frame travel as one `plan.execute` Request (a Frame Batch).

use std::io::Read;
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use super::options::invalid;
use super::{Context, fail_unless_succeeded, text};
use crate::args::Values;
use crate::catalog;
use crate::client::{ClientError, DaemonClient, Result, Target};
use crate::json::compact;
use crate::output::{Stream, echo};

/// A plan is at most this much UTF-8, without a byte order mark.
const MAX_PLAN_BYTES: u64 = 1024 * 1024;
/// A Frame Batch holds at most this many Commands and this many bytes of dispatch text, the
/// limits the Daemon enforces for `plan.execute` (docs/adr/0005-frame-batch-instead-of-session.md).
const FRAME_BATCH_MAX_COMMANDS: usize = 256;
const FRAME_BATCH_MAX_BYTES: usize = 65_536;

pub fn commands_execute(context: &Context, values: &Values) -> Result<()> {
    let path = text(values, "input_file").expect("required option");
    let mut client = context.client();
    let commands = read_plan(path, &mut client)?;
    let mut run = Run {
        commands: &commands,
        client: &mut client,
        deadline: Instant::now() + Duration::from_secs_f64(context.timeout),
        instance: Value::Null,
        succeeded: 0,
    };
    run.start(context.instance.as_deref())?;
    if values.get("frame_batch") == Some(&Value::Bool(true)) {
        run.in_frame_batches()
    } else {
        run.one_by_one()
    }?;
    emit(json!({"event": "finished", "succeeded": run.succeeded}));
    Ok(())
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

fn frame_batchable(command: &Value) -> bool {
    command["name"]
        .as_str()
        .is_some_and(|name| catalog::catalog()["commands"][name]["frame_batchable"] == true)
}

/// Bytes of the Command as the dispatch carries it: compact JSON with non-ASCII escaped, the
/// way the Daemon measures it. Key order does not change the length.
fn dispatch_bytes(command: &Value) -> usize {
    compact(command)
        .chars()
        .map(|character| match character as u32 {
            0..=0x7f => 1,
            0x80..=0xffff => 6,
            _ => 12,
        })
        .sum()
}

struct Run<'a> {
    commands: &'a [Value],
    client: &'a mut DaemonClient,
    deadline: Instant,
    instance: Value,
    succeeded: usize,
}

impl Run<'_> {
    fn start(&mut self, selector: Option<&str>) -> Result<()> {
        let total = self.commands.len();
        self.instance = self
            .client
            .select_instance(selector, true)
            .inspect_err(|error| stopped(0, None, error, 0, total))?;
        Ok(())
    }

    fn one_by_one(&mut self) -> Result<()> {
        for index in 0..self.commands.len() {
            self.single(index)?;
        }
        Ok(())
    }

    fn in_frame_batches(&mut self) -> Result<()> {
        let mut index = 0;
        while index < self.commands.len() {
            let count = self.batch_length(index);
            if count == 0 {
                self.single(index)?;
                index += 1;
            } else {
                index += self.frame_batch(index, count)?;
            }
        }
        Ok(())
    }

    /// How many Commands from `index` on fit one Frame Batch; 0 when that Command cannot join one.
    fn batch_length(&self, index: usize) -> usize {
        let mut bytes = 2; // the enclosing brackets
        let mut count = 0;
        for command in &self.commands[index..] {
            let added = dispatch_bytes(command) + usize::from(count > 0);
            if !frame_batchable(command)
                || count == FRAME_BATCH_MAX_COMMANDS
                || bytes + added > FRAME_BATCH_MAX_BYTES
            {
                break;
            }
            bytes += added;
            count += 1;
        }
        count
    }

    /// Submit one Request for `command` and wait for it, failing unless it succeeded.
    fn submit(&mut self, request_id: &str, command: Value) -> Result<Value> {
        let target = Some(Target::Id(&self.instance["instance_id"]));
        self.client.submit(request_id, command, target)?;
        self.client.wait(request_id)
    }

    fn deadline_passed(&self) -> bool {
        Instant::now() >= self.deadline
    }

    fn single(&mut self, index: usize) -> Result<()> {
        let request_id = crate::request_id::new();
        let mut submitting = false;
        let mut step = |run: &mut Self| -> Result<()> {
            if run.deadline_passed() {
                return Err(ClientError::new("wait_timeout"));
            }
            emit(json!({
                "event": "submitting",
                "index": index,
                "request_id": request_id,
                "instance_id": run.instance["instance_id"],
            }));
            submitting = true;
            let snapshot = run.submit(&request_id, run.commands[index].clone())?;
            emit(json!({"event": "completed", "index": index, "request": snapshot}));
            fail_unless_succeeded(&snapshot)
        };
        if let Err(mut error) = step(self) {
            if submitting {
                error = error.for_request(&request_id);
            }
            let unsubmitted = self.commands.len() - index - usize::from(submitting);
            let shown = submitting.then_some(request_id.as_str());
            stopped(index, shown, &error, self.succeeded, unsubmitted);
            return Err(error);
        }
        self.succeeded += 1;
        Ok(())
    }

    /// Run `count` Commands from `index` as one Frame Batch and return how many it executed.
    fn frame_batch(&mut self, index: usize, count: usize) -> Result<usize> {
        let total = self.commands.len();
        let request_id = crate::request_id::new();
        if self.deadline_passed() {
            let error = ClientError::new("wait_timeout");
            stopped(index, None, &error, self.succeeded, total - index);
            return Err(error);
        }
        emit(json!({
            "event": "submitting",
            "index": index,
            "count": count,
            "request_id": request_id,
            "instance_id": self.instance["instance_id"],
        }));
        let batch = json!({
            "name": "plan.execute",
            "input": {"commands": &self.commands[index..index + count]},
        });
        let report = self
            .submit(&request_id, batch)
            .and_then(|snapshot| {
                fail_unless_succeeded(&snapshot)?;
                Ok(snapshot)
            })
            .map_err(|error| error.for_request(&request_id))
            .inspect_err(|error| {
                // Any Command of the batch may have run when the Request did not succeed.
                stopped(
                    index,
                    Some(&request_id),
                    error,
                    self.succeeded,
                    total - index - count,
                );
            })?;
        let results = report["result"]["results"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        if results.is_empty() {
            let error = ClientError::new("internal_error").for_request(&request_id);
            stopped(
                index,
                Some(&request_id),
                &error,
                self.succeeded,
                total - index - count,
            );
            return Err(error);
        }
        for (offset, item) in results.iter().enumerate() {
            let position = index + offset;
            if item["ok"] != true {
                let error = item_error(&item["error"]).for_request(&request_id);
                stopped(
                    position,
                    Some(&request_id),
                    &error,
                    self.succeeded,
                    total - position - 1,
                );
                return Err(error);
            }
            let mut completed = Map::new();
            completed.insert("event".into(), "completed".into());
            completed.insert("index".into(), position.into());
            completed.insert("request_id".into(), request_id.clone().into());
            if let Some(result) = item.get("result") {
                completed.insert("result".into(), result.clone());
            }
            emit(Value::Object(completed));
            self.succeeded += 1;
        }
        Ok(results.len())
    }
}

/// The error a Frame Batch reported for one of its Commands.
fn item_error(error: &Value) -> ClientError {
    let code = error["code"].as_str().unwrap_or("internal_error");
    let details = error["details"].as_object().cloned().unwrap_or_default();
    ClientError::with(code, details)
}
