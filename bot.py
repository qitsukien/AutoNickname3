import os
import re
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
REGISTRATION_CHANNEL_ID = int(os.getenv("REGISTRATION_CHANNEL_ID", "0"))

if not TOKEN:
    raise RuntimeError("Не найден DISCORD_TOKEN в .env")
if not GUILD_ID:
    raise RuntimeError("Не найден GUILD_ID в .env")
if not REGISTRATION_CHANNEL_ID:
    raise RuntimeError("Не найден REGISTRATION_CHANNEL_ID в .env")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Храним пользователей, которые сейчас ожидают регистрацию
pending_registration: set[int] = set()


def normalize_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\s+", " ", value)
    return value


def is_valid_name(value: str) -> bool:
    """
    Допускаем русские/английские буквы, пробел и дефис.
    Примеры:
    Денис
    Анна-Мария
    Ivan
    """
    if not (2 <= len(value) <= 20):
        return False
    return bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё -]+", value))


def build_nickname(member: discord.Member, real_name: str) -> str:
    """
    Формат ника:
    ghost.run (Денис)

    Берём текущий display_name, а если он пустой — username.
    Ограничение Discord: максимум 32 символа.
    """
    base = member.display_name or member.name

    # Если вдруг бот вызывается повторно, убираем уже добавленное "(Имя)" в конце
    base = re.sub(r"\s*\([^)]+\)$", "", base).strip()

    nickname = f"{base} ({real_name})"
    return nickname[:32]


async def get_registration_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(REGISTRATION_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    channel = await get_registration_channel(member.guild)
    if channel is None:
        return

    pending_registration.add(member.id)

    await channel.send(
        f"{member.mention}, привет!\n"
        f"Напиши своё **имя** одним сообщением в этот канал.\n\n"
        f"После этого я изменю твой ник на формат:\n"
        f"`{member.name} (Твоё имя)`"
    )


@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    if message.author.bot:
        return
    if message.guild is None:
        return
    if message.guild.id != GUILD_ID:
        return
    if message.channel.id != REGISTRATION_CHANNEL_ID:
        return
    if message.author.id not in pending_registration:
        return

    member = message.author
    real_name = normalize_name(message.content)

    if not is_valid_name(real_name):
        await message.channel.send(
            f"{member.mention}, имя должно быть от 2 до 20 символов и содержать только буквы, пробел или дефис.\n"
            f"Пример: `Денис` или `Анна-Мария`"
        )
        return

    new_nick = build_nickname(member, real_name)

    try:
        await member.edit(nick=new_nick, reason="Регистрация нового участника")
    except discord.Forbidden:
        await message.channel.send(
            f"{member.mention}, я не смог изменить ник.\n"
            f"Проверь, что у бота есть право **Manage Nicknames** "
            f"и его роль находится выше роли участника."
        )
        return
    except discord.HTTPException:
        await message.channel.send(
            f"{member.mention}, Discord временно не дал изменить ник. Попробуй отправить имя ещё раз."
        )
        return

    pending_registration.discard(member.id)

    await message.channel.send(
        f"{member.mention}, готово ✅\n"
        f"Твой новый ник: `{new_nick}`"
    )


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")


bot.run(TOKEN)
