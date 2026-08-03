use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use crate::domain::{InstallState, InstallerError, InstallerResult};

#[derive(Debug)]
pub struct StateStore {
    root: PathBuf,
}

impl StateStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn compose_path(&self) -> PathBuf {
        self.root.join("docker-web.compose.yaml")
    }

    pub fn load(&self) -> InstallerResult<Option<InstallState>> {
        let path = self.root.join("install-state.json");
        let bytes = match fs::read(&path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(InstallerError::io("read", &error)),
        };
        serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|_| InstallerError::retryable("state_invalid", "The saved installation state is damaged."))
    }

    pub fn save(&self, state: &InstallState) -> InstallerResult<()> {
        fs::create_dir_all(&self.root).map_err(|error| InstallerError::io("create", &error))?;
        let payload = serde_json::to_vec_pretty(state).map_err(|_| {
            InstallerError::terminal("state_serialize_failed", "Could not prepare installer state.")
        })?;
        atomic_write(&self.root.join("install-state.json"), &payload)
    }

    pub fn write_compose(&self, contents: &str) -> InstallerResult<PathBuf> {
        fs::create_dir_all(&self.root).map_err(|error| InstallerError::io("create", &error))?;
        let path = self.compose_path();
        atomic_write(&path, contents.as_bytes())?;
        Ok(path)
    }

    pub fn clear_state(&self) -> InstallerResult<()> {
        let path = self.root.join("install-state.json");
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(InstallerError::io("remove", &error)),
        }
    }
}

fn atomic_write(path: &Path, payload: &[u8]) -> InstallerResult<()> {
    let temporary = path.with_extension("tmp");
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temporary)
        .map_err(|error| InstallerError::io("write", &error))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|error| InstallerError::io("secure", &error))?;
    }

    file.write_all(payload)
        .and_then(|_| file.sync_all())
        .map_err(|error| InstallerError::io("write", &error))?;
    fs::rename(&temporary, path).map_err(|error| InstallerError::io("replace", &error))?;
    if let Some(parent) = path.parent()
        && let Ok(directory) = OpenOptions::new().read(true).open(parent)
    {
        let _ = directory.sync_all();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::StateStore;
    use crate::domain::InstallState;

    #[test]
    fn state_round_trips_atomically() {
        let temporary = tempdir().expect("tempdir");
        let store = StateStore::new(temporary.path().to_owned());
        let state = InstallState {
            schema_version: 1,
            release: "dev".to_owned(),
            port: 5152,
            web_url: "http://127.0.0.1:5152".to_owned(),
            compose_path: store.compose_path(),
            installed: true,
        };
        store.save(&state).expect("save");
        let loaded = store.load().expect("load").expect("state");
        assert_eq!(loaded.port, 5152);
        assert!(loaded.installed);
    }
}
