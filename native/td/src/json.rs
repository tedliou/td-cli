//! JSON text exactly as Python `json.dumps(..., ensure_ascii=True)` writes it.
//!
//! Object keys keep their order (`preserve_order`). Integers keep the text the Daemon sent
//! (`arbitrary_precision`), and other numbers print as Python's `repr(float)` would after
//! `json.loads`, so output matches the Python client byte for byte.

use serde_json::{Number, Value};

/// `json.dumps(value, separators=(",", ":"), ensure_ascii=True)`.
pub fn compact(value: &Value) -> String {
    let mut out = String::new();
    write(&mut out, value, None, 0);
    out
}

/// `json.dumps(value, indent=2, ensure_ascii=True)`.
pub fn pretty(value: &Value) -> String {
    let mut out = String::new();
    write(&mut out, value, Some(2), 0);
    out
}

fn write(out: &mut String, value: &Value, indent: Option<usize>, depth: usize) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => out.push_str(&number_text(number)),
        Value::String(text) => write_string(out, text),
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                separator(out, index, indent, depth + 1);
                write(out, item, indent, depth + 1);
            }
            close(out, indent, depth);
            out.push(']');
        }
        Value::Object(entries) => {
            if entries.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push('{');
            for (index, (key, item)) in entries.iter().enumerate() {
                separator(out, index, indent, depth + 1);
                write_string(out, key);
                out.push_str(if indent.is_some() { ": " } else { ":" });
                write(out, item, indent, depth + 1);
            }
            close(out, indent, depth);
            out.push('}');
        }
    }
}

/// Python reads a number with a fraction or exponent as a float and an integer as an int.
pub fn number_text(number: &Number) -> String {
    let raw = number.to_string();
    if raw.contains(['.', 'e', 'E']) {
        return python_float(raw.parse().expect("a JSON number parses as f64"));
    }
    match raw.strip_prefix('-') {
        Some(digits) if digits.bytes().all(|byte| byte == b'0') => "0".into(),
        _ => raw,
    }
}

fn separator(out: &mut String, index: usize, indent: Option<usize>, depth: usize) {
    if index > 0 {
        out.push(',');
    }
    if let Some(width) = indent {
        out.push('\n');
        out.push_str(&" ".repeat(width * depth));
    }
}

fn close(out: &mut String, indent: Option<usize>, depth: usize) {
    if let Some(width) = indent {
        out.push('\n');
        out.push_str(&" ".repeat(width * depth));
    }
}

/// Python escapes `"`, `\`, the five named controls, and every other character outside
/// printable ASCII (space to `~`) as `\uXXXX`, using surrogate pairs above U+FFFF.
pub fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for character in text.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            ' '..='~' => out.push(character),
            _ => {
                let mut units = [0u16; 2];
                for unit in character.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

/// Python `repr(float)`: the shortest round-trip digits, fixed notation for exponents from
/// -4 to 15 and `1e-05` / `1e+16` style otherwise.
pub fn python_float(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 {
            "Infinity".into()
        } else {
            "-Infinity".into()
        };
    }
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0".into()
        } else {
            "0.0".into()
        };
    }
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific
        .split_once('e')
        .expect("Rust always writes an exponent");
    let exponent: i32 = exponent.parse().expect("integer exponent");
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let sign = if negative { "-" } else { "" };
    if (-4..16).contains(&exponent) {
        let point = exponent + 1;
        let body = if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if point as usize >= digits.len() {
            format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        };
        return format!("{sign}{body}");
    }
    let fraction = if digits.len() > 1 {
        format!(".{}", &digits[1..])
    } else {
        String::new()
    };
    let exponent_sign = if exponent < 0 { '-' } else { '+' };
    format!(
        "{sign}{}{fraction}e{exponent_sign}{:02}",
        &digits[..1],
        exponent.abs()
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn strings_escape_like_python_ensure_ascii() {
        let text = "a\"\\\n\r\t\u{08}\u{0c}\u{01}\u{7f}\u{e9}\u{1f600}/";
        let units: String = ["0001", "007f", "00e9", "d83d", "de00"]
            .iter()
            .map(|unit| format!("\\u{unit}"))
            .collect();
        let expected = format!("{}{units}/\"", r#""a\"\\\n\r\t\b\f"#);
        assert_eq!(compact(&json!(text)), expected);
    }

    #[test]
    fn layout_matches_python_indent_and_separators() {
        let value: Value =
            serde_json::from_str(r#"{"a":[1,{"b":[]}],"c":{},"d":1.50e3,"e":-0}"#).unwrap();
        assert_eq!(
            compact(&value),
            r#"{"a":[1,{"b":[]}],"c":{},"d":1500.0,"e":0}"#
        );
        assert_eq!(
            pretty(&value),
            "{\n  \"a\": [\n    1,\n    {\n      \"b\": []\n    }\n  ],\n  \"c\": {},\n  \"d\": 1500.0,\n  \"e\": 0\n}"
        );
    }

    #[test]
    fn floats_print_like_python_repr() {
        let cases = [
            (1.0, "1.0"),
            (0.1, "0.1"),
            (1e-5, "1e-05"),
            (0.0001, "0.0001"),
            (1e16, "1e+16"),
            (123456789012345.6, "123456789012345.6"),
            (-2.5, "-2.5"),
            (1.5e300, "1.5e+300"),
            (100.0, "100.0"),
        ];
        for (value, expected) in cases {
            assert_eq!(python_float(value), expected, "{value}");
        }
    }
}
