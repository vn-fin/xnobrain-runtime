"""HermesCommands methods for the Hermes runtime adapter."""

import subprocess

from .hermes_support import (
    AGENT_CREDENTIAL_ENV_KEYS,
    LLM_ROUTER_KEY_ENV,
    LLM_ROUTER_PROVIDER,
    AgentAPIError,
    Any,
    Mapping,
    Path,
    asyncio,
    os,
)


class HermesCommandsMixin:
    async def _run_profile_command(
        self,
        name: str,
        command: list[str],
        *,
        engine: str = "xnobrain",
        timeout_seconds: int,
        input_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        return await self._run_hermes_command(
            self._profile_dir(name),
            self._workspace_dir(name),
            command,
            engine=engine,
            timeout_seconds=timeout_seconds,
            input_bytes=input_bytes,
        )

    async def _run_hermes_command(
        self,
        hermes_home: Path,
        cwd: Path,
        command: list[str],
        *,
        engine: str = "xnobrain",
        timeout_seconds: int,
        input_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        env = self._command_env(hermes_home, engine)
        # Standard Popen avoids uvloop's fork/exec crash in a multithreaded
        # Runtime hosting gRPC and native dispatchers. Only waiting uses a thread.
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        communication = asyncio.create_task(asyncio.to_thread(proc.communicate, input_bytes))
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout_seconds)
        except (asyncio.CancelledError, asyncio.TimeoutError) as error:
            if proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    await asyncio.to_thread(proc.wait)
            await asyncio.shield(communication)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise AgentAPIError(
                "agent command timed out", code="agent_command_timeout", status=504
            ) from error
        masked_command = list(command)
        if len(masked_command) >= 2 and masked_command[-2] == "-z":
            masked_command[-1] = "<message>"
        return {
            "command": masked_command,
            "exit_code": int(proc.returncode or 0),
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
        }

    def _command_env(self, hermes_home: Path, engine: str) -> dict[str, str]:
        router_api_key = self._ensure_router_api_key()
        env = os.environ.copy()
        env["HERMES_HOME"] = str(hermes_home)
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("HERMES_ACCEPT_HOOKS", "1")
        self._load_agent_credentials(env)
        if router_api_key:
            # Legacy credential files cannot replace the canonical mounted key.
            env[LLM_ROUTER_KEY_ENV] = router_api_key
        from .accounting_context import current_accounting

        accounting = current_accounting()
        if accounting:
            env[LLM_ROUTER_KEY_ENV] = accounting["binding"]["user_key"]
            env["RUNTIME_LLM_API_KEY_FILE"] = ""
            env["RUNTIME_EXECUTION_AGENT_ID"] = accounting["binding"]["agent_id"]
            env["RUNTIME_EXECUTION_RUN_ID"] = accounting["headers"].get("X-GoRouter-Run-Id", "")
            env["RUNTIME_EXECUTION_PARENT_RUN_ID"] = accounting["headers"].get(
                "X-GoRouter-Parent-Run-Id", ""
            )
        # Subprocess cwd is a profile workspace, not the application source root.
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(Path(__file__).resolve().parents[2]), env.get("PYTHONPATH", "")))
        )
        return env

    @staticmethod
    def _ensure_router_api_key() -> str:
        """Map the provisioned API key to Hermes' provider key env."""
        token_file = os.environ.get("RUNTIME_LLM_API_KEY_FILE", "").strip()
        token = os.environ.get("RUNTIME_LLM_API_KEY", "").strip()
        if token_file:
            try:
                token = Path(token_file).read_text(encoding="utf-8").strip() or token
            except OSError:
                pass
        if token:
            # Hermes subprocesses receive a fresh copy above. Keep the long-lived
            # Runtime environment unchanged so another verified source-stack user
            # cannot inherit a previously selected Router credential.
            return token
        return ""

    def _hermes_binary(self) -> str:
        return os.environ.get("HERMES_CLI", "hermes")

    def _agent_config_dir(self) -> Path:
        configured = os.environ.get("AGENT_CONFIG_DIR")
        if configured:
            return Path(configured)
        return Path(os.environ.get("HOME") or str(Path.home())) / ".config" / "sandbox-agent"

    def _agent_env_file(self) -> Path:
        configured = os.environ.get("AGENT_ENV_FILE")
        if configured:
            return Path(configured)
        return self._agent_config_dir() / "credentials.env"

    def _load_agent_credentials(self, env: dict[str, str]) -> None:
        env_file = self._agent_env_file()
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        allowed = set(AGENT_CREDENTIAL_ENV_KEYS)
        for line in lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in allowed and value:
                env[key] = value

    def _conversation_provider(self, profile_dir: Path, body: Mapping[str, Any]) -> str:
        return LLM_ROUTER_PROVIDER
