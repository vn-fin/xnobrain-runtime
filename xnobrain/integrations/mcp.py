"""MCP integration methods for the Hermes runtime adapter."""

from .hermes_support import (
    AgentAPIError,
    Any,
    Mapping,
    copy,
    re,
)


class MCPIntegrationMixin:
    @staticmethod
    def _safe_mcp_servers(servers: Mapping[str, Any]) -> dict[str, Any]:
        safe = copy.deepcopy(dict(servers))
        for server in safe.values():
            if not isinstance(server, dict):
                continue
            for field in ("env", "headers"):
                values = server.get(field)
                if not isinstance(values, dict):
                    continue
                server[field] = {
                    str(key): (
                        str(value)
                        if re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", str(value))
                        else "***"
                    )
                    for key, value in values.items()
                }
        return safe


    def get_mcp(self, raw_name: Any) -> dict[str, Any]:
        profile_dir = self._require_profile(self._agent_name(raw_name))
        config = self._read_config(profile_dir)
        servers = config.get("mcp_servers")
        return {
            "servers": self._safe_mcp_servers(servers)
            if isinstance(servers, Mapping)
            else {},
        }


    def update_mcp(self, raw_name: Any, servers: Mapping[str, Any]) -> dict[str, Any]:
        try:
            from hermes_cli.mcp_security import validate_mcp_server_entry
        except ImportError:  # Source-only tests may not have Hermes on sys.path.
            def validate_mcp_server_entry(name: str, entry: Mapping[str, Any]) -> list[str]:
                issues: list[str] = []
                has_command = bool(str(entry.get("command") or "").strip())
                has_url = bool(str(entry.get("url") or "").strip())
                if has_command == has_url:
                    issues.append(
                        f"Server {name!r} must define exactly one of command or url"
                    )
                if "args" in entry and not isinstance(entry["args"], list):
                    issues.append(f"Server {name!r} args must be a list")
                for field in ("env", "headers", "tools"):
                    if field in entry and not isinstance(entry[field], dict):
                        issues.append(f"Server {name!r} {field} must be an object")
                return issues

        profile_dir = self._require_profile(self._agent_name(raw_name))
        cleaned = copy.deepcopy(dict(servers))
        issues: list[str] = []
        for server_name, server in cleaned.items():
            if not isinstance(server_name, str) or not server_name.strip():
                issues.append("MCP server names must be non-empty strings")
                continue
            if not isinstance(server, dict):
                issues.append(f"Server {server_name!r} must be an object")
                continue
            issues.extend(validate_mcp_server_entry(server_name, server))
        if issues:
            raise AgentAPIError(
                "; ".join(issues),
                code="invalid_mcp_config",
            )

        config = self._read_config(profile_dir)
        if cleaned:
            config["mcp_servers"] = cleaned
        else:
            config.pop("mcp_servers", None)
        self._write_yaml_atomic(profile_dir / "config.yaml", config)
        return self.get_mcp(raw_name)
