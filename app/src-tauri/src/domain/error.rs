use serde::Serialize;
use thiserror::Error;

pub type InstallerResult<T> = Result<T, InstallerError>;

#[derive(Debug, Error, Serialize)]
#[serde(rename_all = "camelCase")]
#[error("{message}")]
pub struct InstallerError {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    pub retryable: bool,
}

impl InstallerError {
    pub fn retryable(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_owned(),
            message: message.into(),
            detail: None,
            retryable: true,
        }
    }

    pub fn terminal(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_owned(),
            message: message.into(),
            detail: None,
            retryable: false,
        }
    }

    pub fn io(operation: &str, error: &std::io::Error) -> Self {
        Self {
            code: "local_state_error".to_owned(),
            message: format!("Could not {operation} the XNOBrain installer state."),
            detail: Some(format!("I/O error kind: {:?}", error.kind())),
            retryable: true,
        }
    }
}
