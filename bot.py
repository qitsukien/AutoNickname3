from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from app.config import TOKEN, ensure_directories, load_config
from app.db import init_db, get_user
from app.services import restore_member, send_welcome
from app.views import AdminPanelView, AdminSettingsView, AdminUserToolsView, RegistrationView, ensure_registration_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)


class AutoNicknameBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        ensure_directories()
        init_db()
        self.add_view(RegistrationView())
        self.add_view(AdminPanelView())
        self.add_view(AdminUserToolsView())
        self.add_view(AdminSettingsView())
        await self.load_extension("app.commands")

    async def on_ready(self) -> None:
        cfg = load_config()
        guild_id = int(cfg.get("guild_id", 0) or 0)
        if guild_id:
            guild_obj = discord.Object(id=guild_id)
            try:
                self.tree.copy_global_to(guild=guild_obj)
                synced = await self.tree.sync(guild=guild_obj)
                log.info("Synced %s commands", len(synced))
            except Exception:
                log.exception("Command sync failed")
        try:
            await ensure_registration_message(self)
        except Exception:
            log.exception("Failed to ensure registration panel")
        log.info("Logged in as %s (%s)", self.user, getattr(self.user, "id", "unknown"))

    async def on_member_join(self, member: discord.Member) -> None:
        cfg = load_config()
        if not bool(cfg.get("auto_restore_on_join", True)):
            return
        if get_user(member.id):
            result = await restore_member(member, actor_id=None, source="auto_restore")
            if result.ok:
                await send_welcome(member)


async def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN не найден в .env")
    bot = AutoNicknameBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
