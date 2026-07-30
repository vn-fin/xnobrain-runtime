window.__BRAIN4ALL_CONFIG__ = {
  edition: "cloud",
  api: {
    remoteBaseUrl: "https://runtime.example.com",
    authBaseUrl: "https://auth.example.com",
    controlBaseUrl: "https://control.example.com"
  },
  auth: {
    mode: "required",
    provider: "xno-firebase",
    firebaseApiKey: "AIzaSyD8AFSR1vg21WOwLNVhczWfWfi3YSmZ9NA",
    tokenPath: "/api/brain-control/v1/auth/token",
    refreshPath: "/api/brain-control/v1/auth/refresh",
    mePath: "/api/brain-control/v1/auth/me"
  },
  features: {
    login: true
  }
};
