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
from .views import AdminPanelView, ensure_registration_message


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
            embed = base_embed(
                "Админ-панель",
                "Эта панель видна только тебе. Для остальных участников доступны только публичные кнопки регистрации.",
            )
            await interaction.response.send_message(embed=embed, view=AdminPanelView(), ephemeral=True)
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
            lines.append(f"{status(bool(cfg.get('nickname_format')))} Шаблон ника задан")
            lines.append(f"{status(bool(cfg.get('registration_message_id')))} ID панели сохранён")
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
            history = db.get_name_history(user.id, limit=10)
            if not history:
                raise UserFacingError("История имён пуста.")
            lines = []
            for row in history:
                changed_by = row["changed_by"] if row["changed_by"] else "—"
                lines.append(
                    f"**{row['new_registered_name']}** → **{row['new_final_nickname']}**\n"
                    f"было: {row['old_registered_name'] or '—'} / {row['old_final_nickname'] or '—'}\n"
                    f"источник: {row['source']} | actor: `{changed_by}` | {row['changed_at']}"
                )
            embed = base_embed(f"История имён: {user}", "\n\n".join(lines), 0x5865F2)
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
