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
#[cfg(feature = "desktop")]
use tauri::webview::{NewWindowResponse, WebviewWindowBuilder};

pub struct AppState {
    service: Arc<InstallerService>,
}

#[cfg(feature = "desktop")]
fn popup_url_is_safe(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "http" | "https") || url.as_str() == "about:blank"
}

#[cfg(feature = "desktop")]
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let data_root = app
                .path()
                .app_data_dir()
                .map_err(|error| error.to_string())?;
            let resource_root = app
                .path()
                .resource_dir()
                .map_err(|error| error.to_string())?;
            let service = InstallerService::new(data_root, resource_root)
                .map_err(|error| error.to_string())?;
            app.manage(AppState {
                service: Arc::new(service),
            });

            // The configured window is created here so every desktop webview
            // gets an explicit window.open policy. Provider authentication
            // first opens about:blank synchronously and navigates it after the
            // runtime returns the OAuth URL, so it must remain a related
            // webview instead of being detached into the system browser.
            let window_config = app
                .config()
                .app
                .windows
                .first()
                .cloned()
                .ok_or_else(|| "missing installer window configuration".to_string())?;
            WebviewWindowBuilder::from_config(app.handle(), &window_config)?
                .on_new_window(|url, _features| {
                    if popup_url_is_safe(&url) {
                        NewWindowResponse::Allow
                    } else {
                        NewWindowResponse::Deny
                    }
                })
                .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::inspect_system,
            commands::inspect_port,
            commands::install_web,
            commands::install_docker,
            commands::open_web,
            commands::open_docker_help,
            commands::inspect_runtime,
            commands::manage_runtime,
            commands::read_logs,
            commands::toggle_fullscreen,
            commands::reset_installation,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run XNOBrain app");
}

#[cfg(all(test, feature = "desktop"))]
mod tests {
    use super::popup_url_is_safe;

    #[test]
    fn popup_policy_allows_web_auth_and_rejects_privileged_schemes() {
        for allowed in [
            "about:blank",
            "https://auth.openai.com/oauth/authorize",
            "http://127.0.0.1:1455/auth/callback",
        ] {
            assert!(popup_url_is_safe(&allowed.parse().unwrap()), "{allowed}");
        }

        for rejected in [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,unsafe",
        ] {
            assert!(!popup_url_is_safe(&rejected.parse().unwrap()), "{rejected}");
        }
    }
}
