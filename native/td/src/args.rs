//! Parse `td` arguments the way the Python CLI (typer on click) does, driven by the exported
//! command tree: group options come before the subcommand, a leaf mixes options and
//! arguments, and every usage error exits with code 2.

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{Number, Value};

use crate::catalog::catalog;
use crate::json::python_float;
use crate::python::{close_matches, repr};

pub type Values = BTreeMap<String, Value>;

pub struct Invocation {
    pub path: Vec<String>,
    pub globals: Values,
    pub values: Values,
}

pub enum Outcome {
    Run(Invocation),
    /// Help for stdout, then exit 0.
    Help(String),
    /// `td` without arguments: help for stdout, then exit 2.
    NoArguments(String),
    Version,
    Usage(UsageError),
}

/// click's `UsageError`: the message, and the usage lines shown before it when there are any.
pub struct UsageError {
    pub message: String,
    pub usage: Option<String>,
}

struct Param<'a>(&'a Value);

impl<'a> Param<'a> {
    fn name(&self) -> &'a str {
        self.0["name"].as_str().expect("param name")
    }
    fn is_argument(&self) -> bool {
        self.0["kind"] == "argument"
    }
    fn opts(&self) -> impl Iterator<Item = &'a str> {
        strings(&self.0["opts"])
    }
    fn secondary(&self) -> impl Iterator<Item = &'a str> {
        strings(&self.0["secondary_opts"])
    }
    fn is_flag(&self) -> bool {
        self.0["is_flag"] == true
    }
    fn is_required(&self) -> bool {
        self.0["required"] == true
    }
    fn kind(&self) -> &'a Value {
        &self.0["type"]
    }
    fn nargs(&self) -> usize {
        self.kind()["items"].as_array().map_or(1, Vec::len)
    }
    /// click's error hint: the option names, or the argument name.
    fn hint(&self) -> String {
        if self.is_argument() {
            format!("'{}'", self.name())
        } else {
            self.opts()
                .map(|opt| format!("'{opt}'"))
                .collect::<Vec<_>>()
                .join(" / ")
        }
    }
}

fn strings(value: &Value) -> impl Iterator<Item = &str> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
}

fn params(node: &Value) -> impl Iterator<Item = Param<'_>> {
    node["params"].as_array().into_iter().flatten().map(Param)
}

fn command_line(path: &[String]) -> String {
    std::iter::once("td")
        .chain(path.iter().map(String::as_str))
        .collect::<Vec<_>>()
        .join(" ")
}

fn usage_tail(node: &Value) -> String {
    if node.get("commands").is_some() {
        return "[OPTIONS] COMMAND [ARGS]...".into();
    }
    let mut tail = vec!["[OPTIONS]".to_string()];
    for param in params(node).filter(Param::is_argument) {
        let name = param.name();
        tail.push(if param.is_required() {
            format!("{{{name}}}")
        } else {
            format!("[{name}]")
        });
    }
    tail.join(" ")
}

/// Where a usage error was raised: its command path and node, for the usage lines.
struct CommandSite<'a> {
    path: &'a [String],
    node: &'a Value,
}

impl CommandSite<'_> {
    fn fail(&self, message: impl Into<String>) -> UsageError {
        let line = command_line(self.path);
        UsageError {
            message: message.into(),
            usage: Some(format!(
                "Usage: {line} {}\nTry '{line} --help' for help.\n",
                usage_tail(self.node)
            )),
        }
    }
}

/// click reports a malformed option value without the usage lines.
fn error_without_usage(message: String) -> UsageError {
    UsageError {
        message,
        usage: None,
    }
}

pub fn parse(argv: &[String]) -> Outcome {
    let root = &catalog()["cli"];
    if argv.is_empty() {
        return Outcome::NoArguments(help(root, &[]));
    }
    match parse_tree(root, argv) {
        Ok(outcome) => outcome,
        Err(error) => Outcome::Usage(error),
    }
}

fn parse_tree(root: &Value, argv: &[String]) -> Result<Outcome, UsageError> {
    let mut path: Vec<String> = Vec::new();
    let mut node = root;
    let mut index = 0;
    let mut globals = Values::new();
    loop {
        // A group reads its own options up to the first other token, its subcommand.
        let place = CommandSite { path: &path, node };
        let mut raw = BTreeMap::new();
        let mut wants_help = false;
        while index < argv.len() && is_option(&argv[index]) {
            if argv[index] == "--help" {
                wants_help = true;
                index += 1;
            } else {
                take_option(&place, argv, &mut index, &mut raw)?;
            }
        }
        if wants_help {
            return Ok(Outcome::Help(help(node, &path)));
        }
        if path.is_empty() && matches!(raw.get("version"), Some(Raw::Flag(true))) {
            return Ok(Outcome::Version);
        }
        let values = convert(&place, raw)?;
        if path.is_empty() {
            globals = values;
        }
        if argv.get(index).is_some_and(|token| token == "--") {
            index += 1;
        }
        let Some(name) = argv.get(index) else {
            return Err(place.fail("Missing command."));
        };
        let children = node["commands"].as_object().expect("a group has commands");
        let Some(child) = children.get(name.as_str()) else {
            let names: Vec<&str> = children.keys().map(String::as_str).collect();
            let mut message = format!("No such command {}.", repr(name));
            let matches = close_matches(name, &names);
            if !matches.is_empty() {
                let suggestions: Vec<String> = matches.iter().map(|item| repr(item)).collect();
                message = format!(
                    "{}. Did you mean {}?",
                    message.trim_end_matches('.'),
                    suggestions.join(", ")
                );
            }
            return Err(place.fail(message));
        };
        path.push(name.clone());
        index += 1;
        node = child;
        if child.get("commands").is_none() {
            return parse_leaf(node, path, globals, &argv[index..]);
        }
    }
}

fn parse_leaf(
    node: &Value,
    path: Vec<String>,
    globals: Values,
    argv: &[String],
) -> Result<Outcome, UsageError> {
    let place = CommandSite { path: &path, node };
    let mut raw = BTreeMap::new();
    let mut positional = Vec::new();
    let mut wants_help = false;
    let mut index = 0;
    let mut options_ended = false;
    while index < argv.len() {
        let token = &argv[index];
        if !options_ended && token == "--" {
            options_ended = true;
            index += 1;
        } else if !options_ended && token == "--help" {
            wants_help = true;
            index += 1;
        } else if !options_ended && is_option(token) {
            take_option(&place, argv, &mut index, &mut raw)?;
        } else {
            positional.push(token.clone());
            index += 1;
        }
    }
    if wants_help {
        return Ok(Outcome::Help(help(node, &path)));
    }
    let arguments: Vec<_> = params(node).filter(Param::is_argument).collect();
    for (param, value) in arguments.iter().zip(&positional) {
        raw.insert(param.name().to_string(), Raw::Text(vec![value.clone()]));
    }
    let values = convert(&place, raw)?;
    if positional.len() > arguments.len() {
        let extra = positional[arguments.len()..].join(" ");
        return Err(place.fail(format!("Got unexpected extra argument(s) ({extra})")));
    }
    Ok(Outcome::Run(Invocation {
        path,
        globals,
        values,
    }))
}

fn is_option(token: &str) -> bool {
    token.len() > 1 && token.starts_with('-') && token != "--"
}

enum Raw {
    Flag(bool),
    Text(Vec<String>),
}

fn take_option(
    place: &CommandSite,
    argv: &[String],
    index: &mut usize,
    raw: &mut BTreeMap<String, Raw>,
) -> Result<(), UsageError> {
    let token = &argv[*index];
    *index += 1;
    let (name, inline) = match token.split_once('=') {
        Some((name, value)) if name.starts_with("--") => (name, Some(value.to_string())),
        _ => (token.as_str(), None),
    };
    let options: Vec<_> = params(place.node)
        .filter(|param| !param.is_argument())
        .collect();
    let found = options.iter().find_map(|param| {
        if param.opts().any(|opt| opt == name) {
            Some((param, true))
        } else if param.secondary().any(|opt| opt == name) {
            Some((param, false))
        } else {
            None
        }
    });
    let Some((param, primary)) = found else {
        let mut known: Vec<&str> = options
            .iter()
            .flat_map(|param| param.opts().chain(param.secondary()))
            .collect();
        known.push("--help");
        let mut matches = close_matches(name, &known);
        let mut message = format!("No such option: {name}");
        if !matches.is_empty() {
            matches.sort();
            message.push_str(&format!(" (Possible options: {})", matches.join(", ")));
        }
        return Err(place.fail(message));
    };
    if param.is_flag() {
        if inline.is_some() {
            return Err(error_without_usage(format!(
                "Option '{name}' does not take a value."
            )));
        }
        raw.insert(param.name().to_string(), Raw::Flag(primary));
        return Ok(());
    }
    let nargs = param.nargs();
    let mut values: Vec<String> = inline.into_iter().collect();
    while values.len() < nargs && *index < argv.len() {
        values.push(argv[*index].clone());
        *index += 1;
    }
    if values.len() < nargs {
        return Err(error_without_usage(if nargs == 1 {
            format!("Option '{name}' requires an argument.")
        } else {
            format!("Option '{name}' requires {nargs} arguments.")
        }));
    }
    raw.insert(param.name().to_string(), Raw::Text(values));
    Ok(())
}

fn convert(place: &CommandSite, mut raw: BTreeMap<String, Raw>) -> Result<Values, UsageError> {
    let mut values = Values::new();
    for param in params(place.node) {
        let value = match raw.remove(param.name()) {
            Some(Raw::Flag(flag)) => Value::Bool(flag),
            Some(Raw::Text(texts)) if param.nargs() > 1 => {
                let items = param.kind()["items"].as_array().expect("tuple items");
                let converted = texts
                    .iter()
                    .zip(items)
                    .map(|(text, kind)| convert_text(text, kind, &param));
                Value::Array(
                    converted
                        .collect::<Result<_, _>>()
                        .map_err(|message| place.fail(message))?,
                )
            }
            Some(Raw::Text(texts)) => convert_text(&texts[0], param.kind(), &param)
                .map_err(|message| place.fail(message))?,
            None if param.is_required() => {
                let what = if param.is_argument() {
                    "argument"
                } else {
                    "option"
                };
                return Err(place.fail(format!("Missing {what} {}.", param.hint())));
            }
            None => param.0["default"].clone(),
        };
        values.insert(param.name().to_string(), value);
    }
    Ok(values)
}

fn convert_text(text: &str, kind: &Value, param: &Param) -> Result<Value, String> {
    let ranged = kind.get("min").is_some() || kind.get("max").is_some();
    let invalid = |what: &str| {
        let what = if ranged {
            format!("{what} range")
        } else {
            what.to_string()
        };
        format!(
            "Invalid value for {}: {} is not a valid {what}.",
            param.hint(),
            repr(text)
        )
    };
    match kind["name"].as_str().expect("type name") {
        "int" => {
            let parsed = python_int(text).ok_or_else(|| invalid("int"))?;
            in_range(parsed as f64, kind, param, &parsed.to_string())?;
            Ok(Value::Number(parsed.into()))
        }
        "float" => {
            let parsed = python_float_text(text).ok_or_else(|| invalid("float"))?;
            in_range(parsed, kind, param, &float_str(parsed))?;
            Ok(Value::Number(
                python_float(parsed).parse::<Number>().unwrap_or_else(|_| {
                    // JSON has no infinity; Python keeps it as a float the CLI later rejects.
                    Number::from_f64(f64::MAX.copysign(parsed)).expect("finite")
                }),
            ))
        }
        "bool" => match text.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "t" | "yes" | "y" | "on" => Ok(Value::Bool(true)),
            "0" | "false" | "f" | "no" | "n" | "off" => Ok(Value::Bool(false)),
            _ => Err(invalid("boolean")),
        },
        "path" => {
            let target = Path::new(text);
            let noun = match (kind["file_okay"] == true, kind["dir_okay"] == true) {
                (true, false) => "File",
                (false, true) => "Directory",
                _ => "Path",
            };
            if kind["exists"] == true && !target.exists() {
                return Err(format!(
                    "Invalid value for {}: {noun} {} does not exist.",
                    param.hint(),
                    repr(text)
                ));
            }
            if kind["dir_okay"] == false && target.is_dir() {
                return Err(format!(
                    "Invalid value for {}: {noun} {} is a directory.",
                    param.hint(),
                    repr(text)
                ));
            }
            Ok(Value::String(text.into()))
        }
        _ => Ok(Value::String(text.into())),
    }
}

/// Python `str(float)`, which writes infinities and NaN as `inf` and `nan`.
fn float_str(value: f64) -> String {
    match python_float(value).as_str() {
        "Infinity" => "inf".into(),
        "-Infinity" => "-inf".into(),
        "NaN" => "nan".into(),
        text => text.into(),
    }
}

fn in_range(value: f64, kind: &Value, param: &Param, shown: &str) -> Result<(), String> {
    let min = kind.get("min").and_then(Value::as_f64);
    let max = kind.get("max").and_then(Value::as_f64);
    // NaN fails every comparison in Python and then breaks the Command; reject it here.
    let outside =
        value.is_nan() || min.is_some_and(|min| value < min) || max.is_some_and(|max| value > max);
    if !outside || (min.is_none() && max.is_none()) {
        return Ok(());
    }
    let bounds = match (&kind["min"], &kind["max"]) {
        (Value::Null, high) => format!("x<={high}"),
        (low, Value::Null) => format!("x>={low}"),
        (low, high) => format!("{low}<=x<={high}"),
    };
    Err(format!(
        "Invalid value for {}: {shown} is not in the range {bounds}.",
        param.hint()
    ))
}

/// Python allows single underscores between digits in `int()` and `float()` text.
fn without_digit_underscores(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'_' {
            let between = index > 0
                && bytes[index - 1].is_ascii_digit()
                && bytes.get(index + 1).is_some_and(u8::is_ascii_digit);
            if !between {
                return None;
            }
        }
    }
    Some(text.replace('_', ""))
}

/// Python `int(text)`: surrounding whitespace, a sign, and digits.
fn python_int(text: &str) -> Option<i64> {
    let cleaned = without_digit_underscores(text.trim())?;
    let digits = cleaned.strip_prefix(['+', '-']).unwrap_or(&cleaned);
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    cleaned.parse().ok()
}

/// Python `float(text)`: like Rust's parser, plus surrounding whitespace and digit underscores.
fn python_float_text(text: &str) -> Option<f64> {
    let cleaned = without_digit_underscores(text.trim())?;
    let body = cleaned
        .strip_prefix(['+', '-'])
        .unwrap_or(&cleaned)
        .to_ascii_lowercase();
    let named = matches!(body.as_str(), "inf" | "infinity" | "nan");
    let numeric = !body.is_empty()
        && body
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'.' | b'e' | b'+' | b'-'));
    if !named && !numeric {
        return None;
    }
    cleaned.parse().ok()
}

pub fn help(node: &Value, path: &[String]) -> String {
    let mut text = format!("Usage: {} {}\n", command_line(path), usage_tail(node));
    if let Some(about) = node["help"].as_str() {
        text.push_str(&format!("\n  {}\n", about.trim()));
    }
    let arguments: Vec<_> = params(node).filter(Param::is_argument).collect();
    if !arguments.is_empty() {
        text.push_str("\nArguments:\n");
        for param in arguments {
            let required = if param.is_required() {
                "  [required]"
            } else {
                ""
            };
            text.push_str(&format!("  {}{required}\n", param.name()));
        }
    }
    text.push_str("\nOptions:\n");
    for param in params(node).filter(|param| !param.is_argument()) {
        let mut names: Vec<&str> = param.opts().collect();
        names.extend(param.secondary());
        let mut line = format!("  {}", names.join(" / "));
        if !param.is_flag() {
            line.push_str(&format!(" {}", metavar(param.kind())));
        }
        if let Some(about) = param.0["help"].as_str() {
            line.push_str(&format!("  {about}"));
        }
        if param.is_required() {
            line.push_str("  [required]");
        }
        text.push_str(&line);
        text.push('\n');
    }
    text.push_str("  --help  Show this message and exit.\n");
    if let Some(children) = node["commands"].as_object() {
        text.push_str("\nCommands:\n");
        for (name, child) in children {
            let about = child["help"]
                .as_str()
                .unwrap_or("")
                .lines()
                .next()
                .unwrap_or("");
            text.push_str(&format!("  {name}  {about}\n"));
        }
    }
    text
}

fn metavar(kind: &Value) -> String {
    match kind["name"].as_str().unwrap_or("string") {
        "int" => "INTEGER".into(),
        "float" => "FLOAT".into(),
        "path" => "PATH".into(),
        "tuple" => vec!["FLOAT"; kind["items"].as_array().map_or(1, Vec::len)].join(" "),
        _ => "TEXT".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(args: &[&str]) -> Outcome {
        parse(&args.iter().map(|arg| arg.to_string()).collect::<Vec<_>>())
    }

    fn invocation(args: &[&str]) -> Invocation {
        match run(args) {
            Outcome::Run(invocation) => invocation,
            _ => panic!("expected a runnable invocation for {args:?}"),
        }
    }

    fn usage(args: &[&str]) -> String {
        match run(args) {
            Outcome::Usage(error) => format!(
                "{}Error: {}",
                error.usage.map(|u| u + "\n").unwrap_or_default(),
                error.message
            ),
            _ => panic!("expected a usage error for {args:?}"),
        }
    }

    #[test]
    fn globals_precede_the_command_and_options_mix_with_arguments() {
        let parsed = invocation(&[
            "--json",
            "--timeout",
            "5",
            "ops",
            "get",
            "--no-wait",
            "/project1",
        ]);
        assert_eq!(parsed.path, ["ops", "get"]);
        assert_eq!(parsed.globals["as_json"], true);
        assert_eq!(parsed.globals["timeout"].to_string(), "5.0");
        assert_eq!(parsed.values["operator_path"], "/project1");
        assert_eq!(parsed.values["no_wait"], true);
        assert_eq!(parsed.values["request_id"], Value::Null);
        let value_like = invocation(&["ops", "get", "--input", "--no-wait", "--", "--x"]);
        assert_eq!(value_like.values["input"], "--no-wait");
        assert_eq!(value_like.values["operator_path"], "--x");
    }

    #[test]
    fn secondary_flags_tuples_and_python_number_text_parse() {
        let parsed = invocation(&["events", "read", "--no-include-errors", "--limit= 1_0 "]);
        assert_eq!(parsed.values["include_errors"], false);
        assert_eq!(parsed.values["limit"], 10);
        let colored = invocation(&["ops", "state", "set", "/a", "--color", "1", " .5 ", "-0"]);
        assert_eq!(colored.values["color"].to_string(), "[1.0,0.5,-0.0]");
    }

    #[test]
    fn usage_errors_match_click_text() {
        assert_eq!(
            usage(&["ops", "get", "a", "b"]),
            "Usage: td ops get [OPTIONS] [operator_path]\nTry 'td ops get --help' for help.\n\nError: Got unexpected extra argument(s) (b)"
        );
        assert_eq!(
            usage(&["requests", "get"]),
            "Usage: td requests get [OPTIONS] {request_id}\nTry 'td requests get --help' for help.\n\nError: Missing argument 'request_id'."
        );
        assert!(usage(&["--timeout", "0", "ops", "get"]).ends_with(
            "Error: Invalid value for '--timeout': 0.0 is not in the range 0.1<=x<=3600."
        ));
        assert!(
            usage(&["--timeout", "1e400", "version"])
                .ends_with("inf is not in the range 0.1<=x<=3600.")
        );
        assert!(usage(&["--timeout", "~", "version"]).ends_with("'~' is not a valid float range."));
        assert!(
            usage(&["ops", "types", "--limit", "5.0"])
                .ends_with("Error: Invalid value for '--limit': '5.0' is not a valid int range.")
        );
        assert!(
            usage(&["ops", "state", "set", "/a", "--color", "1", "x", "2"])
                .ends_with("'x' is not a valid float.")
        );
        assert_eq!(
            usage(&["ops", "get", "--input"]),
            "Error: Option '--input' requires an argument."
        );
        assert_eq!(
            usage(&["ops", "state", "set", "/a", "--color", "1", "2"]),
            "Error: Option '--color' requires 3 arguments."
        );
        assert_eq!(
            usage(&["--json=1", "version"]),
            "Error: Option '--json' does not take a value."
        );
        assert!(
            usage(&["ops", "get", "--wait"])
                .ends_with("Error: No such option: --wait (Possible options: --input, --no-wait)")
        );
        assert!(
            usage(&["ops", "gett"]).ends_with("Error: No such command 'gett'. Did you mean 'get'?")
        );
        assert!(usage(&["ops"]).ends_with("Error: Missing command."));
        assert!(usage(&["--json"]).ends_with("Error: Missing command."));
        assert!(usage(&["commands", "execute"]).ends_with("Error: Missing option '--input-file'."));
        assert!(
            usage(&["project", "open", "."])
                .ends_with("Error: Invalid value for 'path': File '.' is a directory.")
        );
    }

    #[test]
    fn eager_help_and_version_win_over_conversion_errors() {
        assert!(matches!(
            run(&["--version", "--timeout", "0"]),
            Outcome::Version
        ));
        assert!(matches!(
            run(&["--timeout", "0", "--help"]),
            Outcome::Help(_)
        ));
        assert!(matches!(
            run(&["requests", "get", "--help"]),
            Outcome::Help(_)
        ));
        assert!(matches!(run(&["--help", "--bogus"]), Outcome::Usage(_)));
        assert!(matches!(run(&[]), Outcome::NoArguments(_)));
    }
}
