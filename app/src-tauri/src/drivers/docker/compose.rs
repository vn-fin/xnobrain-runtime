use crate::domain::RuntimeManifest;

pub struct ComposeSpec<'a> {
    pub manifest: &'a RuntimeManifest,
    pub port: u16,
    pub internal_secret: &'a str,
}

impl ComposeSpec<'_> {
    pub fn render(&self) -> String {
        let pull_policy = if self.manifest.development {
            "never"
        } else {
            "always"
        };
        format!(
            r#"name: brain4all_web

services:
  traefik:
    image: {traefik_image}
    pull_policy: {pull_policy}
    command:
      - --api.dashboard=false
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --providers.docker.network=brain4all_web_edge
      - --entrypoints.web.address=:5152
    ports:
      - "127.0.0.1:{port}:5152"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - edge
    labels:
      - brain4all.install-id=brain4all_web
    restart: unless-stopped

  frontend:
    image: {frontend_image}
    pull_policy: {pull_policy}
    labels:
      - traefik.enable=true
      - traefik.http.routers.brain4all-web-app-ui.rule=PathPrefix(`/`)
      - traefik.http.routers.brain4all-web-app-ui.entrypoints=web
      - traefik.http.routers.brain4all-web-app-ui.priority=1
      - traefik.http.services.brain4all-web-app-ui.loadbalancer.server.port=8080
      - brain4all.install-id=brain4all_web
      - brain4all.web-auth-base-url={auth_base_url}
      - brain4all.web-auth-mode={auth_mode}
      - brain4all.web-auth-provider={auth_provider}
    networks:
      - edge
    restart: unless-stopped

  runtime:
    image: {runtime_image}
    pull_policy: {pull_policy}
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
      DATA_DIR: /opt/data/brain4all
      DEVELOPMENT_ENVIRONMENT: production
      SERVICE_NAME: runtime
      OTEL_ENABLED: "false"
    volumes:
      - brain4all_data:/opt/data
    extra_hosts:
      - host.docker.internal:host-gateway
    labels:
      - traefik.enable=true
      - traefik.http.routers.brain4all-web-app-api.rule=PathPrefix(`/api`) || PathPrefix(`/internal`)
      - traefik.http.routers.brain4all-web-app-api.entrypoints=web
      - traefik.http.routers.brain4all-web-app-api.priority=100
      - traefik.http.services.brain4all-web-app-api.loadbalancer.server.port=8642
      - brain4all.install-id=brain4all_web
    networks:
      - edge
      - control
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8642/api/v1/health',timeout=2)"]
      interval: 15s
      timeout: 3s
      retries: 20
    restart: unless-stopped

networks:
  edge:
    name: brain4all_web_edge
  control:
    name: brain4all_web_control
    internal: true

volumes:
  brain4all_data:
    name: brain4all_web_data
"#,
            traefik_image = self.manifest.images.traefik.reference,
            frontend_image = self.manifest.images.frontend.reference,
            runtime_image = self.manifest.images.runtime.reference,
            auth_base_url = self.manifest.web_auth.base_url,
            auth_mode = self.manifest.web_auth.mode,
            auth_provider = self.manifest.web_auth.provider,
            pull_policy = pull_policy,
            port = self.port,
            internal_secret = self.internal_secret,
        )
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
        assert!(!compose.contains("20128:20128"));
        assert!(compose.contains("--api.dashboard=false"));
        assert!(compose.contains("--providers.docker.exposedbydefault=false"));
        assert!(compose.contains("brain4all_web_data"));
        assert!(compose.contains("brain4all-web-app-ui"));
        assert!(compose.contains("brain4all-web-app-api"));
        assert!(compose.contains("brain4all.web-auth-base-url=https://api.dev.xnoquant.io"));
        assert!(compose.contains("brain4all.web-auth-mode=required"));
        assert!(compose.contains("brain4all.web-auth-provider=xno-firebase"));
        assert!(!compose.contains("routers.brain4all-ui"));
        assert!(!compose.contains("routers.brain4all-api"));
    }
}
