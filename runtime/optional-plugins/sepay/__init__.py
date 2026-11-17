"""Register SePay's managed read tool and guidance."""

from pathlib import Path


def register(ctx):
    from xnobrain.integrations.managed_providers import register as register_provider

    register_provider(ctx, "sepay")
    ctx.register_skill(
        "sepay",
        Path(__file__).parent / "skills/sepay/SKILL.md",
        description="Read connected SePay records with managed credentials.",
    )
