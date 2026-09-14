"""Private CLI bootstrap for accounted one-shot and native Kanban workers.

Runs the pinned engine unchanged. Credentials are supplied only in this child's
process and scoped to its configured Router, never in argv or a profile file.
"""

import os
import sys

from .accounting_context import accounting_binding, accounting_enabled, inference_accounting
from .conversation_runner import ConversationRunnerMixin


def install_worker_agent(binding, run_id, parent_run_id=""):
    """Attach correlation to every native agent, including auxiliary clients."""
    from run_agent import AIAgent

    outcome = {"completed": False}
    original_init = AIAgent.__init__
    original_run = AIAgent.run_conversation

    def initialize(agent, *args, **kwargs):
        # This entrypoint is private and only executes against the managed URL.
        kwargs.update(
            api_key=binding["user_key"],
            base_url=os.environ["RUNTIME_LLM_ROUTER_URL"],
            provider="custom:xnobrain",
        )
        original_init(agent, *args, **kwargs)
        conversation = str(agent.session_id or run_id)
        headers = {
            "X-GoRouter-Agent-Id": binding["agent_id"],
            "X-GoRouter-Conversation-Id": conversation,
            "X-GoRouter-Run-Id": run_id,
        }
        if parent_run_id:
            headers["X-GoRouter-Parent-Run-Id"] = parent_run_id
        for client in (getattr(agent, "client", None), getattr(agent, "async_client", None)):
            if client is not None and hasattr(client, "_custom_headers"):
                client._custom_headers = {**(client._custom_headers or {}), **headers}
        ConversationRunnerMixin._install_provider_runtime_request_guard(agent)
        build = agent._build_api_kwargs

        def attributed_request(messages):
            with inference_accounting(binding, conversation, run_id, parent_run_id):
                return build(messages)

        agent._build_api_kwargs = attributed_request

    def run(agent, *args, **kwargs):
        with inference_accounting(binding, str(agent.session_id or run_id), run_id, parent_run_id):
            result = original_run(agent, *args, **kwargs)
        if result.get("failed") or result.get("interrupted"):
            raise RuntimeError("worker inference did not complete")
        outcome["completed"] = True
        return result

    AIAgent.__init__ = initialize
    AIAgent.run_conversation = run
    return outcome


def main():
    if "--xnobrain-input-stdin" in sys.argv:
        sys.argv.remove("--xnobrain-input-stdin")
        # Keep large Teams prompts out of OS argv (E2BIG) and process listings.
        message = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(message) > 1024 * 1024 or not message:
            raise ValueError("invalid worker input")
        sys.argv.extend(["-z", message.decode("utf-8")])

    from hermes_cli.main import main as native_main

    outcome = None
    if accounting_enabled():
        binding = accounting_binding(os.environ["RUNTIME_EXECUTION_AGENT_ID"])
        # The native CLI validates credentials before constructing AIAgent.
        os.environ["RUNTIME_LLM_API_KEY"] = binding["user_key"]
        outcome = install_worker_agent(
            binding,
            os.environ["RUNTIME_EXECUTION_RUN_ID"],
            os.environ.get("RUNTIME_EXECUTION_PARENT_RUN_ID", ""),
        )
    try:
        native_main()
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    if outcome is not None and not outcome["completed"]:
        raise RuntimeError("worker exited without completing inference")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Native/provider exceptions may contain request data. Do not echo them.
        print("Accounted worker execution failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
