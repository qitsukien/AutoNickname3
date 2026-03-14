from __future__ import annotations

import csv
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from . import db
from .backup import BackupManager
from .config import DB_PATH, EXPORTS_DIR, load_config, save_config
from .logging_utils import base_embed
from .services import (
    build_member_nickname,
    refresh_member_nickname,
    register_member,
    restore_member,
    validate_name,
    validate_nickname_template,
)
from .utils import UserFacingError, require_admin_or_raise
from .views import (
    AdminPanelView,
    _admin_main_embed,
    build_name_history_embed,
    build_system_check_embed,
    create_users_export_file,
    ensure_registration_message,
)


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
            msg = await ensure_registration_message(self.bot, force=True)
            if msg is None:
                raise UserFacingError("Не удалось отправить панель. Проверь config.json.")
            embed = base_embed("Готово", "Панель регистрации отправлена или обновлена.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="admin_panel", description="Открыть скрытую админ-панель")
    async def admin_panel(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            await interaction.response.send_message(embed=_admin_main_embed(), view=AdminPanelView(), ephemeral=True)
            return
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="check_name", description="Предпросмотр итогового ника")
    @app_commands.describe(name="Имя для проверки", user="Для какого пользователя собрать ник")
    async def check_name(self, interaction: discord.Interaction, name: str, user: discord.Member | None = None) -> None:
        target = user if user is not None else interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(embed=base_embed("Ошибка", "Нужен участник сервера.", 0xED4245), ephemeral=True)
            return
        try:
            checked = validate_name(name, target)
            if not checked.ok or not checked.normalized_name:
                raise UserFacingError(checked.public_error or "Имя не прошло проверку.")
            final_nick = build_member_nickname(target, checked.normalized_name)
            embed = base_embed(
                "Предпросмотр ника",
                f"Пользователь: {target.mention}\n"
                f"Login: **{target.name}**\n"
                f"Нормализованное имя: **{checked.normalized_name}**\n"
                f"Итоговый ник: **{final_nick}**",
                0x57F287,
            )
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="system_check", description="Проверка БД, каналов, ролей и прав бота")
    async def system_check(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            assert interaction.guild is not None
            embed = build_system_check_embed(interaction.guild)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="restore_user", description="Восстановить пользователя из БД")
    @app_commands.describe(user="Пользователь")
    async def restore_user(self, interaction: discord.Interaction, user: discord.Member) -> None:
        try:
            require_admin_or_raise(interaction)
            result = await restore_member(user, actor_id=interaction.user.id, source="admin_restore")
            if not result.ok:
                raise UserFacingError(result.public_error or "Не удалось восстановить пользователя.")
            embed = base_embed("Готово", f"Пользователь {user.mention} восстановлен.\nИтоговый ник: **{result.final_nickname}**", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="rename_user", description="Переименовать пользователя через бот")
    @app_commands.describe(user="Пользователь", name="Новое имя")
    async def rename_user(self, interaction: discord.Interaction, user: discord.Member, name: str) -> None:
        try:
            require_admin_or_raise(interaction)
            result = await register_member(user, name, actor_id=interaction.user.id, source="admin_rename")
            if not result.ok:
                raise UserFacingError(result.public_error or "Не удалось изменить имя.")
            embed = base_embed("Готово", f"Новое имя: **{result.normalized_name}**\nИтоговый ник: **{result.final_nickname}**", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="refresh_user_nick", description="Пересобрать ник по текущему шаблону")
    @app_commands.describe(user="Пользователь")
    async def refresh_user_nick(self, interaction: discord.Interaction, user: discord.Member) -> None:
        try:
            require_admin_or_raise(interaction)
            result = await refresh_member_nickname(user, actor_id=interaction.user.id, source="refresh_format")
            if not result.ok:
                raise UserFacingError(result.public_error or "Не удалось обновить ник.")
            embed = base_embed("Готово", f"Ник пересобран: **{result.final_nickname}**", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="set_nick_format", description="Сменить шаблон ника")
    @app_commands.describe(template="Например: {login} ({name})")
    async def set_nick_format(self, interaction: discord.Interaction, template: str) -> None:
        try:
            require_admin_or_raise(interaction)
            checked = validate_nickname_template(template)
            if not checked.ok:
                raise UserFacingError(checked.public_error or "Неверный шаблон.")
            cfg = load_config()
            cfg["nickname_format"] = template
            save_config(cfg)
            await ensure_registration_message(self.bot, force=True)
            embed = base_embed("Готово", f"Новый шаблон: `{template}`\nБаза не переписывалась — ник будет пересобираться из отдельных полей.", 0x57F287)
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
            embed = build_name_history_embed(user.id, f"для {user}")
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="export_users", description="Экспорт пользователей в CSV")
    async def export_users(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            file = create_users_export_file()
            await interaction.response.send_message(embed=base_embed("Экспорт готов", "CSV-файл сформирован.", 0x57F287), file=file, ephemeral=True)
        except UserFacingError as e:
            await interaction.response.send_message(embed=base_embed("Ошибка", e.public_message, 0xED4245), ephemeral=True)

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
