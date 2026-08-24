use std::{fs, path::PathBuf, sync::Mutex, time::Duration};

use tauri::ipc::Channel;

use crate::{
    domain::{
        DockerInstallProgress, DockerInstallResult, InstallPhase, InstallProgress, InstallRequest,
        InstallResult, InstallState, InstallerError, InstallerResult, LogService, PortInspection,
        RuntimeAction, RuntimeLogs, RuntimeManifest, RuntimeOverview, RuntimeState,
        SystemInspection,
    },
    drivers::docker::{
        ComposeSpec, DockerDriver, DockerInstallerDriver, platform, platform_label, validate_port,
    },
    persistence::StateStore,
};

pub struct InstallerService {
    manifest: RuntimeManifest,
    docker: DockerDriver,
    docker_installer: DockerInstallerDriver,
    store: StateStore,
    resource_root: PathBuf,
    mutation: Mutex<()>,
}

impl InstallerService {
    pub fn new(data_root: PathBuf, resource_root: PathBuf) -> InstallerResult<Self> {
        fs::create_dir_all(&data_root).map_err(|error| InstallerError::io("create", &error))?;
        Ok(Self {
            manifest: RuntimeManifest::bundled()?,
            docker: DockerDriver,
            docker_installer: DockerInstallerDriver,
            store: StateStore::new(data_root),
            resource_root,
            mutation: Mutex::new(()),
        })
    }

    pub fn install_docker(
        &self,
        progress: &Channel<DockerInstallProgress>,
    ) -> InstallerResult<DockerInstallResult> {
        let _guard = self.mutation.try_lock().map_err(|_| {
            InstallerError::retryable(
                "operation_in_progress",
                "Another XNOBrain installation operation is already running.",
            )
        })?;
        self.docker_installer.install(
            &self.manifest,
            self.store.root(),
            &self.resource_root,
            |update| {
                progress.send(update).map_err(|_| {
                    InstallerError::retryable(
                        "progress_channel_closed",
                        "The installer window stopped receiving progress.",
                    )
                })
            },
        )
    }

    pub fn inspect_system(&self) -> InstallerResult<SystemInspection> {
        let status = self.docker.status();
        let state = self.store.load()?;
        let healthy = state
            .as_ref()
            .filter(|state| state.installed)
            .is_some_and(|state| self.docker.is_healthy(&health_url(state)));
        Ok(SystemInspection {
            platform: platform().to_owned(),
            platform_label: platform_label().to_owned(),
            architecture: std::env::consts::ARCH.to_owned(),
            docker_installed: status.installed,
            docker_running: status.running,
            compose_available: status.compose_available,
            docker_version: status.docker_version,
            compose_version: status.compose_version,
            default_port: self.manifest.default_host_port,
            installed: state.as_ref().is_some_and(|state| state.installed),
            healthy,
            selected_port: state.as_ref().map(|state| state.port),
            web_url: state.as_ref().map(|state| state.web_url.clone()),
            checks: self.docker.checks(&self.manifest, self.store.root()),
        })
    }

    pub fn inspect_port(&self, port: u16) -> InstallerResult<PortInspection> {
        self.docker.inspect_port(port)
    }

    pub fn install_web(
        &self,
        request: InstallRequest,
        progress: &Channel<InstallProgress>,
    ) -> InstallerResult<InstallResult> {
        let _guard = self.mutation.try_lock().map_err(|_| {
            InstallerError::retryable(
                "installation_in_progress",
                "Another XNOBrain installation operation is already running.",
            )
        })?;
        validate_port(request.port)?;
        send_progress(
            progress,
            InstallPhase::Validating,
            8,
            "Validating installation",
            "Checking Docker and port availability",
        )?;
        let status = self.docker.status();
        if !status.running || !status.compose_available {
            return Err(InstallerError::retryable(
                "docker_not_ready",
                "Install and start Docker before installing XNOBrain.",
            ));
        }
        if !self.docker.inspect_port(request.port)?.available {
            return Err(InstallerError::retryable(
                "port_in_use",
                "The selected port is already used. Choose another port and retry.",
            ));
        }
        send_progress(
            progress,
            InstallPhase::Pulling,
            32,
            "Verifying runtime",
            "Checking immutable XNOBrain image digests",
        )?;
        self.docker.prepare_images(&self.manifest)?;

        send_progress(
            progress,
            InstallPhase::Preparing,
            50,
            "Preparing XNOBrain",
            "Creating secure local configuration",
        )?;
        let compose = ComposeSpec {
            manifest: &self.manifest,
            port: request.port,
        }
        .render();
        let traefik_dynamic = ComposeSpec {
            manifest: &self.manifest,
            port: request.port,
        }
        .render_traefik_dynamic();
        ensure_single_traefik_port(&compose, request.port)?;
        self.store.write_traefik_dynamic(&traefik_dynamic)?;
        let compose_path = self.store.write_compose(&compose)?;

        send_progress(
            progress,
            InstallPhase::Creating,
            68,
            "Creating services",
            "Configuring Traefik as the only local entrypoint",
        )?;
        send_progress(
            progress,
            InstallPhase::Starting,
            80,
            "Starting XNOBrain",
            "Starting the Docker Web runtime",
        )?;
        self.docker.up(&compose_path)?;

        let web_url = format!("http://127.0.0.1:{}", request.port);
        let health_url = format!("{web_url}{}", self.manifest.health_path);
        send_progress(
            progress,
            InstallPhase::HealthCheck,
            92,
            "Checking health",
            format!("Waiting at 127.0.0.1:{}", request.port),
        )?;
        self.docker.wait_for_health(
            &health_url,
            Duration::from_secs(self.manifest.health_timeout_seconds),
        )?;

        let state = InstallState {
            schema_version: 1,
            release: self.manifest.release.clone(),
            port: request.port,
            web_url: web_url.clone(),
            compose_path: compose_path.clone(),
            installed: true,
        };
        self.store.save(&state)?;
        send_progress(
            progress,
            InstallPhase::Ready,
            100,
            "XNOBrain is ready",
            "The Web version is healthy",
        )?;
        Ok(InstallResult {
            port: request.port,
            web_url,
            compose_path: compose_path.to_string_lossy().to_string(),
        })
    }

    pub fn web_url(&self) -> InstallerResult<String> {
        self.store
            .load()?
            .filter(|state| state.installed)
            .map(|state| state.web_url)
            .ok_or_else(|| {
                InstallerError::retryable("not_installed", "XNOBrain Web is not installed yet.")
            })
    }

    pub fn runtime_overview(&self) -> InstallerResult<RuntimeOverview> {
        let docker = self.docker.status();
        let Some(state) = self.store.load()? else {
            return Ok(RuntimeOverview {
                state: RuntimeState::NotInstalled,
                web_url: format!("http://127.0.0.1:{}", self.manifest.default_host_port),
                port: self.manifest.default_host_port,
                docker_version: docker.docker_version,
                compose_version: docker.compose_version,
                services: Vec::new(),
            });
        };
        let (runtime_state, services) = self.docker.runtime_status(&state.compose_path, state.port);
        Ok(RuntimeOverview {
            state: runtime_state,
            web_url: state.web_url,
            port: state.port,
            docker_version: docker.docker_version,
            compose_version: docker.compose_version,
            services,
        })
    }

    pub fn manage_runtime(&self, action: RuntimeAction) -> InstallerResult<RuntimeOverview> {
        let _guard = self.mutation.try_lock().map_err(|_| {
            InstallerError::retryable(
                "operation_in_progress",
                "Another XNOBrain operation is already running.",
            )
        })?;
        let state = self
            .store
            .load()?
            .filter(|state| state.installed)
            .ok_or_else(|| {
                InstallerError::retryable("not_installed", "XNOBrain Web is not installed yet.")
            })?;
        match action {
            RuntimeAction::Start => {
                self.docker.up(&state.compose_path)?;
                self.docker.wait_for_health(
                    &health_url(&state),
                    Duration::from_secs(self.manifest.health_timeout_seconds),
                )?;
            }
            RuntimeAction::Stop => self.docker.stop(&state.compose_path)?,
            RuntimeAction::Restart => {
                self.docker.restart(&state.compose_path)?;
                self.docker.wait_for_health(
                    &health_url(&state),
                    Duration::from_secs(self.manifest.health_timeout_seconds),
                )?;
            }
        }
        self.runtime_overview()
    }

    pub fn logs(&self, service: LogService) -> InstallerResult<RuntimeLogs> {
        let state = self
            .store
            .load()?
            .filter(|state| state.installed)
            .ok_or_else(|| {
                InstallerError::retryable("not_installed", "XNOBrain Web is not installed yet.")
            })?;
        let (lines, truncated) = self.docker.logs(&state.compose_path, service)?;
        Ok(RuntimeLogs {
            service,
            lines,
            truncated,
        })
    }

    pub fn reset_preserving_data(&self) -> InstallerResult<()> {
        let _guard = self.mutation.try_lock().map_err(|_| {
            InstallerError::retryable(
                "installation_in_progress",
                "Another installation operation is running.",
            )
        })?;
        if let Some(state) = self.store.load()? {
            self.docker.down_preserving_data(&state.compose_path)?;
        }
        self.store.clear_state()
    }
}

fn send_progress(
    channel: &Channel<InstallProgress>,
    phase: InstallPhase,
    percent: u8,
    title: &str,
    detail: impl Into<String>,
) -> InstallerResult<()> {
    channel
        .send(InstallProgress::new(phase, percent, title, detail))
        .map_err(|_| {
            InstallerError::retryable(
                "progress_channel_closed",
                "The installer window stopped receiving progress.",
            )
        })
}

fn ensure_single_traefik_port(compose: &str, port: u16) -> InstallerResult<()> {
    let mapping = format!("127.0.0.1:{port}:5152");
    if compose.matches("    ports:\n").count() != 1 || !compose.contains(&mapping) {
        return Err(InstallerError::terminal(
            "ingress_invariant_failed",
            "The generated Docker configuration did not pass its ingress safety check.",
        ));
    }
    Ok(())
}

fn health_url(state: &InstallState) -> String {
    format!("{}/api/brain/v1/health", state.web_url)
}

#[cfg(test)]
mod tests {
    use super::{ensure_single_traefik_port, health_url};
    use crate::domain::InstallState;

    #[test]
    fn ingress_guard_rejects_missing_or_duplicate_ports() {
        assert!(ensure_single_traefik_port("services: {}", 5152).is_err());
        let duplicate = "    ports:\n      - 127.0.0.1:5152:5152\n    ports:\n";
        assert!(ensure_single_traefik_port(duplicate, 5152).is_err());
    }

    #[test]
    fn runtime_health_uses_the_public_unauthenticated_route() {
        let state = InstallState {
            schema_version: 1,
            release: "test".into(),
            port: 5152,
            web_url: "http://127.0.0.1:5152".into(),
            compose_path: "docker-web.compose.yaml".into(),
            installed: true,
        };
        assert_eq!(
            health_url(&state),
            "http://127.0.0.1:5152/api/brain/v1/health"
        );
    }
}
