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
    pub brain: ImageManifest,
    pub runtime_api: ImageManifest,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WebBuildManifest {
    pub auth_base_url: String,
    pub brain_control_base_url: String,
    pub auth_mode: String,
    pub auth_provider: String,
    pub firebase_api_key_sha256: String,
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
    pub web_build: WebBuildManifest,
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
        if self.schema_version != 2 {
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
        if !valid_https_origin(&self.web_build.auth_base_url)
            || !valid_https_origin(&self.web_build.brain_control_base_url)
            || self.web_build.auth_mode != "required"
            || self.web_build.auth_provider != "xno-firebase"
            || self.web_build.firebase_api_key_sha256.len() != 64
            || !self
                .web_build
                .firebase_api_key_sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        {
            return Err(InstallerError::terminal(
                "manifest_auth_invalid",
                "The runtime manifest contains an invalid Web authentication contract.",
            ));
        }
        for image in [
            &self.images.traefik,
            &self.images.brain,
            &self.images.runtime_api,
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

fn valid_https_origin(value: &str) -> bool {
    value.starts_with("https://") && !value.ends_with('/') && !value.contains(char::is_whitespace)
}

#[cfg(test)]
mod tests {
    use super::RuntimeManifest;

    #[test]
    fn bundled_manifest_is_valid_and_immutable() {
        let manifest = RuntimeManifest::bundled().expect("bundled manifest should validate");
        assert_eq!(manifest.default_host_port, 5152);
        assert!(manifest.images.brain.reference.contains("@sha256:"));
        assert!(manifest.images.runtime_api.reference.contains("@sha256:"));
        assert_eq!(
            manifest.web_build.auth_base_url,
            "https://api.dev.xnoquant.io"
        );
        assert_eq!(
            manifest.web_build.brain_control_base_url,
            "https://api.dev.xnoquant.io"
        );
        assert_eq!(manifest.web_build.auth_mode, "required");
        assert_eq!(manifest.web_build.auth_provider, "xno-firebase");
        assert_eq!(manifest.web_build.firebase_api_key_sha256.len(), 64);
    }
}
