use std::{env, fs, path::PathBuf};

fn main() {
    println!("cargo:rerun-if-env-changed=XNOBRAIN_RUNTIME_MANIFEST");
    println!("cargo:rerun-if-changed=../config/runtime-manifest.test.json");
    let source = env::var_os("XNOBRAIN_RUNTIME_MANIFEST")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("../config/runtime-manifest.test.json"));
    let destination = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR is set"))
        .join("runtime-manifest.json");
    fs::copy(&source, &destination).unwrap_or_else(|error| {
        panic!(
            "failed to copy runtime manifest {}: {error}",
            source.display()
        )
    });
    tauri_build::build()
}
