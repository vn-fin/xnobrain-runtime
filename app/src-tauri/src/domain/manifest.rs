use serde::{Deserialize, Serialize};

use super::{InstallerError, InstallerResult};

const BUNDLED_MANIFEST: &str = include_str!(concat!(env!("OUT_DIR"), "/runtime-manifest.json"));

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ImageManifest {
    pub reference: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RuntimeImages {
    pub traefik: ImageManifest,
    pub xnobrain: ImageManifest,
    pub control: ImageManifest,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WebAuthManifest {
    pub base_url: String,
    pub mode: String,
    pub provider: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct DockerInstallerArtifact {
    pub url: String,
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct DockerInstallers {
    pub windows_amd64: DockerInstallerArtifact,
    pub macos_arm64: DockerInstallerArtifact,
    pub macos_amd64: DockerInstallerArtifact,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RuntimeManifest {
    pub schema_version: u32,
    pub release: String,
    pub default_host_port: u16,
    pub minimum_disk_bytes: u64,
    pub health_path: String,
    pub health_timeout_seconds: u64,
    pub web_auth: WebAuthManifest,
    pub docker_installers: DockerInstallers,
    pub images: RuntimeImages,
}

impl RuntimeManifest {
    pub fn bundled() -> InstallerResult<Self> {
        let manifest: Self = serde_json::from_str(BUNDLED_MANIFEST).map_err(|_| {
            InstallerError::terminal(
                "manifest_invalid",
                "The bundled XNOBrain runtime manifest is invalid.",
            )
        })?;
        manifest.validate()?;
        Ok(manifest)
    }

    fn validate(&self) -> InstallerResult<()> {
        if self.schema_version != 1 {
            return Err(InstallerError::terminal(
                "manifest_schema_unsupported",
                "This installer cannot read the bundled runtime manifest.",
            ));
        }
        if !(1024..=65535).contains(&self.default_host_port) {
            return Err(InstallerError::terminal(
                "manifest_port_invalid",
                "The runtime manifest contains an invalid default port.",
            ));
        }
        if !self.web_auth.base_url.starts_with("https://")
            || self.web_auth.base_url.ends_with('/')
            || self.web_auth.mode != "required"
            || self.web_auth.provider != "xno-firebase"
        {
            return Err(InstallerError::terminal(
                "manifest_auth_invalid",
                "The runtime manifest contains an invalid Web authentication contract.",
            ));
        }
        for image in [
            &self.images.traefik,
            &self.images.xnobrain,
            &self.images.control,
        ] {
            let Some((repository, digest)) = image.reference.rsplit_once("@sha256:") else {
                return Err(InstallerError::terminal(
                    "manifest_image_invalid",
                    "The runtime manifest contains a mutable image reference.",
                ));
            };
            if repository.is_empty()
                || digest.len() != 64
                || !digest
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
            {
                return Err(InstallerError::terminal(
                    "manifest_image_invalid",
                    "The runtime manifest contains an invalid image reference.",
                ));
            }
        }
        for installer in [
            &self.docker_installers.windows_amd64,
            &self.docker_installers.macos_arm64,
            &self.docker_installers.macos_amd64,
        ] {
            if !installer.url.starts_with("https://")
                || installer.sha256.len() != 64
                || !installer
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
            {
                return Err(InstallerError::terminal(
                    "manifest_docker_installer_invalid",
                    "The runtime manifest contains an invalid Docker installer artifact.",
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::RuntimeManifest;

    #[test]
    fn bundled_manifest_is_valid_and_immutable() {
        let manifest = RuntimeManifest::bundled().expect("bundled manifest should validate");
        assert_eq!(manifest.default_host_port, 5152);
        assert!(manifest.images.xnobrain.reference.contains("@sha256:"));
        assert_eq!(manifest.web_auth.base_url, "https://api.dev.xnoquant.io");
        assert_eq!(manifest.web_auth.mode, "required");
        assert_eq!(manifest.web_auth.provider, "xno-firebase");
    }
}
