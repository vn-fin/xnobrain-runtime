#[cfg(feature = "desktop")]
mod commands;
mod domain;
mod drivers;
mod persistence;
mod services;

use std::sync::Arc;

use services::InstallerService;
#[cfg(feature = "desktop")]
use tauri::Manager;

pub struct AppState {
    service: Arc<InstallerService>,
}

#[cfg(feature = "desktop")]
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let data_root = app.path().app_data_dir().map_err(|error| error.to_string())?;
            let service = InstallerService::new(data_root).map_err(|error| error.to_string())?;
            app.manage(AppState {
                service: Arc::new(service),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::inspect_system,
            commands::inspect_port,
            commands::install_web,
            commands::open_web,
            commands::open_docker_help,
            commands::inspect_runtime,
            commands::control_runtime,
            commands::read_logs,
            commands::reset_installation,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Brain4All installer");
}
