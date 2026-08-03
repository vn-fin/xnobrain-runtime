mod compose;

use std::{
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener},
    path::Path,
    process::{Command, Output},
    thread,
    time::{Duration, Instant},
};

use fs2::available_space;

use crate::domain::{
    CheckStatus, ImageManifest, InstallerError, InstallerResult, LogService, PortInspection,
    PreflightCheck, RuntimeManifest, RuntimeState, ServiceState, ServiceStatus,
};

pub use compose::ComposeSpec;

#[derive(Clone, Debug, Default)]
pub struct DockerStatus {
    pub installed: bool,
    pub running: bool,
    pub compose_available: bool,
    pub docker_version: Option<String>,
    pub compose_version: Option<String>,
}

#[derive(Debug, Default)]
pub struct DockerDriver;

impl DockerDriver {
    pub fn status(&self) -> DockerStatus {
        let docker_version = command_output(&["--version"]);
        if docker_version.is_none() {
            return DockerStatus::default();
        }
        let running = command_succeeds(&["info", "--format", "{{.ServerVersion}}"]);
        let compose_version = command_output(&["compose", "version", "--short"]);
        DockerStatus {
            installed: true,
            running,
            compose_available: compose_version.is_some(),
            docker_version,
            compose_version,
        }
    }

    pub fn checks(&self, manifest: &RuntimeManifest, data_root: &Path) -> Vec<PreflightCheck> {
        let status = self.status();
        let mut checks = vec![PreflightCheck {
            id: "os".to_owned(),
            label: "Supported operating system".to_owned(),
            detail: format!("{} · {}", platform_label(), std::env::consts::ARCH),
            status: CheckStatus::Pass,
            blocking: false,
        }];
        checks.push(PreflightCheck {
            id: "docker".to_owned(),
            label: "Docker Engine".to_owned(),
            detail: if !status.installed {
                "Docker is not installed.".to_owned()
            } else if !status.running {
                "Docker is installed but the engine is not running.".to_owned()
            } else {
                status
                    .docker_version
                    .clone()
                    .unwrap_or_else(|| "Docker is running.".to_owned())
            },
            status: if status.running { CheckStatus::Pass } else { CheckStatus::Fail },
            blocking: !status.running,
        });
        checks.push(PreflightCheck {
            id: "compose".to_owned(),
            label: "Docker Compose".to_owned(),
            detail: status
                .compose_version
                .clone()
                .map(|version| format!("Compose {version} is available."))
                .unwrap_or_else(|| "Docker Compose is not available.".to_owned()),
            status: if status.compose_available { CheckStatus::Pass } else { CheckStatus::Fail },
            blocking: !status.compose_available,
        });
        let disk = available_space(data_root).ok();
        let disk_ready = disk.is_some_and(|bytes| bytes >= manifest.minimum_disk_bytes);
        let disk_status = if !disk_ready {
            CheckStatus::Fail
        } else if disk.is_some_and(|bytes| bytes < manifest.minimum_disk_bytes.saturating_mul(2)) {
            CheckStatus::Warning
        } else {
            CheckStatus::Pass
        };
        checks.push(PreflightCheck {
            id: "storage".to_owned(),
            label: "Available storage".to_owned(),
            detail: disk
                .map(|bytes| format!("{} GB available for runtime data.", bytes / 1_073_741_824))
                .unwrap_or_else(|| "Storage availability could not be measured.".to_owned()),
            status: disk_status,
            blocking: !disk_ready,
        });
        if manifest.development {
            let images_ready = [
                &manifest.images.traefik,
                &manifest.images.frontend,
                &manifest.images.runtime,
            ]
            .iter()
            .all(|image| self.verify_image(image).is_ok());
            checks.push(PreflightCheck {
                id: "development_images".to_owned(),
                label: "Development runtime images".to_owned(),
                detail: if images_ready {
                    "All locally pinned image IDs are available.".to_owned()
                } else {
                    "One or more locally pinned development images are missing or changed.".to_owned()
                },
                status: if images_ready { CheckStatus::Pass } else { CheckStatus::Fail },
                blocking: !images_ready,
            });
        }
        checks
    }

    pub fn inspect_port(&self, port: u16) -> InstallerResult<PortInspection> {
        validate_port(port)?;
        let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
        match TcpListener::bind(address) {
            Ok(listener) => {
                drop(listener);
                Ok(PortInspection {
                    port,
                    available: true,
                    message: format!("Port {port} is available on this computer."),
                })
            }
            Err(error) if error.kind() == std::io::ErrorKind::AddrInUse => Ok(PortInspection {
                port,
                available: false,
                message: format!("Port {port} is already used by another application."),
            }),
            Err(_) => Ok(PortInspection {
                port,
                available: false,
                message: format!("Port {port} cannot be bound to 127.0.0.1."),
            }),
        }
    }

    pub fn prepare_images(&self, manifest: &RuntimeManifest) -> InstallerResult<()> {
        for image in [
            &manifest.images.traefik,
            &manifest.images.frontend,
            &manifest.images.runtime,
        ] {
            if image.pull {
                run_docker(&["pull", &image.reference], "image_pull_failed")?;
            }
            self.verify_image(image)?;
        }
        Ok(())
    }

    pub fn up(&self, compose_path: &Path) -> InstallerResult<()> {
        let path = compose_path.to_string_lossy();
        let output = run_docker(
            &["compose", "-p", "brain4all_web", "-f", &path, "up", "-d", "--no-build"],
            "docker_start_failed",
        );
        match output {
            Ok(_) => Ok(()),
            Err(error) if error.message.to_ascii_lowercase().contains("port") => Err(
                InstallerError::retryable(
                    "port_in_use",
                    "The selected port became unavailable. Choose another port and retry.",
                ),
            ),
            Err(_) => Err(InstallerError::retryable(
                "docker_start_failed",
                "Docker could not start the Brain4All Web services.",
            )),
        }
    }

    pub fn down_preserving_data(&self, compose_path: &Path) -> InstallerResult<()> {
        let path = compose_path.to_string_lossy();
        run_docker(
            &["compose", "-p", "brain4all_web", "-f", &path, "down", "--remove-orphans"],
            "docker_stop_failed",
        )?;
        Ok(())
    }

    pub fn stop(&self, compose_path: &Path) -> InstallerResult<()> {
        self.run_compose(compose_path, &["stop"], "docker_stop_failed")?;
        Ok(())
    }

    pub fn restart(&self, compose_path: &Path) -> InstallerResult<()> {
        self.run_compose(compose_path, &["restart"], "docker_restart_failed")?;
        Ok(())
    }

    pub fn runtime_status(&self, compose_path: &Path, port: u16) -> (RuntimeState, Vec<ServiceStatus>) {
        let definitions = [
            ("traefik", LogService::Traefik, "Traefik ingress"),
            ("frontend", LogService::Frontend, "Web interface"),
            ("runtime", LogService::Runtime, "Hermes runtime"),
        ];
        let services: Vec<ServiceStatus> = definitions
            .into_iter()
            .map(|(service, id, name)| {
                let container_id = self.compose_output(compose_path, &["ps", "--all", "-q", service]);
                let state = container_id
                    .as_deref()
                    .and_then(container_state)
                    .unwrap_or(ServiceState::Missing);
                let detail = if service == "traefik" {
                    format!("127.0.0.1:{port} → private :5152")
                } else {
                    "Docker-internal only".to_owned()
                };
                ServiceStatus { id, name: name.to_owned(), state, detail }
            })
            .collect();
        let running = services.iter().filter(|service| service.state == ServiceState::Running).count();
        let state = if running == services.len() {
            RuntimeState::Running
        } else if running == 0 && services.iter().all(|service| matches!(service.state, ServiceState::Stopped | ServiceState::Missing)) {
            RuntimeState::Stopped
        } else {
            RuntimeState::Degraded
        };
        (state, services)
    }

    pub fn logs(&self, compose_path: &Path, service: LogService) -> InstallerResult<(Vec<String>, bool)> {
        let mut arguments = vec!["logs", "--no-color", "--timestamps", "--tail", "251"];
        match service {
            LogService::All => {}
            LogService::Traefik => arguments.push("traefik"),
            LogService::Frontend => arguments.push("frontend"),
            LogService::Runtime => arguments.push("runtime"),
        }
        let output = self.run_compose(compose_path, &arguments, "docker_logs_failed")?;
        let combined = if output.stdout.is_empty() { output.stderr } else { output.stdout };
        let mut lines: Vec<String> = String::from_utf8_lossy(&combined)
            .lines()
            .map(redact_log_line)
            .collect();
        let truncated = lines.len() > 250;
        if truncated { lines.drain(..lines.len() - 250); }
        Ok((lines, truncated))
    }

    pub fn wait_for_health(
        &self,
        url: &str,
        timeout: Duration,
    ) -> InstallerResult<()> {
        let client = reqwest::blocking::Client::builder()
            .connect_timeout(Duration::from_secs(2))
            .timeout(Duration::from_secs(4))
            .build()
            .map_err(|_| InstallerError::retryable("health_client_failed", "Could not initialize the health check."))?;
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if client
                .get(url)
                .send()
                .is_ok_and(|response| response.status().is_success())
            {
                return Ok(());
            }
            thread::sleep(Duration::from_secs(2));
        }
        Err(InstallerError::retryable(
            "health_timeout",
            "Brain4All started but did not become healthy in time.",
        ))
    }

    pub fn is_healthy(&self, url: &str) -> bool {
        reqwest::blocking::Client::builder()
            .connect_timeout(Duration::from_secs(1))
            .timeout(Duration::from_secs(2))
            .build()
            .ok()
            .and_then(|client| client.get(url).send().ok())
            .is_some_and(|response| response.status().is_success())
    }

    fn verify_image(&self, image: &ImageManifest) -> InstallerResult<()> {
        let actual = command_output(&["image", "inspect", "--format", "{{.Id}}", &image.reference]);
        if actual.as_deref() != Some(image.expected_id.as_str()) {
            return Err(InstallerError::retryable(
                "image_verification_failed",
                format!("The verified runtime image {} is not available.", image.reference),
            ));
        }
        Ok(())
    }

    fn run_compose(&self, compose_path: &Path, arguments: &[&str], code: &str) -> InstallerResult<Output> {
        let path = compose_path.to_string_lossy();
        let mut fixed = vec!["compose", "-p", "brain4all_web", "-f", path.as_ref()];
        fixed.extend_from_slice(arguments);
        run_docker(&fixed, code)
    }

    fn compose_output(&self, compose_path: &Path, arguments: &[&str]) -> Option<String> {
        self.run_compose(compose_path, arguments, "docker_inspect_failed")
            .ok()
            .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
            .filter(|output| !output.is_empty())
    }
}

fn container_state(container_id: &str) -> Option<ServiceState> {
    let state = command_output(&[
        "inspect",
        "--format",
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        container_id,
    ])?;
    Some(match state.as_str() {
        "running" | "healthy" => ServiceState::Running,
        "unhealthy" => ServiceState::Unhealthy,
        "exited" | "created" | "paused" | "restarting" => ServiceState::Stopped,
        _ => ServiceState::Missing,
    })
}

fn redact_log_line(line: &str) -> String {
    let lower = line.to_ascii_lowercase();
    if ["authorization:", "token=", "secret=", "password="].iter().any(|needle| lower.contains(needle)) {
        "[REDACTED sensitive log line]".to_owned()
    } else {
        line.to_owned()
    }
}

fn command_succeeds(arguments: &[&str]) -> bool {
    Command::new("docker")
        .args(arguments)
        .output()
        .is_ok_and(|output| output.status.success())
}

fn command_output(arguments: &[&str]) -> Option<String> {
    Command::new("docker")
        .args(arguments)
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .filter(|output| !output.is_empty())
}

fn run_docker(arguments: &[&str], code: &str) -> InstallerResult<Output> {
    let output = Command::new("docker")
        .args(arguments)
        .output()
        .map_err(|_| InstallerError::retryable("docker_unavailable", "Docker could not be started."))?;
    if output.status.success() {
        return Ok(output);
    }
    let stderr = String::from_utf8_lossy(&output.stderr).to_ascii_lowercase();
    let safe_message = if stderr.contains("port is already allocated") || stderr.contains("address already in use") {
        "Docker reported that the selected port is already in use."
    } else if stderr.contains("permission denied") {
        "Docker denied access. Check the current user's Docker permissions."
    } else if stderr.contains("cannot connect") || stderr.contains("daemon") {
        "Docker Engine is not running."
    } else {
        "Docker could not complete the requested installer operation."
    };
    Err(InstallerError::retryable(code, safe_message))
}

pub fn validate_port(port: u16) -> InstallerResult<()> {
    if port < 1024 {
        return Err(InstallerError::retryable(
            "port_invalid",
            "Choose a port from 1024 through 65535.",
        ));
    }
    Ok(())
}

pub fn platform() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

pub fn platform_label() -> &'static str {
    if cfg!(target_os = "windows") {
        "Windows"
    } else if cfg!(target_os = "macos") {
        "macOS"
    } else {
        "Linux"
    }
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use super::{DockerDriver, redact_log_line, validate_port};

    #[test]
    fn privileged_ports_are_rejected() {
        assert_eq!(validate_port(80).expect_err("port should fail").code, "port_invalid");
        assert!(validate_port(5152).is_ok());
    }

    #[test]
    fn occupied_loopback_port_is_reported() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        let port = listener.local_addr().expect("address").port();
        let result = DockerDriver.inspect_port(port).expect("inspection");
        assert!(!result.available);
        assert!(result.message.contains("already used"));
    }

    #[test]
    fn sensitive_log_lines_are_replaced() {
        assert_eq!(redact_log_line("authorization: bearer private"), "[REDACTED sensitive log line]");
        assert_eq!(redact_log_line("runtime ready"), "runtime ready");
    }
}
