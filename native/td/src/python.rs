//! The Python behaviours the CLI's messages depend on: `repr`, `str`, truthiness,
//! `str.isspace` and `difflib.get_close_matches`.

use serde_json::Value;

use crate::json::number_text;

/// Python `str(value)` for a value `json.loads` produced.
pub fn str_of(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => repr_value(other),
    }
}

/// Python truthiness of a value `json.loads` produced.
pub fn is_falsy(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Bool(flag) => !flag,
        Value::Number(number) => number.as_f64() == Some(0.0),
        Value::String(text) => text.is_empty(),
        Value::Array(items) => items.is_empty(),
        Value::Object(entries) => entries.is_empty(),
    }
}

/// `str.isspace`, which also counts the ASCII file, group, record and unit separators.
pub fn is_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

fn repr_value(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => match number_text(number).as_str() {
            "Infinity" => "inf".into(),
            "-Infinity" => "-inf".into(),
            text => text.into(),
        },
        Value::String(text) => repr(text),
        Value::Array(items) => {
            let items: Vec<String> = items.iter().map(repr_value).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(entries) => {
            let entries: Vec<String> = entries
                .iter()
                .map(|(key, item)| format!("{}: {}", repr(key), repr_value(item)))
                .collect();
            format!("{{{}}}", entries.join(", "))
        }
    }
}

/// Python `repr(text)`: single quotes unless the text has a single quote and no double quote,
/// with backslash escapes for the quote, backslash and characters Python cannot print.
pub fn repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ if character == quote => {
                out.push('\\');
                out.push(quote);
            }
            _ if !printable(character) => {
                let code = character as u32;
                if code < 0x100 {
                    out.push_str(&format!("\\x{code:02x}"));
                } else if code < 0x10000 {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    out.push_str(&format!("\\U{code:08x}"));
                }
            }
            _ => out.push(character),
        }
    }
    out.push(quote);
    out
}

/// `str.isprintable` for the characters a command line can plausibly carry: controls,
/// separators other than space, and the common format characters are not printable.
fn printable(character: char) -> bool {
    !(character.is_control()
        || matches!(
            character,
            '\u{a0}'
                | '\u{ad}'
                | '\u{1680}'
                | '\u{2000}'..='\u{200f}'
                | '\u{2028}'..='\u{202f}'
                | '\u{205f}'..='\u{2064}'
                | '\u{3000}'
                | '\u{feff}'
        ))
}

/// `difflib.get_close_matches(word, possibilities)`: at most three possibilities whose
/// `SequenceMatcher` ratio is at least 0.6, best first, ties broken by the larger string.
pub fn close_matches<'a>(word: &str, possibilities: &[&'a str]) -> Vec<&'a str> {
    let word: Vec<char> = word.chars().collect();
    let mut scored: Vec<(f64, &str)> = possibilities
        .iter()
        .filter_map(|candidate| {
            let chars: Vec<char> = candidate.chars().collect();
            let total = chars.len() + word.len();
            if total == 0 {
                return Some((1.0, *candidate));
            }
            let ratio = 2.0 * matched(&chars, &word) as f64 / total as f64;
            (ratio >= 0.6).then_some((ratio, *candidate))
        })
        .collect();
    scored.sort_by(|left, right| right.0.total_cmp(&left.0).then_with(|| right.1.cmp(left.1)));
    scored
        .into_iter()
        .take(3)
        .map(|(_, candidate)| candidate)
        .collect()
}

/// The characters in `SequenceMatcher(None, a, b).get_matching_blocks()`, without junk
/// heuristics (they only apply to sequences of 200 or more).
fn matched(a: &[char], b: &[char]) -> usize {
    let (i, j, size) = longest_match(a, b);
    if size == 0 {
        return 0;
    }
    size + matched(&a[..i], &b[..j]) + matched(&a[i + size..], &b[j + size..])
}

/// `find_longest_match`: the longest common block, earliest in `a`, then earliest in `b`.
fn longest_match(a: &[char], b: &[char]) -> (usize, usize, usize) {
    let mut best = (0, 0, 0);
    let mut previous = vec![0usize; b.len() + 1];
    for (i, left) in a.iter().enumerate() {
        let mut current = vec![0usize; b.len() + 1];
        for (j, right) in b.iter().enumerate() {
            if left == right {
                let size = previous[j] + 1;
                current[j + 1] = size;
                if size > best.2 {
                    best = (i + 1 - size, j + 1 - size, size);
                }
            }
        }
        previous = current;
    }
    best
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn str_of_json_values_matches_python() {
        let value: Value =
            serde_json::from_str(r#"{"a":[1,"x",null,true,1.5,1e400],"it's":{}}"#).unwrap();
        assert_eq!(
            str_of(&value),
            r#"{'a': [1, 'x', None, True, 1.5, inf], "it's": {}}"#
        );
        assert_eq!(str_of(&Value::String("plain".into())), "plain");
    }

    #[test]
    fn repr_matches_python() {
        assert_eq!(repr("x"), "'x'");
        assert_eq!(repr(r"C:\Users"), r"'C:\\Users'");
        assert_eq!(repr("it's"), "\"it's\"");
        assert_eq!(repr("'\""), r#"'\'"'"#);
        assert_eq!(repr("\u{1}é\u{a0}😀"), "'\\x01é\\xa0😀'");
    }

    #[test]
    fn close_matches_follow_difflib() {
        let options = [
            "--input",
            "--input-file",
            "--no-wait",
            "--request-id",
            "--help",
        ];
        assert_eq!(close_matches("--wait", &options), ["--no-wait", "--input"]);
        assert_eq!(
            close_matches("--input-fil", &options),
            ["--input-file", "--input"]
        );
        assert_eq!(close_matches("--requestid", &options), ["--request-id"]);
        assert!(close_matches("--zzzzzzzz", &options).is_empty());
    }
}
