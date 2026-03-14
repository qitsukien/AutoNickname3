from __future__ import annotations

import discord

from .config import load_config, save_config
from .logging_utils import base_embed
from .services import build_member_nickname, register_member, restore_member
from .utils import UserFacingError, require_admin_or_raise


class RegistrationModal(discord.ui.Modal, title="Регистрация"):
    nickname = discord.ui.TextInput(
        label="Введите имя",
        placeholder="Например: Денис",
        min_length=2,
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        result = await register_member(interaction.user, str(self.nickname), actor_id=interaction.user.id)
        if result.ok:
            embed = base_embed(
                "Регистрация завершена",
                f"Твоё имя сохранено как **{result.normalized_name}**.\nИтоговый ник: **{build_member_nickname(interaction.user, result.normalized_name)}**",
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


class RestoreSelfButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Восстановить из БД", style=discord.ButtonStyle.blurple, custom_id="restore_self_button")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        cfg = load_config()
        if not bool(cfg.get("allow_restore_button", True)):
            await interaction.response.send_message(embed=base_embed("Отключено", "Кнопка восстановления временно отключена.", 0xED4245), ephemeral=True)
            return
        result = await restore_member(interaction.user, actor_id=interaction.user.id)
        if result.ok:
            embed = base_embed("Готово", f"Имя восстановлено: **{result.normalized_name}**\nИтоговый ник: **{build_member_nickname(interaction.user, result.normalized_name)}**", 0x57F287)
        else:
            embed = base_embed("Ошибка", result.public_error or "Не удалось восстановить пользователя.", 0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HelpButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Помощь", style=discord.ButtonStyle.gray, custom_id="help_button")

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = base_embed(
            "Помощь",
            "Если ты новый участник, нажми **Зарегистрироваться**.\n"
            "Если уже был в базе и зашёл заново — нажми **Восстановить из БД**.\n"
            "Если бот не меняет роль или ник, сообщи администратору.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminRefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Обновить панель", style=discord.ButtonStyle.red, custom_id="admin_refresh_panel")

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            require_admin_or_raise(interaction)
            msg = await ensure_registration_message(interaction.client)
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
        self.add_item(RestoreSelfButton())
        self.add_item(HelpButton())


class AdminPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.add_item(AdminRefreshButton())


async def build_registration_embed() -> discord.Embed:
    cfg = load_config()
    embed = discord.Embed(
        title="Регистрация участников",
        description=(
            "Нажми кнопку ниже, чтобы зарегистрироваться.\n\n"
            "**Что произойдёт после регистрации:**\n"
            "• снимется роль незарегистрированного\n"
            "• выдастся роль участника\n"
            "• бот сохранит твой логин и имя в базе\n"
            "• ник будет в формате `логин (Имя)`\n"
            "• при повторном входе можно восстановиться из БД"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text=f"Guild ID: {cfg.get('guild_id', 0)}")
    return embed


async def ensure_registration_message(bot: discord.Client) -> discord.Message | None:
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

    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed, view=view)
            return msg
        except discord.NotFound:
            pass
        except discord.HTTPException:
            pass

    msg = await channel.send(embed=embed, view=view)
    cfg["registration_message_id"] = msg.id
    save_config(cfg)
    return msg
