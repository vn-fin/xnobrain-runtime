use serde::{Deserialize, Serialize};

use super::{InstallerError, InstallerResult};

const DEVELOPMENT_MANIFEST: &str =
    include_str!("../../../resources/runtime-manifest.dev.json");

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ImageManifest {
    pub reference: String,
    pub expected_id: String,
    pub pull: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RuntimeImages {
    pub traefik: ImageManifest,
    pub frontend: ImageManifest,
    pub runtime: ImageManifest,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RuntimeManifest {
    pub schema_version: u32,
    pub release: String,
    pub development: bool,
    pub default_host_port: u16,
    pub minimum_disk_bytes: u64,
    pub health_path: String,
    pub health_timeout_seconds: u64,
    pub images: RuntimeImages,
}

impl RuntimeManifest {
    pub fn bundled() -> InstallerResult<Self> {
        let manifest: Self = serde_json::from_str(DEVELOPMENT_MANIFEST).map_err(|_| {
            InstallerError::terminal(
                "manifest_invalid",
                "The bundled Brain4All runtime manifest is invalid.",
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
        for image in [
            &self.images.traefik,
            &self.images.frontend,
            &self.images.runtime,
        ] {
            if image.reference.trim().is_empty()
                || !image.expected_id.starts_with("sha256:")
                || image.expected_id.len() != 71
            {
                return Err(InstallerError::terminal(
                    "manifest_image_invalid",
                    "The runtime manifest contains an invalid image reference.",
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
    fn bundled_manifest_is_valid_and_development_only() {
        let manifest = RuntimeManifest::bundled().expect("bundled manifest should validate");
        assert!(manifest.development);
        assert_eq!(manifest.default_host_port, 5152);
        assert!(!manifest.images.frontend.pull);
    }
}
