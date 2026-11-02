"""Resolve Runtime-owned blends inside native cron execution, without rewriting jobs."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any

from .conversation_credentials import conversation_model_route
from .conversation_runner import ConversationRunnerMixin

_ROUTER: ContextVar[Any] = ContextVar("cron_blend_router", default=None)
_JOB: ContextVar[Any] = ContextVar("cron_blend_job", default=None)
_INSTALL_LOCK = threading.RLock()


def _resolve(awaitable):
    # Native cron runs in a worker. Router clients create request-local sessions,
    # so no main-loop client or lock is carried across threads.
    async def bounded():
        return await asyncio.wait_for(awaitable, timeout=65)

    return asyncio.run(bounded())


class _CronRouting(ConversationRunnerMixin):
    """Reuse chat's per-step Smart Route and native fallback integration."""

    def __init__(self, router):
        self.llm_router = router

    def _resolve_smart_route_from_worker(
        self, loop, loop_thread_id, route_name, message, *, required_context_tokens
    ):
        try:
            return _resolve(
                self.llm_router.resolve_smart_route(
                    route_name, message, required_context_tokens=required_context_tokens
                )
            )
        except Exception:
            # As in chat, keep the last selected model if a later classifier
            # call fails. Initial selection failures still fail the native run.
            return None


def _initialize(original, agent, args, kwargs, router, job):
    name = str(kwargs.get("model") or "").strip()
    explicit_blend = job.get("xnobrain_pinned_connector") == "blend"
    if not name or name == "auto" or "/" in name:
        return original(agent, *args, **kwargs)
    message = str(job.get("prompt") or "")
    decision = _resolve(
        router.resolve_blend_route(
            name, message, required_context_tokens=8192 + max(1, len(message) // 4)
        )
    )
    if decision is None:
        if explicit_blend:
            raise ValueError("cron_model_blend_unavailable")
        return original(agent, *args, **kwargs)

    selected = str(decision["model"])
    kwargs.update(conversation_model_route(selected, router.base_url))
    kwargs["requested_provider"] = kwargs.get("provider")
    routing = _CronRouting(router)
    apply_reasoning = kwargs.get("reasoning_config") is None
    if decision.get("tier") and apply_reasoning:
        from hermes_constants import parse_reasoning_effort

        kwargs["reasoning_config"] = parse_reasoning_effort(decision.get("reasoning"))
    original(agent, *args, **kwargs)
    routing._install_provider_runtime_request_guard(agent)
    routing._install_model_fallbacks(
        agent,
        {"model_fallbacks": [m for m in decision.get("candidates", []) if m != selected]},
    )
    if decision.get("tier"):
        routing._install_smart_route_step_routing(
            agent, None, 0, name, apply_reasoning=apply_reasoning
        )


def install_blend_executor(scheduler=None, agent_class=None):
    """Install once; ContextVars isolate concurrent cron jobs and ordinary chat."""
    if scheduler is None:
        from cron import scheduler
    if agent_class is None:
        from run_agent import AIAgent

        agent_class = AIAgent
    with _INSTALL_LOCK:
        original_run = scheduler.run_job
        if not getattr(original_run, "_xnobrain_cron_blends", False):

            @wraps(original_run)
            def run_job(job, *args, **kwargs):
                scoped_job = dict(job)
                if kwargs.get("extra_prompt"):
                    scoped_job["prompt"] = (
                        str(job.get("prompt") or "") + "\n" + str(kwargs["extra_prompt"])
                    )
                token = _JOB.set(scoped_job)
                try:
                    return original_run(job, *args, **kwargs)
                finally:
                    _JOB.reset(token)

            run_job._xnobrain_cron_blends = True
            scheduler.run_job = run_job

        original_init = agent_class.__init__
        if not getattr(original_init, "_xnobrain_cron_blends", False):

            @wraps(original_init)
            def initialize(agent, *args, **kwargs):
                router, job = _ROUTER.get(), _JOB.get()
                if router is None or job is None or kwargs.get("platform") != "cron":
                    return original_init(agent, *args, **kwargs)
                return _initialize(original_init, agent, args, kwargs, router, job)

            initialize._xnobrain_cron_blends = True
            agent_class.__init__ = initialize


@contextmanager
def blend_execution_scope(router):
    if router is not None:
        install_blend_executor()
    token = _ROUTER.set(router)
    try:
        yield
    finally:
        _ROUTER.reset(token)
