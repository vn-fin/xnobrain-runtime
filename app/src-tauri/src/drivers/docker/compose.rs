use crate::domain::RuntimeManifest;

pub struct ComposeSpec<'a> {
    pub manifest: &'a RuntimeManifest,
    pub port: u16,
    pub internal_secret: &'a str,
}

impl ComposeSpec<'_> {
    pub fn render(&self) -> String {
        format!(
            r#"name: xnobrain_web

services:
  traefik:
    image: {traefik_image}
    pull_policy: always
    command:
      - --api.dashboard=false
      - --providers.file.filename=/etc/traefik/dynamic.yaml
      - --providers.file.watch=true
      - --entrypoints.web.address=:5152
    ports:
      - "127.0.0.1:{port}:5152"
    volumes:
      - ./traefik-dynamic.yaml:/etc/traefik/dynamic.yaml:ro
    networks:
      - edge
    labels:
      - xnobrain.install-id=xnobrain_web
    restart: unless-stopped

  brain:
    image: {brain_image}
    pull_policy: always
    labels:
      - xnobrain.install-id=xnobrain_web
      - xnobrain.web-build.auth-base-url={auth_base_url}
      - xnobrain.web-build.brain-control-base-url={brain_control_base_url}
      - xnobrain.web-build.auth-mode={auth_mode}
      - xnobrain.web-build.auth-provider={auth_provider}
      - xnobrain.web-build.firebase-api-key-sha256={firebase_api_key_sha256}
    networks:
      - edge
    restart: unless-stopped

  runtime-api:
    image: {runtime_api_image}
    pull_policy: always
    user: "0:0"
    cpus: 4.0
    mem_limit: 4G
    memswap_limit: 4G
    environment:
      HERMES_DESKTOP: "1"
      HOME: /opt/data/home
      XDG_CONFIG_HOME: /opt/data/home/.config
      NINE_ROUTER_INTERNAL_SECRET: "{internal_secret}"
      API_SERVER_HOST: 0.0.0.0
      API_SERVER_PORT: 8642
      DATA_DIR: /opt/data/xnobrain
      DEVELOPMENT_ENVIRONMENT: production
      SERVICE_NAME: xnobrain-runtime-api
      OTEL_ENABLED: "false"
    volumes:
      - xnobrain_data:/opt/data
    extra_hosts:
      - host.docker.internal:host-gateway
    labels:
      - xnobrain.install-id=xnobrain_web
    networks:
      - edge
      - runtime
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8642/api/v1/health',timeout=2)"]
      interval: 15s
      timeout: 3s
      retries: 20
    restart: unless-stopped

networks:
  edge:
    name: xnobrain_web_edge
  runtime:
    name: xnobrain_web_runtime
    internal: true

volumes:
  xnobrain_data:
    name: xnobrain_web_data
"#,
            traefik_image = self.manifest.images.traefik.reference,
            brain_image = self.manifest.images.brain.reference,
            runtime_api_image = self.manifest.images.runtime_api.reference,
            auth_base_url = self.manifest.web_build.auth_base_url,
            brain_control_base_url = self.manifest.web_build.brain_control_base_url,
            auth_mode = self.manifest.web_build.auth_mode,
            auth_provider = self.manifest.web_build.auth_provider,
            firebase_api_key_sha256 = self.manifest.web_build.firebase_api_key_sha256,
            port = self.port,
            internal_secret = self.internal_secret,
        )
    }

    pub fn render_traefik_dynamic(&self) -> String {
        r#"http:
  routers:
    xnobrain-web-app-api:
      rule: PathPrefix(`/api`) || PathPrefix(`/internal`)
      entryPoints: [web]
      priority: 100
      service: xnobrain-runtime-api
    xnobrain-web-app-ui:
      rule: PathPrefix(`/`)
      entryPoints: [web]
      priority: 1
      service: xnobrain-brain
  services:
    xnobrain-runtime-api:
      loadBalancer:
        servers:
          - url: http://runtime-api:8642
    xnobrain-brain:
      loadBalancer:
        servers:
          - url: http://brain:8080
"#
        .to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::ComposeSpec;
    use crate::domain::RuntimeManifest;

    #[test]
    fn only_traefik_publishes_the_selected_loopback_port() {
        let manifest = RuntimeManifest::bundled().expect("manifest");
        let compose = ComposeSpec {
            manifest: &manifest,
            port: 6200,
            internal_secret: "test-secret",
        }
        .render();

        assert_eq!(compose.matches("    ports:\n").count(), 1);
        assert!(compose.contains("127.0.0.1:6200:5152"));
        assert!(!compose.contains("8642:8642"));
        assert!(compose.contains("--api.dashboard=false"));
        assert!(compose.contains("--providers.file.filename=/etc/traefik/dynamic.yaml"));
        assert!(!compose.contains("/var/run/docker.sock"));
        assert!(compose.contains("xnobrain_web_data"));
        assert!(compose.contains("xnobrain.web-build.auth-base-url=https://api.dev.xnoquant.io"));
        assert!(
            compose
                .contains("xnobrain.web-build.brain-control-base-url=https://api.dev.xnoquant.io")
        );
        assert!(compose.contains("xnobrain.web-build.auth-mode=required"));
        assert!(compose.contains("xnobrain.web-build.auth-provider=xno-firebase"));
        assert!(compose.contains(
            "xnobrain.web-build.firebase-api-key-sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        ));
        assert!(!compose.contains("  control:"));
        assert_eq!(compose.matches("pull_policy: always").count(), 3);
        assert!(!compose.contains("build:"));

        let dynamic = ComposeSpec {
            manifest: &manifest,
            port: 6200,
            internal_secret: "test-secret",
        }
        .render_traefik_dynamic();
        assert!(dynamic.contains("xnobrain-web-app-ui"));
        assert!(dynamic.contains("xnobrain-web-app-api"));
        assert!(dynamic.contains("http://runtime-api:8642"));
        assert!(dynamic.contains("http://brain:8080"));
    }
}
