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
        visible = (
            {
                name: server
                for name, server in servers.items()
                if not (isinstance(server, dict) and server.get("xnobrain_plugin_shadow"))
            }
            if isinstance(servers, Mapping)
            else {}
        )
        return {
            "servers": self._safe_mcp_servers(visible),
            "chat_status": self._native_mcp_status(profile_dir, config),
        }

    @staticmethod
    def _native_mcp_status(profile_dir: Any, config: Mapping[str, Any]) -> dict[str, str]:
        """Report how each server behaves in native chat turns, never failing reads."""
        try:
            from .extension_engine import _custom_mcp_servers
            from .extension_policy import load_state
            from .managed_mcp import native_mcp_status

            custom = _custom_mcp_servers(dict(config), load_state(profile_dir))
            return native_mcp_status(profile_dir, custom)
        except Exception:
            return {}

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
            allowed = {
                "command",
                "args",
                "env",
                "url",
                "headers",
                "tools",
                "xnobrain_connection_id",
            }
            for key in entry:
                if key not in allowed:
                    issues.append(f"{name}.{key} is not supported")
            if "xnobrain_connection_id" in entry:
                # Control holds the headers; the entry only names the connection.
                if not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    str(entry["xnobrain_connection_id"]),
                ):
                    issues.append(f"{name}.xnobrain_connection_id must be a UUID")
                if "headers" in entry or "command" in entry:
                    issues.append(f"{name}.xnobrain_connection_id requires a url without headers")

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
        existing = config.get("mcp_servers") or {}
        # Plugin shadows are hidden from get_mcp, so keep them and only refuse overwrites.
        shadows = (
            {
                name: server
                for name, server in existing.items()
                if isinstance(server, dict) and server.get("xnobrain_plugin_shadow")
            }
            if isinstance(existing, Mapping)
            else {}
        )
        if any(name in shadows for name in cleaned):
            raise AgentAPIError(
                "Plugin managed MCP entries cannot be edited",
                status=422,
                code="mcp_server_managed",
            )
        from .extension_policy import load_state

        managed_names = {
            record.get("server_name")
            for record in load_state(profile_dir).get("mcp", {}).values()
            if isinstance(record, dict)
        }
        if any(name in managed_names for name in cleaned):
            raise AgentAPIError(
                "Managed MCP entries cannot be edited",
                status=422,
                code="mcp_server_managed",
            )
        cleaned.update(shadows)
        if cleaned:
            config["mcp_servers"] = cleaned
        else:
            config.pop("mcp_servers", None)
        self._write_yaml_atomic(profile_dir / "config.yaml", config)
        return self.get_mcp(raw_name)
