//! Read parsed options the way the Python commands build a Command input from them.

use serde_json::{Map, Value};

use crate::args::Values;
use crate::catalog::catalog;
use crate::client::{ClientError, Result};

pub struct Options<'a>(pub &'a Values);

/// A command's dedicated Command input; `None` when only `--input` or `--input-file` can say it.
pub type Dedicated = Result<Option<Value>>;

/// Builds a command's dedicated input from its options.
pub type Builder = fn(&Options) -> Dedicated;

impl Options<'_> {
    pub fn value(&self, name: &str) -> Value {
        self.0.get(name).cloned().unwrap_or(Value::Null)
    }

    pub fn given(&self, name: &str) -> bool {
        !self.value(name).is_null()
    }

    pub fn flag(&self, name: &str) -> bool {
        self.value(name) == true
    }

    /// `value if value is not None else default`.
    pub fn or(&self, name: &str, default: impl Into<Value>) -> Value {
        match self.value(name) {
            Value::Null => default.into(),
            value => value,
        }
    }

    /// Python `value or 0` for an integer option.
    pub fn or_zero(&self, name: &str) -> Value {
        match self.value(name) {
            Value::Null => Value::from(0),
            value => value,
        }
    }

    pub fn any_given(&self, names: &[&str]) -> bool {
        names.iter().any(|name| self.given(name))
    }

    /// The fields that must come together: all of them, or none of them.
    pub fn together(&self, names: &[&str]) -> Result<bool> {
        let given = names.iter().filter(|name| self.given(name)).count();
        if given != 0 && given != names.len() {
            return Err(invalid());
        }
        Ok(given == names.len())
    }

    /// Copy the named options into an input object, under the same names.
    pub fn object(&self, names: &[&str]) -> Map<String, Value> {
        names
            .iter()
            .map(|name| (name.to_string(), self.value(name)))
            .collect()
    }
}

pub fn invalid() -> ClientError {
    ClientError::new("invalid_arguments")
}

/// A refinement option given without the field it refines.
pub fn require(condition: bool) -> Result<()> {
    if condition { Ok(()) } else { Err(invalid()) }
}

/// A default the Python commands take from the Command catalog.
pub fn input_default(name: &str) -> Value {
    catalog()["input_defaults"][name].clone()
}
