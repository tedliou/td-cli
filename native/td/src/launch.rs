//! Windows launches owned by the desktop user, not the invoking shell's job, through the same
//! local `Win32_Process.Create` call the Python `launch_detached` makes.

use std::io::Read;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::json::compact;
use crate::python;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Why a launch failed, in the words of the Python `LaunchError` or `OSError`.
pub struct LaunchError(pub String);

/// The PowerShell script the Python client runs, with its payload in place of `PAYLOAD`.
const SCRIPT: &str = "$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD'))|ConvertFrom-Json
$s=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
    ShowWindow=[uint16]$p.show;CreateFlags=[uint32]$p.flags}
$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine=[string]$p.command;CurrentDirectory=[string]$p.cwd;ProcessStartupInformation=$s}
$r|Select-Object ReturnValue,ProcessId|ConvertTo-Json -Compress
";

/// Launch `command` in `cwd` as a visible window and return its PID; never retried.
/// The PID is launch status, not application readiness.
pub fn launch_detached(command: &[&str], cwd: &Path, timeout: f64) -> Result<u32, LaunchError> {
    let payload = json!({
        "command": list2cmdline(command),
        "cwd": cwd.to_string_lossy(),
        "show": 1,
        "flags": 0x0100_0008,
    });
    let script = SCRIPT.replace("PAYLOAD", &base64(compact(&payload).as_bytes()));
    let encoded: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let system_root = std::env::var_os("SystemRoot")
        .ok_or_else(|| LaunchError("SystemRoot is not set".into()))?;
    let program = PathBuf::from(system_root).join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut child = Command::new(program)
        .args(["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden"])
        .args(["-EncodedCommand", &base64(&encoded)])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| LaunchError(os_error_text(&error)))?;
    let stdout = collect(child.stdout.take().expect("piped stdout"));
    let stderr = collect(child.stderr.take().expect("piped stderr"));
    let limit = Instant::now() + Duration::from_secs_f64(timeout.max(0.0));
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < limit => thread::sleep(Duration::from_millis(10)),
            outcome => {
                let _ = child.kill();
                let _ = child.wait();
                if let Err(error) = outcome {
                    return Err(LaunchError(os_error_text(&error)));
                }
                return Err(LaunchError(
                    "launch outcome unknown after WMI timeout; inspect processes before retrying"
                        .into(),
                ));
            }
        }
    };
    let (stdout, stderr) = (text(stdout), text(stderr));
    if !status.success() {
        return Err(LaunchError(format!(
            "Windows process service rejected launch: {}",
            stderr.trim_matches(python::is_space)
        )));
    }
    let unverifiable = || {
        LaunchError("unverifiable WMI launch response; inspect processes before retrying".into())
    };
    let response: Value = serde_json::from_str(&stdout).map_err(|_| unverifiable())?;
    let fields = response.as_object().ok_or_else(unverifiable)?;
    let code = fields
        .get("ReturnValue")
        .and_then(python_int)
        .ok_or_else(unverifiable)?;
    let pid = match fields.get("ProcessId").ok_or_else(unverifiable)? {
        value if is_falsy(value) => 0,
        value => python_int(value).ok_or_else(unverifiable)?,
    };
    if code != 0 || pid <= 0 {
        return Err(LaunchError(format!(
            "Windows process service rejected launch (Win32_Process {code})"
        )));
    }
    u32::try_from(pid).map_err(|_| unverifiable())
}

/// Read a pipe to its end on its own thread.
fn collect(mut pipe: impl Read + Send + 'static) -> mpsc::Receiver<Vec<u8>> {
    let (sender, received) = mpsc::channel();
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = pipe.read_to_end(&mut bytes);
        let _ = sender.send(bytes);
    });
    received
}

/// What the pipe held, decoded as `utf-8-sig` with replacement. A process that inherited the
/// pipe could keep it open after PowerShell exits; its output is not worth waiting for.
fn text(received: mpsc::Receiver<Vec<u8>>) -> String {
    let bytes = received
        .recv_timeout(Duration::from_secs(1))
        .unwrap_or_default();
    let text = String::from_utf8_lossy(&bytes);
    text.strip_prefix('\u{feff}').unwrap_or(&text).to_string()
}

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

/// Python `int(value)` for the JSON values PowerShell can print.
fn python_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(flag) => Some(i64::from(*flag)),
        Value::Number(number) => number.as_i64().or_else(|| {
            number
                .as_f64()
                .filter(|value| value.is_finite())
                .map(|value| value as i64)
        }),
        Value::String(text) => text.trim_matches(python::is_space).parse().ok(),
        _ => None,
    }
}

/// `str(OSError)` on Windows: `[WinError n] message`, without the trailing space and dots
/// Python strips from the system message.
fn os_error_text(error: &std::io::Error) -> String {
    let Some(code) = error.raw_os_error() else {
        return error.to_string();
    };
    let shown = error.to_string();
    let message = shown
        .strip_suffix(&format!(" (os error {code})"))
        .unwrap_or(&shown)
        .trim_end_matches(|character: char| character <= ' ' || character == '.');
    format!("[WinError {code}] {message}")
}

/// `subprocess.list2cmdline`: the MS C runtime quoting rules.
pub fn list2cmdline(arguments: &[&str]) -> String {
    let mut line = String::new();
    for argument in arguments {
        if !line.is_empty() {
            line.push(' ');
        }
        let quoted = argument.is_empty() || argument.contains([' ', '\t']);
        if quoted {
            line.push('"');
        }
        let mut backslashes = 0;
        for character in argument.chars() {
            match character {
                '\\' => backslashes += 1,
                '"' => {
                    line.push_str(&"\\".repeat(backslashes * 2));
                    line.push_str("\\\"");
                    backslashes = 0;
                }
                _ => {
                    line.push_str(&"\\".repeat(backslashes));
                    backslashes = 0;
                    line.push(character);
                }
            }
        }
        line.push_str(&"\\".repeat(backslashes));
        if quoted {
            line.push_str(&"\\".repeat(backslashes));
            line.push('"');
        }
    }
    line
}

/// Standard base64 with padding.
fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let value = chunk.iter().enumerate().fold(0u32, |value, (index, byte)| {
            value | u32::from(*byte) << (16 - 8 * index)
        });
        for index in 0..4 {
            if index <= chunk.len() {
                out.push(ALPHABET[(value >> (18 - 6 * index) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_lines_follow_list2cmdline() {
        assert_eq!(
            list2cmdline(&[r"C:\Program Files\TD\TouchDesigner.exe", r"C:\a b\p.toe"]),
            r#""C:\Program Files\TD\TouchDesigner.exe" "C:\a b\p.toe""#
        );
        assert_eq!(
            list2cmdline(&["x", "", r#"a"b"#, r#"c\\"d"#, r"e f\", r"g\h", "tab\there"]),
            "x \"\" a\\\"b c\\\\\\\\\\\"d \"e f\\\\\" g\\h \"tab\there\""
        );
    }

    #[test]
    fn base64_matches_python() {
        let cases: [(&[u8], &str); 6] = [
            (b"", ""),
            (b"f", "Zg=="),
            (b"fo", "Zm8="),
            (b"foo", "Zm9v"),
            (b"foob", "Zm9vYg=="),
            (&[250, 251, 252, 253, 254, 255], "+vv8/f7/"),
        ];
        for (bytes, expected) in cases {
            assert_eq!(base64(bytes), expected);
        }
    }

    #[test]
    fn integers_follow_python_int() {
        for (value, expected) in [
            (json!(7), Some(7)),
            (json!(7.9), Some(7)),
            (json!(" 12 "), Some(12)),
            (json!(true), Some(1)),
            (json!(null), None),
            (json!("x"), None),
        ] {
            assert_eq!(python_int(&value), expected, "{value}");
        }
    }
}
