use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

use sha2::{Digest, Sha256};

use crate::domain::{
    DockerInstallProgress, DockerInstallResult, DockerInstallerArtifact, InstallerError,
    InstallerResult, RuntimeManifest,
};

#[derive(Debug, Default)]
pub struct DockerInstallerDriver;

impl DockerInstallerDriver {
    pub fn install(
        &self,
        manifest: &RuntimeManifest,
        data_root: &Path,
        resource_root: &Path,
        mut progress: impl FnMut(DockerInstallProgress) -> InstallerResult<()>,
    ) -> InstallerResult<DockerInstallResult> {
        if cfg!(target_os = "windows") && !windows_wsl_ready() {
            progress(DockerInstallProgress::new(
                20,
                "Preparing WSL 2",
                "Windows will ask for permission to enable the Docker backend.",
            ))?;
            install_windows_wsl()?;
            progress(DockerInstallProgress::new(
                100,
                "Windows restart required",
                "Restart Windows, reopen XNOBrain, and continue Docker installation.",
            ))?;
            return Ok(DockerInstallResult {
                restart_required: true,
                message: "WSL 2 was enabled. Restart Windows, then reopen XNOBrain to finish Docker installation.".to_owned(),
            });
        }
        if cfg!(target_os = "linux") {
            progress(DockerInstallProgress::new(
                15,
                "Requesting permission",
                "Your system will ask before installing Docker Engine and Compose.",
            ))?;
            install_linux(resource_root)?;
            progress(DockerInstallProgress::new(
                100,
                "Docker installed",
                "Sign out and back in if Docker access is not available yet.",
            ))?;
            return Ok(DockerInstallResult {
                restart_required: true,
                message: "Docker Engine and Compose were installed. Sign out and back in once if this app cannot access Docker yet.".to_owned(),
            });
        }

        let artifact = select_artifact(manifest)?;
        let extension = if cfg!(target_os = "windows") {
            "exe"
        } else {
            "dmg"
        };
        let download_dir = data_root.join("downloads");
        fs::create_dir_all(&download_dir).map_err(|error| InstallerError::io("create", &error))?;
        let target = download_dir.join(format!("docker-desktop.{extension}"));
        download_verified(artifact, &target, &mut progress)?;
        progress(DockerInstallProgress::new(
            82,
            "Installing Docker",
            "Follow the operating system prompt. Docker's license is accepted only by you.",
        ))?;

        if cfg!(target_os = "windows") {
            install_windows(&target)?;
        } else {
            install_macos(&target)?;
        }
        progress(DockerInstallProgress::new(
            100,
            "Docker setup opened",
            "Complete Docker's first-run agreement, then check the system again.",
        ))?;
        Ok(DockerInstallResult {
            restart_required: false,
            message: "Docker setup completed. Start Docker Desktop and accept its agreement, then check the system again.".to_owned(),
        })
    }
}

fn windows_wsl_ready() -> bool {
    Command::new("wsl.exe")
        .arg("--version")
        .status()
        .is_ok_and(|status| status.success())
}

fn install_windows_wsl() -> InstallerResult<()> {
    run(
        Command::new("powershell.exe").args([
            "-NoProfile",
            "-Command",
            "Start-Process -FilePath wsl.exe -Verb RunAs -Wait -ArgumentList '--install','--no-distribution'",
        ]),
        "wsl_install_failed",
    )
}

fn select_artifact(manifest: &RuntimeManifest) -> InstallerResult<&DockerInstallerArtifact> {
    if cfg!(target_os = "windows") {
        if std::env::consts::ARCH != "x86_64" {
            return Err(InstallerError::terminal(
                "docker_arch_unsupported",
                "This release supports Docker Desktop on Windows x86-64 only.",
            ));
        }
        return Ok(&manifest.docker_installers.windows_amd64);
    }
    match std::env::consts::ARCH {
        "aarch64" => Ok(&manifest.docker_installers.macos_arm64),
        "x86_64" => Ok(&manifest.docker_installers.macos_amd64),
        _ => Err(InstallerError::terminal(
            "docker_arch_unsupported",
            "This macOS architecture is not supported by the bundled Docker installer manifest.",
        )),
    }
}

fn download_verified(
    artifact: &DockerInstallerArtifact,
    target: &Path,
    progress: &mut impl FnMut(DockerInstallProgress) -> InstallerResult<()>,
) -> InstallerResult<()> {
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(1800))
        .build()
        .map_err(|_| {
            InstallerError::retryable(
                "download_client_failed",
                "Could not initialize the Docker download.",
            )
        })?;
    let mut response = client
        .get(&artifact.url)
        .send()
        .and_then(reqwest::blocking::Response::error_for_status)
        .map_err(|_| {
            InstallerError::retryable(
                "docker_download_failed",
                "Could not download Docker from the configured official URL.",
            )
        })?;
    let total = response.content_length();
    let temporary = target.with_extension("download");
    let mut output =
        File::create(&temporary).map_err(|error| InstallerError::io("write", &error))?;
    let mut digest = Sha256::new();
    let mut downloaded = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = response.read(&mut buffer).map_err(|_| {
            InstallerError::retryable(
                "docker_download_failed",
                "The Docker download was interrupted.",
            )
        })?;
        if read == 0 {
            break;
        }
        output
            .write_all(&buffer[..read])
            .map_err(|error| InstallerError::io("write", &error))?;
        digest.update(&buffer[..read]);
        downloaded += read as u64;
        let percent = total
            .map(|bytes| 5 + ((downloaded.saturating_mul(70) / bytes.max(1)) as u8).min(70))
            .unwrap_or(40);
        progress(DockerInstallProgress::new(
            percent,
            "Downloading Docker",
            "Downloading the verified official installer.",
        ))?;
    }
    output
        .sync_all()
        .map_err(|error| InstallerError::io("write", &error))?;
    let actual = format!("{:x}", digest.finalize());
    if actual != artifact.sha256 {
        let _ = fs::remove_file(&temporary);
        return Err(InstallerError::terminal(
            "docker_checksum_mismatch",
            "The Docker installer checksum did not match the release manifest.",
        ));
    }
    fs::rename(&temporary, target).map_err(|error| InstallerError::io("replace", &error))
}

fn install_linux(resource_root: &Path) -> InstallerResult<()> {
    let script = [
        resource_root.join("install-docker-linux.sh"),
        resource_root.join("resources/install-docker-linux.sh"),
    ]
    .into_iter()
    .find(|path| path.is_file())
    .ok_or_else(|| {
        InstallerError::terminal(
            "docker_helper_missing",
            "The Linux Docker installer helper is missing.",
        )
    })?;
    run(
        Command::new("pkexec").arg("/bin/sh").arg(script),
        "docker_install_failed",
    )
}

fn install_windows(path: &Path) -> InstallerResult<()> {
    run(
        Command::new(path).args(["install", "--user"]),
        "docker_install_failed",
    )?;
    let desktop = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .map(|root| root.join("Programs/Docker/Docker/Docker Desktop.exe"));
    if let Some(executable) = desktop.filter(|path| path.is_file()) {
        Command::new(executable).spawn().map_err(|_| {
            InstallerError::retryable(
                "docker_start_failed",
                "Docker Desktop was installed but could not be started.",
            )
        })?;
    }
    Ok(())
}

fn install_macos(path: &Path) -> InstallerResult<()> {
    run(
        Command::new("hdiutil")
            .args(["attach", "-nobrowse"])
            .arg(path),
        "docker_mount_failed",
    )?;
    let install_result = run(
        Command::new("osascript").args([
            "-e",
            "do shell script \"/usr/bin/ditto /Volumes/Docker/Docker.app /Applications/Docker.app\" with administrator privileges",
        ]),
        "docker_install_failed",
    );
    let _ = Command::new("hdiutil")
        .args(["detach", "/Volumes/Docker"])
        .status();
    install_result?;
    Command::new("open")
        .args(["-a", "Docker"])
        .spawn()
        .map_err(|_| {
            InstallerError::retryable(
                "docker_start_failed",
                "Docker Desktop was installed but could not be opened.",
            )
        })?;
    Ok(())
}

fn run(command: &mut Command, code: &str) -> InstallerResult<()> {
    let status = command.status().map_err(|_| {
        InstallerError::retryable(code, "The Docker installer could not be started.")
    })?;
    if status.success() {
        Ok(())
    } else {
        Err(InstallerError::retryable(
            code,
            "Docker installation did not complete successfully.",
        ))
    }
}
