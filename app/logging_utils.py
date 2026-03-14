from __future__ import annotations

from datetime import datetime, timezone

import discord


def now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def base_embed(title: str, description: str, color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=now_label())
    return embed


async def send_log(channel: discord.abc.Messageable | None, *, title: str, description: str, color: int = 0x5865F2) -> None:
    if channel is None:
        return
    try:
        await channel.send(embed=base_embed(title, description, color))
    except Exception:
        pass
