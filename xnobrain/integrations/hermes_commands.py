"""HermesCommands methods for the Hermes runtime adapter."""

from .hermes_support import (
    AGENT_CREDENTIAL_ENV_KEYS,
    AgentAPIError,
    Any,
    Mapping,
    LLM_ROUTER_KEY_ENV,
    LLM_ROUTER_PROVIDER,
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
    ) -> dict[str, Any]:
        return await self._run_hermes_command(
            self._profile_dir(name),
            self._workspace_dir(name),
            command,
            engine=engine,
            timeout_seconds=timeout_seconds,
        )


    async def _run_hermes_command(
        self,
        hermes_home: Path,
        cwd: Path,
        command: list[str],
        *,
        engine: str = "xnobrain",
        timeout_seconds: int,
    ) -> dict[str, Any]:
        env = self._command_env(hermes_home, engine)
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            raise
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise AgentAPIError(
                "agent command timed out",
                code="agent_command_timeout",
                status=504,
            )
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
        self._ensure_router_api_key()
        env = os.environ.copy()
        env["HERMES_HOME"] = str(hermes_home)
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("HERMES_ACCEPT_HOOKS", "1")
        self._load_agent_credentials(env)
        return env


    @staticmethod
    def _ensure_router_api_key() -> None:
        """Map the provisioned workload token to Hermes' provider key env."""
        token = os.environ.get("RUNTIME_LLM_WORKLOAD_TOKEN", "").strip()
        if token:
            os.environ[LLM_ROUTER_KEY_ENV] = token


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
