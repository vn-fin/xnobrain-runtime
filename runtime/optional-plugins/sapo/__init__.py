"""Register Sapo's managed read tool and guidance."""

from pathlib import Path


def register(ctx):
    from xnobrain.integrations.managed_providers import register as register_provider

    register_provider(ctx, "sapo")
    ctx.register_skill(
        "sapo",
        Path(__file__).parent / "skills/sapo/SKILL.md",
        description="Read connected Sapo records with managed credentials.",
    )
