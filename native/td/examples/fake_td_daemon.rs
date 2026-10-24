//! A stand-in for `td-daemon.exe` in tests: copied beside `td.exe`, it records how `start`
//! was called and then does what `FAKE_TD_DAEMON` asks.
//!
//! - `token`: write the token in `FAKE_TD_DAEMON_TOKEN` where the client reads it, exit 0.
//! - `fail`: print `FAKE_TD_DAEMON_REASON` on stderr and exit 3, like a failed start.
//! - `hang`: never finish, so the client's own limit has to stop it.

use std::fs;
use std::path::PathBuf;
use std::time::Duration;

fn main() {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if let Ok(log) = std::env::var("FAKE_TD_DAEMON_LOG") {
        let _ = fs::write(log, arguments.join("\n"));
    }
    match std::env::var("FAKE_TD_DAEMON").as_deref() {
        Ok("token") => {
            let state = PathBuf::from(std::env::var_os("LOCALAPPDATA").expect("LOCALAPPDATA"))
                .join("touchdesigner-cli")
                .join("state");
            fs::create_dir_all(&state).expect("state directory");
            let token = std::env::var("FAKE_TD_DAEMON_TOKEN").expect("FAKE_TD_DAEMON_TOKEN");
            fs::write(state.join("auth.token"), token).expect("token file");
        }
        Ok("hang") => loop {
            std::thread::sleep(Duration::from_secs(60));
        },
        _ => {
            eprintln!(
                "{}",
                std::env::var("FAKE_TD_DAEMON_REASON").unwrap_or_default()
            );
            std::process::exit(3);
        }
    }
}
