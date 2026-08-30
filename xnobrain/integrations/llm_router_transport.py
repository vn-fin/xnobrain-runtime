"""Transport helpers for the centralized OpenAI-compatible LLM router client."""

from .llm_router_support import (
    Any,
    Mapping,
    LLM_ROUTER_PROVIDER_KEY,
    LLMRouterAPIError,
    _SAFE_ID_RE,
    aiohttp,
    os,
    Path,
)


class LLMRouterTransportMixin:
    @staticmethod
    def _workload_token() -> str:
        token_file = os.environ.get("RUNTIME_LLM_API_KEY_FILE", "").strip()
        if token_file:
            try:
                value = Path(token_file).read_text(encoding="utf-8").strip()
                if value:
                    os.environ["RUNTIME_LLM_API_KEY"] = value
                    return value
            except OSError:
                pass
        return os.environ.get("RUNTIME_LLM_API_KEY", "").strip()

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
        }
        workload_token = self._workload_token()
        if workload_token:
            headers["Authorization"] = f"Bearer {workload_token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith(("/models", "/chat/completions")):
            raise LLMRouterAPIError("invalid provider runtime path", status=400)
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
                            raise LLMRouterAPIError(message, status=response.status)
                        return dict(payload) if isinstance(payload, Mapping) else {"data": payload}
        except LLMRouterAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise LLMRouterAPIError(
                "Provider runtime is unavailable",
                code="provider_runtime_unavailable",
                status=503,
            ) from exc

    def _safe_id(self, value: Any, field: str) -> str:
        normalized = str(value or "").strip()
        if not _SAFE_ID_RE.fullmatch(normalized):
            raise LLMRouterAPIError(f"{field} is invalid", status=400)
        return normalized


    def _model_owner(self, model_id: str) -> str:
        return model_id.split("/", 1)[0] if "/" in model_id else LLM_ROUTER_PROVIDER_KEY


    def _error_message(self, payload: Any) -> str:
        if not isinstance(payload, Mapping):
            return ""
        error = payload.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error.get("detail") or "").strip()
        return str(error or payload.get("message") or "").strip()
