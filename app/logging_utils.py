from __future__ import annotations

from datetime import datetime, timezone

import discord


def now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def base_embed(title: str, description: str, color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=now_label())
    return embed


def action_embed(*, title: str, fields: list[tuple[str, str, bool]], color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    for name, value, inline in fields:
        embed.add_field(name=name, value=value or "—", inline=inline)
    embed.set_footer(text=now_label())
    return embed


async def send_log(channel: discord.abc.Messageable | None, *, embed: discord.Embed | None = None, title: str | None = None, description: str | None = None, color: int = 0x5865F2) -> None:
    if channel is None:
        return
    try:
        await channel.send(embed=embed or base_embed(title or "Лог", description or "", color))
    except Exception:
        pass
