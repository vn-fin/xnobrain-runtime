use std::sync::Arc;

use tauri::{AppHandle, State, Window, ipc::Channel};
use tauri_plugin_opener::OpenerExt;

use crate::{
    AppState,
    domain::{
        InstallProgress, InstallRequest, InstallResult, InstallerError, LogService, PortInspection,
        RuntimeAction, RuntimeLogs, RuntimeOverview, SystemInspection,
    },
};

#[tauri::command]
pub async fn inspect_system(state: State<'_, AppState>) -> Result<SystemInspection, InstallerError> {
    let service = Arc::clone(&state.service);
    tauri::async_runtime::spawn_blocking(move || service.inspect_system())
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "The system check stopped unexpectedly."))?
}

#[tauri::command]
pub async fn inspect_port(state: State<'_, AppState>, port: u16) -> Result<PortInspection, InstallerError> {
    let service = Arc::clone(&state.service);
    tauri::async_runtime::spawn_blocking(move || service.inspect_port(port))
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "The port check stopped unexpectedly."))?
}

#[tauri::command]
pub async fn install_web(
    state: State<'_, AppState>,
    request: InstallRequest,
    progress: Channel<InstallProgress>,
) -> Result<InstallResult, InstallerError> {
    let service = Arc::clone(&state.service);
    tauri::async_runtime::spawn_blocking(move || service.install_web(request, &progress))
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "The installation stopped unexpectedly."))?
}

#[tauri::command]
pub async fn open_web(app: AppHandle, state: State<'_, AppState>) -> Result<(), InstallerError> {
    let service = Arc::clone(&state.service);
    let url = tauri::async_runtime::spawn_blocking(move || service.web_url())
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "Could not read the Web address."))??;
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|_| InstallerError::retryable("browser_open_failed", "Could not open the default browser."))
}

#[tauri::command]
pub fn open_docker_help(app: AppHandle) -> Result<(), InstallerError> {
    let url = if cfg!(target_os = "windows") {
        "https://docs.docker.com/desktop/setup/install/windows-install/"
    } else if cfg!(target_os = "macos") {
        "https://docs.docker.com/desktop/setup/install/mac-install/"
    } else {
        "https://docs.docker.com/engine/install/"
    };
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|_| InstallerError::retryable("browser_open_failed", "Could not open Docker's official setup guide."))
}

#[tauri::command]
pub async fn inspect_runtime(state: State<'_, AppState>) -> Result<RuntimeOverview, InstallerError> {
    let service = Arc::clone(&state.service);
    tauri::async_runtime::spawn_blocking(move || service.runtime_overview())
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "The runtime check stopped unexpectedly."))?
}

#[tauri::command]
pub async fn control_runtime(
    state: State<'_, AppState>,
    action: RuntimeAction,
) -> Result<RuntimeOverview, InstallerError> {
    let service = Arc::clone(&state.service);
    tauri::async_runtime::spawn_blocking(move || service.control_runtime(action))
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "The Docker operation stopped unexpectedly."))?
}

#[tauri::command]
pub async fn read_logs(
    state: State<'_, AppState>,
    service: LogService,
) -> Result<RuntimeLogs, InstallerError> {
    let installer = Arc::clone(&state.service);
    tauri::async_runtime::spawn_blocking(move || installer.logs(service))
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "The log request stopped unexpectedly."))?
}

#[tauri::command]
pub fn toggle_fullscreen(window: Window) -> Result<bool, InstallerError> {
    let fullscreen = window
        .is_fullscreen()
        .map_err(|_| InstallerError::retryable("window_state_failed", "Could not read the window state."))?;
    let next = !fullscreen;
    window
        .set_fullscreen(next)
        .map_err(|_| InstallerError::retryable("fullscreen_failed", "Could not change fullscreen mode."))?;
    Ok(next)
}

#[tauri::command]
pub async fn reset_installation(state: State<'_, AppState>) -> Result<(), InstallerError> {
    let service = Arc::clone(&state.service);
    tauri::async_runtime::spawn_blocking(move || service.reset_preserving_data())
        .await
        .map_err(|_| InstallerError::retryable("worker_failed", "The reset operation stopped unexpectedly."))?
}
