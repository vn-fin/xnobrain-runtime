use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum CheckStatus {
    Pass,
    Warning,
    Fail,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreflightCheck {
    pub id: String,
    pub label: String,
    pub detail: String,
    pub status: CheckStatus,
    pub blocking: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SystemInspection {
    pub platform: String,
    pub platform_label: String,
    pub architecture: String,
    pub docker_installed: bool,
    pub docker_running: bool,
    pub compose_available: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub docker_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compose_version: Option<String>,
    pub default_port: u16,
    pub installed: bool,
    pub healthy: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub selected_port: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub web_url: Option<String>,
    pub checks: Vec<PreflightCheck>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PortInspection {
    pub port: u16,
    pub available: bool,
    pub message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DockerInstallProgress {
    pub percent: u8,
    pub title: String,
    pub detail: String,
}

impl DockerInstallProgress {
    pub fn new(percent: u8, title: &str, detail: &str) -> Self {
        Self {
            percent,
            title: title.to_owned(),
            detail: detail.to_owned(),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DockerInstallResult {
    pub restart_required: bool,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InstallRequest {
    pub port: u16,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallPhase {
    Validating,
    Preparing,
    Pulling,
    Creating,
    Starting,
    HealthCheck,
    Ready,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallProgress {
    pub phase: InstallPhase,
    pub percent: u8,
    pub title: String,
    pub detail: String,
}

impl InstallProgress {
    pub fn new(phase: InstallPhase, percent: u8, title: &str, detail: impl Into<String>) -> Self {
        Self {
            phase,
            percent,
            title: title.to_owned(),
            detail: detail.into(),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallResult {
    pub port: u16,
    pub web_url: String,
    pub compose_path: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeAction {
    Start,
    Stop,
    Restart,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum LogService {
    All,
    Traefik,
    Frontend,
    Runtime,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeState {
    Running,
    Stopped,
    Degraded,
    NotInstalled,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ServiceState {
    Running,
    Stopped,
    Unhealthy,
    Missing,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServiceStatus {
    pub id: LogService,
    pub name: String,
    pub state: ServiceState,
    pub detail: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeOverview {
    pub state: RuntimeState,
    pub web_url: String,
    pub port: u16,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub docker_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compose_version: Option<String>,
    pub services: Vec<ServiceStatus>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLogs {
    pub service: LogService,
    pub lines: Vec<String>,
    pub truncated: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallState {
    pub schema_version: u32,
    pub release: String,
    pub port: u16,
    pub web_url: String,
    pub compose_path: PathBuf,
    pub installed: bool,
}
