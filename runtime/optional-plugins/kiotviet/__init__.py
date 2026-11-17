"""Register KiotViet's managed retail tools and guidance."""

from pathlib import Path


def register(ctx):
    from xnobrain.integrations.managed_providers import register as register_provider

    register_provider(ctx, "kiotviet")
    ctx.register_skill(
        "kiotviet",
        Path(__file__).parent / "skills/kiotviet/SKILL.md",
        description="Read and manage KiotViet products, stock, sales and purchases.",
    )
