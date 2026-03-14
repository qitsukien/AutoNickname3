from __future__ import annotations

import csv
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from . import db
from .backup import BackupManager
from .config import DB_PATH, EXPORTS_DIR, load_config
from .logging_utils import base_embed
from .services import restore_member
from .utils import UserFacingError, require_admin_or_raise
from .views import ensure_registration_message


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.backup_manager = BackupManager(bot)

    def cog_unload(self) -> None:
        self.backup_manager.cog_unload()

    @app_commands.command(name="register_panel", description="Отправить или обновить панель регистрации")
    async def register_panel(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            msg = await ensure_registration_message(self.bot)
            if msg is None:
                raise UserFacingError("Не удалось отправить панель. Проверь config.json.")
            embed = base_embed("Готово", "Панель регистрации отправлена или обновлена.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="system_check", description="Проверка БД, каналов, ролей и прав бота")
    async def system_check(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            assert interaction.guild is not None
            cfg = load_config()
            guild = interaction.guild
            me = guild.me
            lines: list[str] = []

            def status(ok: bool) -> str:
                return "✅" if ok else "❌"

            reg_channel = guild.get_channel(int(cfg.get("registration_channel_id", 0) or 0))
            log_channel = guild.get_channel(int(cfg.get("log_channel_id", 0) or 0))
            welcome_channel = guild.get_channel(int(cfg.get("welcome_channel_id", 0) or 0))
            unreg_role = guild.get_role(int(cfg.get("unregistered_role_id", 0) or 0))
            member_role = guild.get_role(int(cfg.get("member_role_id", 0) or 0))

            lines.append(f"{status(reg_channel is not None)} Канал регистрации")
            lines.append(f"{status(log_channel is not None)} Лог-канал")
            lines.append(f"{status(welcome_channel is not None)} Welcome-канал")
            lines.append(f"{status(unreg_role is not None)} Роль незарегистрированного")
            lines.append(f"{status(member_role is not None)} Роль участника")
            lines.append(f"{status(DB_PATH.exists())} База SQLite")
            lines.append(f"{status(me is not None and me.guild_permissions.manage_roles)} Право Manage Roles")
            lines.append(f"{status(me is not None and me.guild_permissions.manage_nicknames)} Право Manage Nicknames")
            if me and member_role:
                lines.append(f"{status(me.top_role > member_role)} Роль бота выше роли участника")
            if me and unreg_role:
                lines.append(f"{status(me.top_role > unreg_role)} Роль бота выше роли незарегистрированного")

            embed = base_embed("System Check", "\n".join(lines), 0x5865F2)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="restore_user", description="Восстановить пользователя из БД")
    @app_commands.describe(user="Пользователь")
    async def restore_user(self, interaction: discord.Interaction, user: discord.Member) -> None:
        try:
            require_admin_or_raise(interaction)
            result = await restore_member(user, actor_id=interaction.user.id)
            if not result.ok:
                raise UserFacingError(result.public_error or "Не удалось восстановить пользователя.")
            embed = base_embed("Готово", f"Пользователь {user.mention} восстановлен: **{result.normalized_name}**", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="delete_user", description="Удалить пользователя из базы")
    @app_commands.describe(user="Пользователь")
    async def delete_user(self, interaction: discord.Interaction, user: discord.Member) -> None:
        try:
            require_admin_or_raise(interaction)
            deleted = db.delete_user(user.id)
            if not deleted:
                raise UserFacingError("Пользователь не найден в базе.")
            embed = base_embed("Готово", f"Пользователь {user.mention} удалён из базы.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="name_history", description="Показать историю имён пользователя")
    @app_commands.describe(user="Пользователь")
    async def name_history(self, interaction: discord.Interaction, user: discord.Member) -> None:
        try:
            require_admin_or_raise(interaction)
            history = db.get_name_history(user.id, limit=10)
            if not history:
                raise UserFacingError("История имён пуста.")
            lines = []
            for row in history:
                changed_by = row["changed_by"] if row["changed_by"] else "—"
                lines.append(f"**{row['new_name']}** ← {row['old_name'] or '—'} | {row['source']} | changed_by: `{changed_by}`")
            embed = base_embed(f"История имён: {user}", "\n".join(lines), 0x5865F2)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="export_users", description="Экспорт пользователей в CSV")
    async def export_users(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            rows = db.list_users()
            if not rows:
                raise UserFacingError("В базе нет данных для экспорта.")
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            path = EXPORTS_DIR / f"registrations_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            await interaction.response.send_message("Экспорт готов.", file=discord.File(path), ephemeral=True)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="backup_now", description="Создать backup прямо сейчас")
    async def backup_now(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            path = self.backup_manager.create_backup()
            embed = base_embed("Backup создан", f"Файл: `{path.name}`", 0x57F287)
        except Exception:
            embed = base_embed("Ошибка", "Не удалось создать backup.", 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCommands(bot))
