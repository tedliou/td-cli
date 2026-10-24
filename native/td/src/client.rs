//! The Daemon client: one Command deadline, autostart through `td-daemon.exe start`, retries
//! only where nothing was sent, and the Protocol v3 checks the Python client makes.

use std::io::Read;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Map, Value};

use crate::catalog;
use crate::http::{Connection, Failure};
use crate::json::compact;

const DEFAULT_PORT: u16 = 9982;
const HEALTH_PROBE_TIMEOUT: f64 = 0.5;
/// The Daemon caps one long poll at 25 s; leave room for its answer within the HTTP budget.
const MAX_REQUEST_WAIT_MS: i64 = 25_000;
const WAIT_RESPONSE_MARGIN: f64 = 0.1;
/// `td-daemon start` bounds itself by `--timeout`; this only covers its own Python startup.
const START_PROCESS_MARGIN: f64 = 5.0;
/// Tests point the client at a fake Daemon; only a loopback address is accepted.
const TEST_ENDPOINT_VARIABLE: &str = "TDCLI_TEST_ENDPOINT";

#[derive(Debug)]
pub struct ClientError {
    pub code: String,
    pub details: Map<String, Value>,
}

impl ClientError {
    pub fn new(code: &str) -> Self {
        Self {
            code: code.into(),
            details: Map::new(),
        }
    }

    pub fn with(code: &str, details: Map<String, Value>) -> Self {
        Self {
            code: code.into(),
            details,
        }
    }

    fn reason(code: &str, reason: impl Into<String>) -> Self {
        Self::with(code, details([("reason", Value::String(reason.into()))]))
    }

    /// Python's `details.setdefault(key, value)`.
    pub fn set_default(&mut self, key: &str, value: Value) {
        if !self.details.contains_key(key) {
            self.details.insert(key.into(), value);
        }
    }
}

pub fn details<const N: usize>(entries: [(&str, Value); N]) -> Map<String, Value> {
    entries
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect()
}

pub type Result<T> = std::result::Result<T, ClientError>;

pub struct DaemonClient {
    timeout: f64,
    autostart: bool,
    deadline: Option<Instant>,
    connection: Connection,
}

impl DaemonClient {
    pub fn new(timeout: f64, autostart: bool) -> Self {
        Self {
            timeout,
            autostart,
            deadline: None,
            connection: Connection::new(endpoint()),
        }
    }

    fn headers(&self) -> Result<Vec<(&'static str, String)>> {
        let token = load_token().ok_or_else(|| ClientError::new("daemon_unavailable"))?;
        Ok(vec![("Authorization", format!("Bearer {token}"))])
    }

    /// Probe health on the shared connection; any doubt is left to the start routine.
    fn daemon_is_healthy(&mut self) -> bool {
        let Ok(headers) = self.headers() else {
            return false;
        };
        let timeout = seconds(HEALTH_PROBE_TIMEOUT.min(self.timeout));
        let Ok((200, content)) =
            self.connection
                .exchange("GET", "/v3/health", &headers, None, timeout)
        else {
            return false;
        };
        let Ok(payload) = serde_json::from_slice::<Value>(&content) else {
            return false;
        };
        payload["ready"] == true && supports_protocol(&payload["protocol_versions"])
    }

    /// Send one call within the Command deadline. With `long_poll`, each attempt asks the
    /// Daemon to wait for the rest of the deadline, less a margin for its answer.
    pub fn request(
        &mut self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
        long_poll: bool,
    ) -> Result<Value> {
        if self.autostart {
            self.autostart = false;
            let deadline = Instant::now() + seconds(self.timeout);
            self.deadline = Some(deadline);
            if !self.daemon_is_healthy() {
                let remaining = remaining(deadline);
                if remaining <= 0.0 {
                    return Err(ClientError::reason(
                        "daemon_unavailable",
                        "command deadline expired",
                    ));
                }
                start_daemon(remaining)?;
            }
        }
        let deadline = self
            .deadline
            .unwrap_or_else(|| Instant::now() + seconds(self.timeout));
        if Instant::now() >= deadline {
            return Err(ClientError::reason(
                "daemon_unavailable",
                "command deadline expired",
            ));
        }
        let backoffs: &[f64] = if method == "GET" {
            &[0.0, 0.1, 0.3]
        } else {
            &[0.0]
        };
        let mut answer = None;
        for &backoff in backoffs {
            if backoff > 0.0 {
                if remaining(deadline) <= backoff {
                    break;
                }
                thread::sleep(seconds(backoff));
            }
            let wait_ms = MAX_REQUEST_WAIT_MS
                .min(((remaining(deadline) - WAIT_RESPONSE_MARGIN) * 1000.0) as i64);
            let target = if long_poll && wait_ms > 0 {
                format!("{path}?wait_ms={wait_ms}")
            } else {
                path.to_string()
            };
            let headers = self.headers()?;
            let timeout = seconds(remaining(deadline).max(0.001));
            match self
                .connection
                .exchange(method, &target, &headers, body, timeout)
            {
                Ok(exchanged) => {
                    answer = Some(exchanged);
                    break;
                }
                Err(Failure::Connect) => {}
                Err(Failure::Exchange) => return Err(ClientError::new("daemon_unavailable")),
            }
        }
        let (status, content) = answer.ok_or_else(|| ClientError::new("daemon_unavailable"))?;
        let payload: Value =
            serde_json::from_slice(&content).map_err(|_| ClientError::new("daemon_unavailable"))?;
        if status >= 400 {
            let detail = match payload.get("detail") {
                None => "transport_error".to_string(),
                Some(Value::Array(_)) => "invalid_arguments".to_string(),
                Some(Value::String(text)) if text == "Not Found" => {
                    "daemon_unavailable".to_string()
                }
                Some(Value::String(text)) => text.clone(),
                Some(other) => compact(other),
            };
            let details = match payload.get("details") {
                Some(Value::Object(details)) => details.clone(),
                _ => Map::new(),
            };
            return Err(ClientError::with(&detail, details));
        }
        Ok(payload)
    }

    pub fn health(&mut self) -> Result<Value> {
        let payload = self.request("GET", "/v3/health", None, false)?;
        if !supports_protocol(&payload["protocol_versions"]) {
            return Err(ClientError::new("protocol_incompatible"));
        }
        Ok(payload)
    }

    pub fn instances(&mut self) -> Result<Value> {
        let items = self.request("GET", "/v3/instances", None, false)?;
        let compatible = items.as_array().is_some_and(|items| {
            items.iter().all(|item| {
                matches!(
                    item["status"].as_str(),
                    Some("online" | "offline" | "draining" | "synchronizing")
                ) && item["protocol_version"] == *catalog::protocol_version()
            })
        });
        if !compatible {
            return Err(ClientError::new("protocol_incompatible"));
        }
        Ok(items)
    }

    /// Let the Daemon apply the selector rule; without a selector, the only online one.
    pub fn select_instance(&mut self, selector: Option<&str>, online_only: bool) -> Result<Value> {
        let mut query = format!("online_only={online_only}");
        if let Some(selector) = selector {
            query.push_str(&format!("&selector={}", quote_plus(selector)));
        }
        self.request(
            "GET",
            &format!("/v3/instances/resolve?{query}"),
            None,
            false,
        )
    }

    /// Submit by Instance selector; the Daemon validates the Command and resolves it.
    pub fn submit(
        &mut self,
        request_id: &str,
        command: Value,
        selector: Option<&str>,
    ) -> Result<Value> {
        let mut body = Map::new();
        body.insert("request_id".into(), Value::String(request_id.into()));
        body.insert("command".into(), command);
        if let Some(selector) = selector {
            body.insert("instance_selector".into(), Value::String(selector.into()));
        }
        let document = compact(&Value::Object(body));
        self.request("POST", "/v3/requests", Some(document.as_bytes()), false)
            .map_err(|mut error| {
                error.set_default("request_id", Value::String(request_id.into()));
                error
            })
    }

    /// Read a Request; with `long_poll` the Daemon answers once it settles.
    pub fn get_request(&mut self, request_id: &str, long_poll: bool) -> Result<Value> {
        let path = format!("/v3/requests/{}", path_segment(request_id));
        let snapshot = self.request("GET", &path, None, long_poll)?;
        if !snapshot_is_compatible(&snapshot) {
            return Err(ClientError::with(
                "protocol_incompatible",
                details([("request", snapshot)]),
            ));
        }
        Ok(snapshot)
    }

    pub fn wait(&mut self, request_id: &str) -> Result<Value> {
        let deadline = self
            .deadline
            .unwrap_or_else(|| Instant::now() + seconds(self.timeout));
        let mut snapshot = Value::Null;
        loop {
            if Instant::now() >= deadline {
                let id = Value::String(request_id.into());
                return Err(ClientError::with(
                    "wait_timeout",
                    details([("request_id", id), ("request", snapshot)]),
                ));
            }
            snapshot = self.get_request(request_id, true).map_err(|mut error| {
                error.set_default("request_id", Value::String(request_id.into()));
                error
            })?;
            if snapshot["status"]
                .as_str()
                .is_some_and(catalog::is_terminal_status)
            {
                return Ok(snapshot);
            }
            if Instant::now() >= deadline {
                return Err(ClientError::with(
                    "wait_timeout",
                    details([("request", snapshot)]),
                ));
            }
            // A long poll ends unsettled only when its wait ran out or the Daemon is stopping;
            // space the next query so neither case becomes a tight loop.
            thread::sleep(seconds(0.05f64.min(remaining(deadline))));
        }
    }
}

fn seconds(value: f64) -> Duration {
    Duration::from_secs_f64(value.max(0.0))
}

fn remaining(deadline: Instant) -> f64 {
    deadline
        .saturating_duration_since(Instant::now())
        .as_secs_f64()
}

fn supports_protocol(versions: &Value) -> bool {
    versions
        .as_array()
        .is_some_and(|versions| versions.contains(catalog::protocol_version()))
}

const MODES: &[&str] = &["constant", "expression", "export", "bind"];

fn one_of(value: &Value, allowed: &[&str]) -> bool {
    value.as_str().is_some_and(|text| allowed.contains(&text))
}

/// Reject a Request this client cannot interpret.
fn snapshot_is_compatible(snapshot: &Value) -> bool {
    if !snapshot["status"]
        .as_str()
        .is_some_and(catalog::is_request_status)
    {
        return false;
    }
    if let Some(error) = snapshot.get("error").filter(|error| error.is_object())
        && !error["code"].as_str().is_some_and(catalog::is_known_error)
    {
        return false;
    }
    let name = snapshot
        .get("command")
        .filter(|command| command.is_object())
        .and_then(|command| command["name"].as_str());
    let Some(result) = snapshot.get("result").filter(|result| result.is_object()) else {
        return true;
    };
    match name {
        Some("parameters.get") => {
            one_of(&result["mode"], MODES)
                && one_of(
                    &result["value_type"],
                    &[
                        "boolean",
                        "integer",
                        "number",
                        "string",
                        "operator",
                        "multi_operator",
                        "python",
                        "sequence",
                        "unknown",
                    ],
                )
        }
        Some("parameters.set") => {
            one_of(&result["mode"], MODES)
                && one_of(
                    &result["value_type"],
                    &[
                        "boolean",
                        "integer",
                        "number",
                        "string",
                        "operator",
                        "multi_operator",
                    ],
                )
        }
        Some("parameters.list") => result["parameters"].as_array().is_some_and(|parameters| {
            parameters.iter().all(|parameter| {
                parameter.is_object()
                    && one_of(&parameter["mode"], MODES)
                    && one_of(
                        &parameter["value_kind"],
                        &[
                            "boolean",
                            "integer",
                            "number",
                            "string",
                            "menu",
                            "operator",
                            "multi_operator",
                            "pulse",
                            "python",
                            "sequence",
                            "unknown",
                        ],
                    )
            })
        }),
        Some("parameters.sequence.get" | "parameters.sequence.replace") => {
            result["blocks"].is_array()
        }
        _ => true,
    }
}

fn endpoint() -> SocketAddr {
    let default = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), DEFAULT_PORT);
    let Ok(configured) = std::env::var(TEST_ENDPOINT_VARIABLE) else {
        return default;
    };
    configured
        .trim_start_matches("http://")
        .parse::<SocketAddr>()
        .ok()
        .filter(|address| address.ip().is_loopback())
        .unwrap_or(default)
}

fn data_root() -> Option<PathBuf> {
    let local = std::env::var_os("LOCALAPPDATA").filter(|local| !local.is_empty())?;
    Some(PathBuf::from(local).join("touchdesigner-cli"))
}

/// The Daemon's bearer token: 64 lowercase hex characters, or nothing usable.
fn load_token() -> Option<String> {
    let text = std::fs::read_to_string(data_root()?.join("state").join("auth.token")).ok()?;
    let token = text.trim();
    (token.len() == 64
        && token
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')))
    .then(|| token.to_string())
}

/// Run `td-daemon.exe start` beside this exe; it starts, waits and reports like the Python
/// client's start routine, so its stderr line is the failure reason.
fn start_daemon(remaining: f64) -> Result<()> {
    let program = std::env::current_exe()
        .map_err(|error| ClientError::reason("daemon_unavailable", error.to_string()))?
        .with_file_name("td-daemon.exe");
    let mut child = Command::new(&program)
        .args(["start", "--timeout", &format!("{:.3}", remaining.max(0.1))])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| ClientError::reason("daemon_unavailable", error.to_string()))?;
    let mut stderr = child.stderr.take().expect("piped stderr");
    let reader = thread::spawn(move || {
        let mut text = Vec::new();
        let _ = stderr.read_to_end(&mut text);
        text
    });
    let limit = Instant::now() + seconds(remaining + START_PROCESS_MARGIN);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < limit => thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ClientError::reason(
                    "daemon_unavailable",
                    "td-daemon start did not finish in time",
                ));
            }
            Err(error) => return Err(ClientError::reason("daemon_unavailable", error.to_string())),
        }
    };
    if status.success() {
        return Ok(());
    }
    let text = reader.join().unwrap_or_default();
    Err(ClientError::reason(
        "daemon_unavailable",
        String::from_utf8_lossy(&text).trim(),
    ))
}

/// `urllib.parse.quote_plus`.
fn quote_plus(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(byte as char)
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// A Request ID goes into the path as typed, like the Python client; only bytes that cannot
/// appear in a request line are escaped.
fn path_segment(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        if byte.is_ascii_graphic() {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn query_text_matches_urlencode() {
        assert_eq!(quote_plus("a b/é~"), "a+b%2F%C3%A9~");
    }

    #[test]
    fn unknown_parameter_shapes_are_incompatible() {
        let base = json!({"status": "succeeded", "command": {"name": "parameters.get"}, "result": {"mode": "constant", "value_type": "number"}});
        assert!(snapshot_is_compatible(&base));
        let mut odd = base.clone();
        odd["result"]["value_type"] = json!("menu");
        assert!(!snapshot_is_compatible(&odd));
        assert!(!snapshot_is_compatible(&json!({"status": "lost"})));
        assert!(!snapshot_is_compatible(
            &json!({"status": "failed", "error": {"code": "future_error"}})
        ));
    }
}
