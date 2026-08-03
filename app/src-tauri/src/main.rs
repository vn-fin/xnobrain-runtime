#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(target_os = "linux")]
fn disable_ibus_for_xnobrain() {
    // WebKitGTK can lose all key input when its IBus context disconnects or
    // conflicts with the compositor. Select GTK's built-in simple context for
    // this process only; the user's desktop-wide IBus configuration is left
    // unchanged. This runs before Tauri/GTK creates any threads.
    //
    // SAFETY: mutating the process environment is safe here because `main`
    // has not initialized Tauri, GTK, WebKit, or any worker thread yet.
    unsafe {
        std::env::set_var("GTK_IM_MODULE", "gtk-im-context-simple");
        std::env::remove_var("QT_IM_MODULE");
        std::env::remove_var("XMODIFIERS");
    }
}

fn main() {
    #[cfg(target_os = "linux")]
    disable_ibus_for_xnobrain();

    xnobrain_app_lib::run()
}
