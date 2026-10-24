//! `td`: control TouchDesigner Instances through the local Daemon.

mod args;
mod catalog;
mod client;
mod commands;
mod http;
mod json;
mod output;
mod python;
mod request_id;

use args::{Outcome, UsageError};
use output::{Stream, echo};

fn main() {
    let argv: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect();
    let code = match args::parse(&argv) {
        Outcome::Run(invocation) => commands::run(invocation),
        Outcome::Help(text) => {
            echo(Stream::Out, text.trim_end());
            0
        }
        Outcome::NoArguments(text) => {
            echo(Stream::Out, text.trim_end());
            echo(Stream::Err, "");
            2
        }
        Outcome::Version => {
            echo(
                Stream::Out,
                &format!(
                    "td {} (protocol {})",
                    catalog::release_version(),
                    catalog::protocol_version()
                ),
            );
            0
        }
        Outcome::Usage(error) => usage(&argv, error),
    };
    std::process::exit(code);
}

/// A usage error exits with 2. With `--json` anywhere on the command line, stdout still
/// carries a Protocol v3 error envelope and stderr only the message.
fn usage(argv: &[String], error: UsageError) -> i32 {
    if argv.iter().any(|arg| arg == "--json") {
        echo(
            Stream::Out,
            &format!(
                r#"{{"protocol_version":{},"error":{{"code":"invalid_arguments","message":"invalid_arguments","details":{{}},"retryable":false}}}}"#,
                catalog::protocol_version()
            ),
        );
        echo(Stream::Err, &error.message);
    } else {
        let usage = error.usage.map(|usage| usage + "\n").unwrap_or_default();
        echo(Stream::Err, &format!("{usage}Error: {}", error.message));
    }
    2
}
