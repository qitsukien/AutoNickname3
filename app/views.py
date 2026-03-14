from __future__ import annotations

import csv
import re
from datetime import datetime, timezone

import discord

from . import db
from .config import EXPORTS_DIR, load_config, panel_hash, save_config
from .logging_utils import base_embed
from .services import build_member_nickname, refresh_member_nickname, register_member, restore_member, validate_nickname_template, validate_name
from .utils import UserFacingError, require_admin_or_raise


async def _reply(interaction: discord.Interaction, *, embed: discord.Embed | None = None, view: discord.ui.View | None = None, file: discord.File | None = None) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, file=file, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, file=file, ephemeral=True)


class RegistrationModal(discord.ui.Modal, title="Регистрация"):
    nickname = discord.ui.TextInput(
        label="Введите имя",
        placeholder="Например: Андрей",
        min_length=2,
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.user, discord.Member)
        result = await register_member(interaction.user, str(self.nickname), actor_id=interaction.user.id, source="modal_register")
        if result.ok:
            embed = base_embed(
                "Регистрация завершена",
                f"Имя сохранено как **{result.normalized_name}**.\nИтоговый ник: **{result.final_nickname or build_member_nickname(interaction.user, result.normalized_name or '')}**",
                0x57F287,
            )
        else:
            embed = base_embed("Ошибка", result.public_error or "Не удалось завершить регистрацию.", 0xED4245)
        await _reply(interaction, embed=embed)


class RegisterButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Зарегистрироваться", style=discord.ButtonStyle.green, custom_id="register_button")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RegistrationModal())


class HelpButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Помощь", style=discord.ButtonStyle.gray, custom_id="help_button")

    async def callback(self, interaction: discord.Interaction) -> None:
        cfg = load_config()
        await _reply(interaction, embed=base_embed("Помощь", str(cfg.get("panel_help_text", ""))))


class RestoreSelfButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Восстановить себя", style=discord.ButtonStyle.blurple, custom_id="restore_self_button")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.user, discord.Member)
        cfg = load_config()
        if not bool(cfg.get("allow_restore_button", False)):
            await _reply(
                interaction,
                embed=base_embed("Отключено", "Самостоятельное восстановление отключено. Обратись к администратору.", 0xED4245),
            )
            return
        result = await restore_member(interaction.user, actor_id=interaction.user.id, source="self_restore")
        if result.ok:
            embed = base_embed("Готово", f"Профиль восстановлен.\nИтоговый ник: **{result.final_nickname}**", 0x57F287)
        else:
            embed = base_embed("Ошибка", result.public_error or "Не удалось восстановить профиль.", 0xED4245)
        await _reply(interaction, embed=embed)


class RegistrationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(RegisterButton())
        self.add_item(HelpButton())
        self.add_item(RestoreSelfButton())


def _status(ok: bool) -> str:
    return "✅" if ok else "❌"


def _extract_user_id(raw: str) -> int | None:
    raw = raw.strip()
    match = re.search(r"(\d{15,25})", raw)
    return int(match.group(1)) if match else None


def _resolve_member(guild: discord.Guild, raw: str) -> discord.Member | None:
    user_id = _extract_user_id(raw)
    return guild.get_member(user_id) if user_id else None


def _admin_main_embed() -> discord.Embed:
    cfg = load_config()
    return base_embed(
        "Функциональная админ-панель",
        "Выбери нужный блок ниже. Все действия здесь скрыты и видны только тебе.\n\n"
        f"**Текущий шаблон ника:** `{cfg.get('nickname_format', '{login} ({name})')}`\n"
        f"**Смена имени:** {'включена' if cfg.get('allow_name_change', True) else 'выключена'}\n"
        f"**Self-restore:** {'включён' if cfg.get('allow_restore_button', False) else 'выключен'}\n"
        f"**ID панели:** `{cfg.get('registration_message_id', 0)}`",
        0x5865F2,
    )


def _settings_embed() -> discord.Embed:
    cfg = load_config()
    return base_embed(
        "Настройки панели и профиля",
        f"**Шаблон ника:** `{cfg.get('nickname_format', '{login} ({name})')}`\n"
        f"**Смена имени:** {'включена' if cfg.get('allow_name_change', True) else 'выключена'}\n"
        f"**Кнопка self-restore:** {'включена' if cfg.get('allow_restore_button', False) else 'выключена'}\n"
        f"**Лимит длины имени:** {cfg.get('name_min_length', 2)}–{cfg.get('name_max_length', 20)}",
        0x5865F2,
    )


def build_system_check_embed(guild: discord.Guild) -> discord.Embed:
    cfg = load_config()
    me = guild.me
    reg_channel = guild.get_channel(int(cfg.get("registration_channel_id", 0) or 0))
    log_channel = guild.get_channel(int(cfg.get("log_channel_id", 0) or 0))
    welcome_channel = guild.get_channel(int(cfg.get("welcome_channel_id", 0) or 0))
    unreg_role = guild.get_role(int(cfg.get("unregistered_role_id", 0) or 0))
    member_role = guild.get_role(int(cfg.get("member_role_id", 0) or 0))
    lines: list[str] = [
        f"{_status(reg_channel is not None)} Канал регистрации",
        f"{_status(log_channel is not None)} Лог-канал",
        f"{_status(welcome_channel is not None)} Welcome-канал",
        f"{_status(unreg_role is not None)} Роль незарегистрированного",
        f"{_status(member_role is not None)} Роль участника",
        f"{_status(bool(cfg.get('nickname_format')))} Шаблон ника задан",
        f"{_status(bool(cfg.get('registration_message_id')))} ID панели сохранён",
    ]
    try:
        db.list_users()
        db_ok = True
    except Exception:
        db_ok = False
    lines.append(f"{_status(db_ok)} База SQLite")
    lines.append(f"{_status(me is not None and me.guild_permissions.manage_roles)} Право Manage Roles")
    lines.append(f"{_status(me is not None and me.guild_permissions.manage_nicknames)} Право Manage Nicknames")
    lines.append(f"{_status(me is not None and me.guild_permissions.send_messages)} Право Send Messages")
    lines.append(f"{_status(me is not None and me.guild_permissions.embed_links)} Право Embed Links")
    if me and member_role:
        lines.append(f"{_status(me.top_role > member_role)} Роль бота выше роли участника")
    if me and unreg_role:
        lines.append(f"{_status(me.top_role > unreg_role)} Роль бота выше роли незарегистрированного")
    return base_embed("System Check", "\n".join(lines), 0x5865F2)


def build_name_history_embed(user_id: int, title_suffix: str = "") -> discord.Embed:
    history = db.get_name_history(user_id, limit=10)
    if not history:
        raise UserFacingError("История имён пуста.")
    lines: list[str] = []
    for row in history:
        changed_by = row["changed_by"] if row["changed_by"] else "—"
        lines.append(
            f"**{row['new_registered_name']}** → **{row['new_final_nickname']}**\n"
            f"было: {row['old_registered_name'] or '—'} / {row['old_final_nickname'] or '—'}\n"
            f"источник: {row['source']} | actor: `{changed_by}` | {row['changed_at']}"
        )
    return base_embed(f"История имён {title_suffix}".strip(), "\n\n".join(lines), 0x5865F2)


def create_users_export_file() -> discord.File:
    rows = db.list_users()
    if not rows:
        raise UserFacingError("В базе нет данных для экспорта.")
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_DIR / f"registrations_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return discord.File(path)


class AdminRefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Обновить панель", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            msg = await ensure_registration_message(interaction.client, force=True)
            if msg is None:
                raise UserFacingError("Не удалось обновить панель регистрации.")
            embed = base_embed("Готово", "Панель регистрации обновлена.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class AdminSystemCheckButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="System Check", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            if interaction.guild is None:
                raise UserFacingError("Команда доступна только на сервере.")
            embed = build_system_check_embed(interaction.guild)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class AdminBackupButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Сделать backup", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            cog = interaction.client.get_cog("AdminCommands")
            if cog is None or not hasattr(cog, "backup_manager"):
                raise UserFacingError("Менеджер backup не найден.")
            path = cog.backup_manager.create_backup()
            embed = base_embed("Backup создан", f"Файл: `{path.name}`", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        except Exception:
            embed = base_embed("Ошибка", "Не удалось создать backup.", 0xED4245)
        await _reply(interaction, embed=embed)


class AdminExportUsersButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Экспорт CSV", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            file = create_users_export_file()
            embed = base_embed("Экспорт готов", "CSV-файл сформирован.", 0x57F287)
            await _reply(interaction, embed=embed, file=file)
            return
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class OpenUserToolsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Пользователи", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            embed = base_embed(
                "Инструменты пользователя",
                "Здесь можно восстановить профиль, переименовать участника, пересобрать ник, удалить запись из БД или посмотреть историю имён.",
                0x5865F2,
            )
            await _reply(interaction, embed=embed, view=AdminUserToolsView())
            return
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
            await _reply(interaction, embed=embed)


class OpenSettingsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Настройки", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            await _reply(interaction, embed=_settings_embed(), view=AdminSettingsView())
            return
        except UserFacingError as e:
            await _reply(interaction, embed=base_embed("Ошибка", e.public_message, 0xED4245))


class AdminPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(AdminRefreshButton())
        self.add_item(AdminSystemCheckButton())
        self.add_item(AdminBackupButton())
        self.add_item(AdminExportUsersButton())
        self.add_item(OpenUserToolsButton())
        self.add_item(OpenSettingsButton())


class RestoreUserModal(discord.ui.Modal, title="Восстановить пользователя"):
    user_ref = discord.ui.TextInput(label="ID или mention пользователя", placeholder="1481855136622968834")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            if interaction.guild is None:
                raise UserFacingError("Команда доступна только на сервере.")
            member = _resolve_member(interaction.guild, str(self.user_ref))
            if member is None:
                raise UserFacingError("Пользователь не найден на сервере.")
            result = await restore_member(member, actor_id=interaction.user.id, source="admin_panel_restore")
            if not result.ok:
                raise UserFacingError(result.public_error or "Не удалось восстановить пользователя.")
            embed = base_embed("Готово", f"Пользователь {member.mention} восстановлен.\nИтоговый ник: **{result.final_nickname}**", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class RenameUserModal(discord.ui.Modal, title="Переименовать пользователя"):
    user_ref = discord.ui.TextInput(label="ID или mention пользователя", placeholder="1481855136622968834")
    new_name = discord.ui.TextInput(label="Новое имя", placeholder="Андрей", min_length=2, max_length=32)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            if interaction.guild is None:
                raise UserFacingError("Команда доступна только на сервере.")
            member = _resolve_member(interaction.guild, str(self.user_ref))
            if member is None:
                raise UserFacingError("Пользователь не найден на сервере.")
            result = await register_member(member, str(self.new_name), actor_id=interaction.user.id, source="admin_panel_rename")
            if not result.ok:
                raise UserFacingError(result.public_error or "Не удалось изменить имя.")
            embed = base_embed("Готово", f"Пользователь: {member.mention}\nИмя: **{result.normalized_name}**\nИтоговый ник: **{result.final_nickname}**", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class RefreshNickModal(discord.ui.Modal, title="Пересобрать ник"):
    user_ref = discord.ui.TextInput(label="ID или mention пользователя", placeholder="1481855136622968834")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            if interaction.guild is None:
                raise UserFacingError("Команда доступна только на сервере.")
            member = _resolve_member(interaction.guild, str(self.user_ref))
            if member is None:
                raise UserFacingError("Пользователь не найден на сервере.")
            result = await refresh_member_nickname(member, actor_id=interaction.user.id, source="admin_panel_refresh_nick")
            if not result.ok:
                raise UserFacingError(result.public_error or "Не удалось обновить ник.")
            embed = base_embed("Готово", f"Ник пересобран: **{result.final_nickname}**", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class DeleteUserModal(discord.ui.Modal, title="Удалить запись из БД"):
    user_ref = discord.ui.TextInput(label="ID или mention пользователя", placeholder="1481855136622968834")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            user_id = _extract_user_id(str(self.user_ref))
            if not user_id:
                raise UserFacingError("Не удалось распознать ID пользователя.")
            deleted = db.delete_user(user_id)
            if not deleted:
                raise UserFacingError("Пользователь не найден в базе.")
            embed = base_embed("Готово", f"Запись пользователя `{user_id}` удалена из базы.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class NameHistoryModal(discord.ui.Modal, title="История имён"):
    user_ref = discord.ui.TextInput(label="ID или mention пользователя", placeholder="1481855136622968834")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            user_id = _extract_user_id(str(self.user_ref))
            if not user_id:
                raise UserFacingError("Не удалось распознать ID пользователя.")
            title_suffix = f"для `{user_id}`"
            if interaction.guild is not None:
                member = interaction.guild.get_member(user_id)
                if member is not None:
                    title_suffix = f"для {member}"
            embed = build_name_history_embed(user_id, title_suffix)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class CheckNameModal(discord.ui.Modal, title="Предпросмотр ника"):
    user_ref = discord.ui.TextInput(label="ID или mention пользователя", placeholder="1481855136622968834")
    name = discord.ui.TextInput(label="Имя для проверки", placeholder="Андрей", min_length=2, max_length=32)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            if interaction.guild is None:
                raise UserFacingError("Команда доступна только на сервере.")
            member = _resolve_member(interaction.guild, str(self.user_ref))
            if member is None:
                raise UserFacingError("Пользователь не найден на сервере.")
            checked = validate_name(str(self.name), member)
            if not checked.ok or not checked.normalized_name:
                raise UserFacingError(checked.public_error or "Имя не прошло проверку.")
            final_nick = build_member_nickname(member, checked.normalized_name)
            embed = base_embed(
                "Предпросмотр ника",
                f"Пользователь: {member.mention}\n"
                f"Login: **{member.name}**\n"
                f"Нормализованное имя: **{checked.normalized_name}**\n"
                f"Итоговый ник: **{final_nick}**",
                0x57F287,
            )
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class OpenModalButton(discord.ui.Button):
    def __init__(self, *, label: str, style: discord.ButtonStyle, modal_factory):
        super().__init__(label=label, style=style)
        self.modal_factory = modal_factory

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            await interaction.response.send_modal(self.modal_factory())
        except UserFacingError as e:
            await _reply(interaction, embed=base_embed("Ошибка", e.public_message, 0xED4245))


class AdminUserToolsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(OpenModalButton(label="Восстановить", style=discord.ButtonStyle.success, modal_factory=RestoreUserModal))
        self.add_item(OpenModalButton(label="Переименовать", style=discord.ButtonStyle.primary, modal_factory=RenameUserModal))
        self.add_item(OpenModalButton(label="Пересобрать ник", style=discord.ButtonStyle.secondary, modal_factory=RefreshNickModal))
        self.add_item(OpenModalButton(label="Удалить из БД", style=discord.ButtonStyle.danger, modal_factory=DeleteUserModal))
        self.add_item(OpenModalButton(label="История имён", style=discord.ButtonStyle.secondary, modal_factory=NameHistoryModal))
        self.add_item(OpenModalButton(label="Предпросмотр", style=discord.ButtonStyle.secondary, modal_factory=CheckNameModal))


class SetNickFormatModal(discord.ui.Modal, title="Сменить шаблон ника"):
    template = discord.ui.TextInput(label="Шаблон", placeholder="{login} ({name})", max_length=32)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            checked = validate_nickname_template(str(self.template))
            if not checked.ok:
                raise UserFacingError(checked.public_error or "Неверный шаблон.")
            cfg = load_config()
            cfg["nickname_format"] = str(self.template)
            save_config(cfg)
            await ensure_registration_message(interaction.client, force=True)
            embed = base_embed("Готово", f"Новый шаблон: `{self.template}`\nПанель регистрации обновлена.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed)


class ToggleNameChangeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Вкл/выкл смену имени", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            cfg = load_config()
            cfg["allow_name_change"] = not bool(cfg.get("allow_name_change", True))
            save_config(cfg)
            embed = base_embed("Настройка обновлена", f"Смена имени теперь {'включена' if cfg['allow_name_change'] else 'выключена'}.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed, view=AdminSettingsView())


class ToggleRestoreButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Вкл/выкл self-restore", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            cfg = load_config()
            cfg["allow_restore_button"] = not bool(cfg.get("allow_restore_button", False))
            save_config(cfg)
            await ensure_registration_message(interaction.client, force=True)
            embed = base_embed("Настройка обновлена", f"Кнопка self-restore теперь {'включена' if cfg['allow_restore_button'] else 'выключена'}.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await _reply(interaction, embed=embed, view=AdminSettingsView())


class OpenSetNickFormatButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Сменить шаблон", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            await interaction.response.send_modal(SetNickFormatModal())
        except UserFacingError as e:
            await _reply(interaction, embed=base_embed("Ошибка", e.public_message, 0xED4245))


class AdminSettingsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(OpenSetNickFormatButton())
        self.add_item(ToggleNameChangeButton())
        self.add_item(ToggleRestoreButton())
        self.add_item(AdminRefreshButton())


async def build_registration_embed() -> discord.Embed:
    cfg = load_config()
    embed = discord.Embed(
        title=str(cfg.get("panel_title", "Регистрация участников")),
        description=(
            f"{cfg.get('panel_description', '')}\n\n"
            f"**Текущий шаблон ника:** `{cfg.get('nickname_format', '{login} ({name})')}`\n"
            "**Пример:** `qwerty (Андрей)`"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text=f"Guild ID: {cfg.get('guild_id', 0)}")
    return embed


async def ensure_registration_message(bot: discord.Client, force: bool = False) -> discord.Message | None:
    cfg = load_config()
    guild = bot.get_guild(int(cfg.get("guild_id", 0) or 0))
    if guild is None:
        return None
    channel = guild.get_channel(int(cfg.get("registration_channel_id", 0) or 0))
    if not isinstance(channel, discord.TextChannel):
        return None

    message_id = int(cfg.get("registration_message_id", 0) or 0)
    embed = await build_registration_embed()
    view = RegistrationView()
    current_hash = panel_hash(embed.title or "", embed.description or "", str(cfg.get("nickname_format", "")))

    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            if force or cfg.get("last_panel_message_hash") != current_hash:
                await msg.edit(embed=embed, view=view)
                cfg["last_panel_message_hash"] = current_hash
                cfg["last_panel_refresh_at"] = discord.utils.utcnow().isoformat()
                save_config(cfg)
            return msg
        except discord.NotFound:
            pass
        except discord.HTTPException:
            pass

    msg = await channel.send(embed=embed, view=view)
    cfg["registration_message_id"] = msg.id
    cfg["last_panel_message_hash"] = current_hash
    cfg["last_panel_refresh_at"] = discord.utils.utcnow().isoformat()
    save_config(cfg)
    return msg


__all__ = [
    "AdminPanelView",
    "AdminSettingsView",
    "AdminUserToolsView",
    "RegistrationView",
    "build_system_check_embed",
    "build_name_history_embed",
    "create_users_export_file",
    "ensure_registration_message",
    "_admin_main_embed",
]
