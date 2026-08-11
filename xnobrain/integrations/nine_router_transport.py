"""Grouped NineRouterTransport behavior for 9router."""

from .nine_router_support import (
    Any,
    Mapping,
    NINE_ROUTER_DEFAULT_MODEL,
    NINE_ROUTER_PROVIDER_KEY,
    NineRouterAPIError,
    Path,
    ROUTER_PROVIDER_BY_MODEL_OWNER,
    _SAFE_ID_RE,
    aiohttp,
    hashlib,
    os,
    secrets,
    socket,
)


class NineRouterTransportMixin:
    async def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith(("/api/", "/v1/")):
            raise NineRouterAPIError("invalid 9Router path", status=400)
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                for attempt in range(2):
                    headers = {"Accept": "application/json"}
                    cli_token = self._cli_token()
                    if cli_token:
                        headers["x-9r-cli-token"] = cli_token
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
                        if response.status == 401 and not cli_token and attempt == 0:
                            continue
                        if response.status < 200 or response.status >= 300:
                            message = self._error_message(payload) or f"9Router returned HTTP {response.status}"
                            raise NineRouterAPIError(message, status=response.status)
                        return dict(payload) if isinstance(payload, Mapping) else {"data": payload}
        except NineRouterAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise NineRouterAPIError(
                "9Router is unavailable", code="nine_router_unavailable", status=503
            ) from exc


    def _filtered_connection_response(self, payload: Any) -> dict[str, Any]:
        connection = payload.get("connection", {}) if isinstance(payload, Mapping) else {}
        if not isinstance(connection, Mapping):
            connection = {}
        return {
            "object": "nine_router.provider",
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
        if not model_id or model_id == NINE_ROUTER_DEFAULT_MODEL:
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
        return model_id.split("/", 1)[0] if "/" in model_id else NINE_ROUTER_PROVIDER_KEY


    def _cli_token(self) -> str:
        machine_id = self._read_or_create_secret(
            self.data_dir / "machine-id", self._native_machine_id()
        )
        cli_secret = self._read_or_create_secret(
            self.data_dir / "auth" / "cli-secret", secrets.token_hex(32)
        )
        if not machine_id or not cli_secret:
            return ""
        raw = f"{machine_id}9r-cli-auth{cli_secret}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]


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
        return hashlib.sha256(normalized.encode()).hexdigest()


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
