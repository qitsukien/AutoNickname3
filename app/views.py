from __future__ import annotations

import discord

from .config import load_config, panel_hash, save_config
from .logging_utils import base_embed
from .services import build_member_nickname, register_member, restore_member
from .utils import UserFacingError, require_admin_or_raise


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
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


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
        await interaction.response.send_message(embed=base_embed("Помощь", str(cfg.get("panel_help_text", ""))), ephemeral=True)


class RestoreSelfButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Восстановить себя", style=discord.ButtonStyle.blurple, custom_id="restore_self_button")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.user, discord.Member)
        cfg = load_config()
        if not bool(cfg.get("allow_restore_button", False)):
            await interaction.response.send_message(embed=base_embed("Отключено", "Самостоятельное восстановление отключено. Обратись к администратору.", 0xED4245), ephemeral=True)
            return
        result = await restore_member(interaction.user, actor_id=interaction.user.id, source="self_restore")
        if result.ok:
            embed = base_embed("Готово", f"Профиль восстановлен.\nИтоговый ник: **{result.final_nickname}**", 0x57F287)
        else:
            embed = base_embed("Ошибка", result.public_error or "Не удалось восстановить профиль.", 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminRefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Обновить панель", style=discord.ButtonStyle.red, custom_id="admin_refresh_panel")

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            msg = await ensure_registration_message(interaction.client, force=True)
            if msg is None:
                raise UserFacingError("Не удалось обновить панель регистрации.")
            embed = base_embed("Готово", "Панель регистрации обновлена.", 0x57F287)
        except UserFacingError as e:
            embed = base_embed("Ошибка", e.public_message, 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RegistrationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(RegisterButton())
        self.add_item(HelpButton())
        self.add_item(RestoreSelfButton())


class AdminPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
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
