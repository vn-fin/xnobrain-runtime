"""Transport helpers for the centralized managed LLM gateway."""

from .nine_router_support import (
    Any,
    Mapping,
    OMNIROUTE_DEFAULT_MODEL,
    OMNIROUTE_PROVIDER_KEY,
    NineRouterAPIError,
    Path,
    ROUTER_PROVIDER_BY_MODEL_OWNER,
    _SAFE_ID_RE,
    aiohttp,
    hashlib,
    hmac,
    os,
    secrets,
    socket,
)


class NineRouterTransportMixin:
    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
        }
        workload_token = os.environ.get("RUNTIME_LLM_WORKLOAD_TOKEN", "").strip()
        if workload_token:
            headers["Authorization"] = f"Bearer {workload_token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith(("/api/", "/v1/")):
            raise NineRouterAPIError("invalid provider runtime path", status=400)
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                for attempt in range(2):
                    headers = self._request_headers()
                    async with session.request(
                        method,
                        self.base_url + path,
                        json=dict(body) if body is not None else None,
                        headers=headers,
                    ) as response:
                        try:
                            payload = await response.json(content_type=None)
                        except Exception:
                            payload = {"error": (await response.text()).strip()}
                        if response.status < 200 or response.status >= 300:
                            message = self._error_message(payload) or f"Provider runtime returned HTTP {response.status}"
                            raise NineRouterAPIError(message, status=response.status)
                        return dict(payload) if isinstance(payload, Mapping) else {"data": payload}
        except NineRouterAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise NineRouterAPIError(
                "Provider runtime is unavailable",
                code="provider_runtime_unavailable",
                status=503,
            ) from exc


    def _filtered_connection_response(self, payload: Any) -> dict[str, Any]:
        connection = payload.get("connection", {}) if isinstance(payload, Mapping) else {}
        if not isinstance(connection, Mapping):
            connection = {}
        return {
            "object": "xnobrain.provider_runtime.provider",
            "connection": {
                "id": str(connection.get("id") or ""),
                "provider": str(connection.get("provider") or ""),
                "auth_type": str(connection.get("authType") or ""),
                "name": str(connection.get("name") or connection.get("displayName") or ""),
                "active": connection.get("isActive") is not False,
                "default_model": str(connection.get("defaultModel") or ""),
                "email": str(connection.get("email") or ""),
                "priority": self._number(connection.get("priority")),
            },
        }


    def _provider(self, value: Any, allowed: frozenset[str]) -> str:
        provider = str(value or "").strip().lower()
        if provider not in allowed:
            raise NineRouterAPIError(
                "unsupported provider", code="unsupported_provider", status=400
            )
        return provider


    def _provider_for_model(self, model_id: str) -> str:
        if not model_id or model_id == OMNIROUTE_DEFAULT_MODEL:
            return ""
        owner = model_id.split("/", 1)[0]
        return ROUTER_PROVIDER_BY_MODEL_OWNER.get(owner, "")


    def _empty_usage(
        self,
        model_id: str,
        message: str,
        *,
        provider: str = "",
    ) -> dict[str, Any]:
        return {
            "object": "router.usage",
            "available": False,
            "provider": provider,
            "model": model_id,
            "plan": "",
            "message": message,
            "quotas": [],
        }


    def _quota_matches_model(self, provider: str, model_id: str, name: str) -> bool:
        model_name = model_id.split("/", 1)[-1].lower()
        quota_name = name.lower()
        if provider == "antigravity":
            return quota_name == model_name
        if provider == "codex":
            is_review_model = model_name.endswith("-review")
            is_review_quota = quota_name.startswith("review_")
            return is_review_model == is_review_quota
        if provider == "claude" and quota_name.startswith("weekly "):
            families = ("opus", "sonnet", "haiku")
            quota_family = next((item for item in families if item in quota_name), "")
            return not quota_family or quota_family in model_name
        return True


    def _normalize_quota(
        self,
        name: str,
        quota: Mapping[str, Any],
    ) -> dict[str, Any]:
        used = self._number(quota.get("used"))
        total = self._number(quota.get("total"))
        remaining = self._number(quota.get("remaining"))
        remaining_percent = self._number(quota.get("remainingPercentage"))
        if remaining_percent == 0 and quota.get("remainingPercentage") is None:
            if total > 0:
                remaining_percent = round(max(0, total - used) / total * 100)
            elif quota.get("remaining") is not None and 0 <= remaining <= 100:
                remaining_percent = remaining
        remaining_percent = max(0, min(100, remaining_percent))
        return {
            "name": name,
            "used": used,
            "total": total,
            "remaining_percent": remaining_percent,
            "reset_at": str(quota.get("resetAt") or ""),
            "unlimited": bool(quota.get("unlimited")),
        }


    def _number(self, value: Any) -> int:
        try:
            return round(float(value or 0))
        except (TypeError, ValueError):
            return 0


    def _safe_id(self, value: Any, field: str) -> str:
        normalized = str(value or "").strip()
        if not _SAFE_ID_RE.fullmatch(normalized):
            raise NineRouterAPIError(f"{field} is invalid", status=400)
        return normalized


    def _model_owner(self, model_id: str) -> str:
        return model_id.split("/", 1)[0] if "/" in model_id else OMNIROUTE_PROVIDER_KEY


    def _cli_token(self) -> str:
        return os.environ.get("RUNTIME_LLM_WORKLOAD_TOKEN", "").strip()


    def _native_machine_id(self) -> str:
        value = ""
        for source in (Path("/var/lib/dbus/machine-id"), Path("/etc/machine-id")):
            try:
                value = source.read_text(encoding="utf-8")
            except OSError:
                continue
            if value.strip():
                break
        normalized = "".join(value.split()).lower() or socket.gethostname().lower()
        return normalized


    def _read_or_create_secret(self, path: Path, generated: str) -> str:
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(generated + "\n")
            return generated
        except FileExistsError:
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        except OSError:
            return ""


    def _error_message(self, payload: Any) -> str:
        if not isinstance(payload, Mapping):
            return ""
        error = payload.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error.get("detail") or "").strip()
        return str(error or payload.get("message") or "").strip()
