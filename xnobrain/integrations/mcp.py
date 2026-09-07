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
            "servers": self._safe_mcp_servers(servers) if isinstance(servers, Mapping) else {},
        }

    def update_mcp(self, raw_name: Any, servers: Mapping[str, Any]) -> dict[str, Any]:
        from urllib.parse import urlparse

        def validate_string_map(path: str, value: Any) -> list[str]:
            if not isinstance(value, Mapping):
                return [f"{path} must be an object of string values"]
            return [
                f"{path}.{key} must be a string"
                for key, item in value.items()
                if not isinstance(key, str) or not key.strip() or not isinstance(item, str)
            ]

        def validate_string_list(path: str, value: Any) -> list[str]:
            if not isinstance(value, list):
                return [f"{path} must be an array of non-empty strings"]
            if any(not isinstance(item, str) or not item.strip() for item in value):
                return [f"{path} must contain only non-empty strings"]
            return []

        def validate_mcp_server_entry(name: str, entry: Mapping[str, Any]) -> list[str]:
            issues: list[str] = []
            allowed = {"command", "args", "env", "url", "headers", "tools"}
            for key in entry:
                if key not in allowed:
                    issues.append(f"{name}.{key} is not supported")

            command = entry.get("command")
            url = entry.get("url")
            has_command = isinstance(command, str) and bool(command.strip())
            has_url = isinstance(url, str) and bool(url.strip())
            if has_command == has_url:
                issues.append(f"{name} must define exactly one non-empty command or url")
            elif "command" in entry and not has_command:
                issues.append(f"{name}.command must be a non-empty string")
            elif has_url:
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    issues.append(f"{name}.url must be an absolute HTTP(S) URL")
            if "url" in entry and not isinstance(url, str):
                issues.append(f"{name}.url must be a non-empty string")
            if "args" in entry:
                issues.extend(validate_string_list(f"{name}.args", entry["args"]))
            for field in ("env", "headers"):
                if field in entry:
                    issues.extend(validate_string_map(f"{name}.{field}", entry[field]))
            if "tools" in entry:
                tools = entry["tools"]
                if not isinstance(tools, Mapping):
                    issues.append(f"{name}.tools must be an object")
                else:
                    for key in tools:
                        if key not in {"include", "exclude"}:
                            issues.append(f"{name}.tools.{key} is not supported")
                    for key in ("include", "exclude"):
                        if key in tools:
                            issues.extend(validate_string_list(f"{name}.tools.{key}", tools[key]))
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
