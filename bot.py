import csv
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CONFIG_PATH = Path("config.json")
DB_PATH = Path("registrations.db")
EXPORTS_DIR = Path("exports")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError("Файл config.json не найден.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


config = load_config()
GUILD_ID = int(config["guild_id"])

intents = discord.Intents.default()
intents.members = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)


class LogType(Enum):
    JOIN = "join"
    REGISTER = "register"
    RESTORE = "restore"
    RENAME = "rename"
    ADMIN = "admin"
    ERROR = "error"


class AdminAction(Enum):
    DELETE_DB_USER = "delete_db_user"
    RESET_DB_USER = "reset_db_user"


# =========================
# Антимат только для имени
# =========================

LEET_MAP = {
    "@": "a",
    "4": "a",
    "3": "e",
    "1": "i",
    "!": "i",
    "|": "i",
    "0": "o",
    "$": "s",
    "5": "s",
    "7": "t",
    "+": "t",
    "8": "b",
    "9": "g",
}

CYRILLIC_TO_LATIN_SIMILAR = {
    "а": "a",
    "е": "e",
    "ё": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "к": "k",
    "м": "m",
    "т": "t",
    "в": "b",
    "н": "h",
}

RU_PROFANITY_PATTERNS = [
    r"ху[йиеяюёйлл]*",
    r"пизд[аыуюеёитьец]*",
    r"еб[а-яё]*",
    r"ёб[а-яё]*",
    r"бля[а-яё]*",
    r"бляд[а-яё]*",
    r"сук[а-яё]*",
    r"муд[а-яё]*",
    r"гандон[а-яё]*",
    r"пид[а-яё]*р",
    r"педик[а-яё]*",
    r"долбоеб[а-яё]*",
    r"уеб[а-яё]*",
    r"заеб[а-яё]*",
    r"наеб[а-яё]*",
    r"поеб[а-яё]*",
    r"выеб[а-яё]*",
    r"оху[а-яё]*",
    r"ниху[а-яё]*",
]

EN_PROFANITY_PATTERNS = [
    r"f+u+c+k+",
    r"f+\W*u+\W*c+\W*k+",
    r"b+i+t+c+h+",
    r"s+h+i+t+",
    r"d+i+c+k+",
    r"p+u+s+s+y+",
    r"w+h+o+r+e+",
    r"s+l+u+t+",
    r"m+o+t+h+e+r+f+u+c+k+e*r*",
    r"n+i+g+g+[ae]r*",
    r"f+a+g+[go]t*",
    r"c+u+n+t+",
    r"a+s+s+h+o+l+e+",
]


def strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_leetspeak(text: str) -> str:
    return "".join(LEET_MAP.get(ch, ch) for ch in text)


def normalize_similar_letters(text: str) -> str:
    return "".join(CYRILLIC_TO_LATIN_SIMILAR.get(ch.lower(), ch.lower()) for ch in text)


def collapse_separators(text: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]", "", text, flags=re.IGNORECASE)


def build_text_variants(text: str) -> list[str]:
    text = text.lower()
    text = strip_diacritics(text)
    text = normalize_leetspeak(text)

    variant_1 = text
    variant_2 = normalize_similar_letters(text)
    variant_3 = collapse_separators(variant_1)
    variant_4 = collapse_separators(variant_2)

    unique: list[str] = []
    for item in [variant_1, variant_2, variant_3, variant_4]:
        if item not in unique:
            unique.append(item)
    return unique


def contains_profanity(text: str) -> tuple[bool, Optional[str]]:
    variants = build_text_variants(text)

    for variant in variants:
        for pattern in RU_PROFANITY_PATTERNS:
            if re.search(pattern, variant, flags=re.IGNORECASE):
                return True, f"RU:{pattern}"

        for pattern in EN_PROFANITY_PATTERNS:
            if re.search(pattern, variant, flags=re.IGNORECASE):
                return True, f"EN:{pattern}"

    return False, None


# =========================
# База данных
# =========================

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            discord_name TEXT NOT NULL,
            real_name TEXT NOT NULL,
            nickname TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, guild_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rename_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            old_real_name TEXT,
            new_real_name TEXT NOT NULL,
            changed_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    EXPORTS_DIR.mkdir(exist_ok=True)


def get_registration(user_id: int, guild_id: int) -> Optional[sqlite3.Row]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM registrations
        WHERE user_id = ? AND guild_id = ?
    """, (user_id, guild_id))
    row = cur.fetchone()
    conn.close()
    return row


def get_all_registrations(guild_id: int) -> list[sqlite3.Row]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM registrations
        WHERE guild_id = ?
        ORDER BY updated_at DESC
    """, (guild_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_registration(
    user_id: int,
    guild_id: int,
    discord_name: str,
    real_name: str,
    nickname: str
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    existing = get_registration(user_id, guild_id)

    conn = get_db_connection()
    cur = conn.cursor()

    if existing:
        cur.execute("""
            UPDATE registrations
            SET discord_name = ?, real_name = ?, nickname = ?, updated_at = ?
            WHERE user_id = ? AND guild_id = ?
        """, (discord_name, real_name, nickname, now, user_id, guild_id))
    else:
        cur.execute("""
            INSERT INTO registrations (
                user_id, guild_id, discord_name, real_name, nickname,
                registered_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, guild_id, discord_name, real_name, nickname, now, now))

    conn.commit()
    conn.close()


def add_rename_history(
    user_id: int,
    guild_id: int,
    old_real_name: Optional[str],
    new_real_name: str
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO rename_history (
            user_id, guild_id, old_real_name, new_real_name, changed_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, guild_id, old_real_name, new_real_name, now))
    conn.commit()
    conn.close()


def get_last_rename_time(user_id: int, guild_id: int) -> Optional[datetime]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT changed_at
        FROM rename_history
        WHERE user_id = ? AND guild_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id, guild_id))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return datetime.fromisoformat(row["changed_at"])


def delete_registration(user_id: int, guild_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM registrations
        WHERE user_id = ? AND guild_id = ?
    """, (user_id, guild_id))

    deleted = cur.rowcount > 0

    conn.commit()
    conn.close()
    return deleted


def delete_rename_history(user_id: int, guild_id: int) -> int:
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM rename_history
        WHERE user_id = ? AND guild_id = ?
    """, (user_id, guild_id))

    deleted_count = cur.rowcount

    conn.commit()
    conn.close()
    return deleted_count


def get_last_registrations(guild_id: int, limit: int = 10) -> list[sqlite3.Row]:
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM registrations
        WHERE guild_id = ?
        ORDER BY updated_at DESC
        LIMIT ?
    """, (guild_id, limit))

    rows = cur.fetchall()
    conn.close()
    return rows


def export_registrations_to_csv(guild_id: int) -> Optional[Path]:
    rows = get_all_registrations(guild_id)
    if not rows:
        return None

    EXPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    file_path = EXPORTS_DIR / f"registrations_{guild_id}_{timestamp}.csv"

    with open(file_path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile, delimiter=";")
        writer.writerow([
            "user_id",
            "guild_id",
            "discord_name",
            "real_name",
            "nickname",
            "registered_at",
            "updated_at",
        ])

        for row in rows:
            writer.writerow([
                row["user_id"],
                row["guild_id"],
                row["discord_name"],
                row["real_name"],
                row["nickname"],
                row["registered_at"],
                row["updated_at"],
            ])

    return file_path


# =========================
# Вспомогательные функции
# =========================

def is_valid_name(name: str) -> bool:
    name = name.strip()
    if not (2 <= len(name) <= 20):
        return False
    return bool(re.fullmatch(r"[А-Яа-яЁё -]+", name))


def extract_base_name(member: discord.Member) -> str:
    source = member.nick if member.nick else member.name
    match = re.match(r"^(.*?)\s*\([^()]+\)\s*$", source)
    return match.group(1).strip() if match else source.strip()


def build_nickname(base_name: str, real_name: str) -> str:
    return f"{base_name} ({real_name})"


def get_unregistered_role(guild: discord.Guild) -> Optional[discord.Role]:
    return guild.get_role(int(config["unregistered_role_id"]))


def get_member_role(guild: discord.Guild) -> Optional[discord.Role]:
    return guild.get_role(int(config["member_role_id"]))


def get_registration_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(int(config["registration_channel_id"]))
    return channel if isinstance(channel, discord.TextChannel) else None


def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(int(config["log_channel_id"]))
    return channel if isinstance(channel, discord.TextChannel) else None


def get_welcome_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(int(config["welcome_channel_id"]))
    return channel if isinstance(channel, discord.TextChannel) else None


def format_dt(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    return dt.strftime("%d.%m.%Y %H:%M UTC")


def make_success_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"✅ {title}",
        description=description,
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Успешно")
    return embed


def make_error_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚠️ {title}",
        description=description,
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Ошибка")
    return embed


def make_warning_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚡ {title}",
        description=description,
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Требуется внимание")
    return embed


def make_info_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"📘 {title}",
        description=description,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Информация")
    return embed


def make_registration_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Регистрация участников",
        description=(
            "Добро пожаловать на сервер.\n"
            "Для доступа ко всем каналам пройдите короткую регистрацию."
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(
        name="┌ Что нужно сделать",
        value=(
            "• Нажмите **«Указать имя»**\n"
            "• Введите своё имя в появившемся окне\n"
            "• Бот добавит имя к вашему нику автоматически"
        ),
        inline=False
    )

    embed.add_field(
        name="├ После регистрации",
        value=(
            "• снимется роль **Не зарегистрирован**\n"
            "• выдастся роль **Участник**\n"
            "• откроется доступ к серверу"
        ),
        inline=False
    )

    embed.add_field(
        name="└ Ограничения на имя",
        value=(
            "• только русские буквы, пробелы и дефис\n"
            "• без мата, оскорблений и маскировки символами"
        ),
        inline=False
    )

    embed.set_footer(text="Нажмите кнопку ниже, чтобы продолжить")
    return embed


def make_welcome_embed(member: discord.Member, nickname: str) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 Новый участник",
        description=(
            f"Поприветствуем {member.mention}!\n\n"
            f"Пользователь успешно завершил регистрацию\n"
            f"и теперь известен как **{nickname}**."
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="👋 Добро пожаловать",
        value="Надеемся, вам понравится на сервере.",
        inline=False
    )

    embed.add_field(
        name="📣 Что дальше",
        value=(
            "• загляните в важные каналы\n"
            "• познакомьтесь с участниками\n"
            "• приятного общения"
        ),
        inline=False
    )

    embed.set_footer(text=f"User ID: {member.id}")
    return embed


def make_log_embed(
    log_type: LogType,
    title: str,
    description: str,
    member: Optional[discord.Member] = None,
    moderator: Optional[discord.abc.User] = None
) -> discord.Embed:
    styles = {
        LogType.JOIN: {"color": discord.Color.orange(), "emoji": "📥"},
        LogType.REGISTER: {"color": discord.Color.green(), "emoji": "✅"},
        LogType.RESTORE: {"color": discord.Color.blue(), "emoji": "♻️"},
        LogType.RENAME: {"color": discord.Color.gold(), "emoji": "✏️"},
        LogType.ADMIN: {"color": discord.Color.dark_teal(), "emoji": "🛠️"},
        LogType.ERROR: {"color": discord.Color.red(), "emoji": "⚠️"},
    }

    style = styles[log_type]

    embed = discord.Embed(
        title=f"{style['emoji']} {title}",
        description=description,
        color=style["color"],
        timestamp=datetime.now(timezone.utc)
    )

    if member:
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Пользователь",
            value=f"{member.mention}\n`{member.id}`",
            inline=True
        )
        embed.add_field(
            name="Аккаунт",
            value=f"`{member.name}`",
            inline=True
        )

    if moderator:
        embed.add_field(
            name="Администратор",
            value=f"{moderator.mention}\n`{moderator.id}`",
            inline=False
        )

    embed.set_footer(text="Registration System Logs")
    return embed


async def send_log(
    guild: discord.Guild,
    log_type: LogType,
    title: str,
    description: str,
    member: Optional[discord.Member] = None,
    moderator: Optional[discord.abc.User] = None
) -> None:
    channel = get_log_channel(guild)
    if not channel:
        return

    embed = make_log_embed(
        log_type=log_type,
        title=title,
        description=description,
        member=member,
        moderator=moderator
    )

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        print("Нет прав на отправку логов.")
    except discord.HTTPException as e:
        print(f"Ошибка отправки логов: {e}")


# =========================
# Постоянное сообщение регистрации
# =========================

async def ensure_registration_message(guild: discord.Guild) -> Optional[discord.Message]:
    channel = get_registration_channel(guild)
    if channel is None:
        return None

    message_id = int(config.get("registration_message_id", 0))

    if message_id:
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            pass
        except discord.Forbidden:
            print("Нет прав для получения сообщения регистрации.")
            return None
        except discord.HTTPException as e:
            print(f"Ошибка при получении сообщения регистрации: {e}")
            return None

    embed = make_registration_embed()

    try:
        message = await channel.send(embed=embed, view=RegistrationView())
    except discord.Forbidden:
        print("Нет прав на отправку постоянного сообщения регистрации.")
        return None
    except discord.HTTPException as e:
        print(f"Ошибка при отправке постоянного сообщения регистрации: {e}")
        return None

    config["registration_message_id"] = message.id
    save_config(config)
    return message


async def refresh_registration_message(guild: discord.Guild) -> bool:
    channel = get_registration_channel(guild)
    if channel is None:
        return False

    message_id = int(config.get("registration_message_id", 0))
    if not message_id:
        return False

    embed = make_registration_embed()

    try:
        message = await channel.fetch_message(message_id)
        await message.edit(embed=embed, view=RegistrationView())
        return True
    except discord.NotFound:
        return False
    except discord.Forbidden:
        print("Нет прав на обновление сообщения регистрации.")
        return False
    except discord.HTTPException as e:
        print(f"Ошибка при обновлении сообщения регистрации: {e}")
        return False


# =========================
# Основная логика регистрации
# =========================

async def apply_registration(
    member: discord.Member,
    real_name: str,
    rename_mode: bool = False
) -> tuple[bool, str]:
    guild = member.guild
    real_name = real_name.strip()

    found_profanity, matched_rule = contains_profanity(real_name)
    if found_profanity:
        await send_log(
            guild,
            LogType.ERROR,
            "Попытка использовать запрещённое имя",
            (
                f"Пользователь попытался указать имя: **{real_name}**\n"
                f"Совпадение: `{matched_rule}`"
            ),
            member=member
        )
        return False, (
            "Имя содержит запрещённые слова или их маскировку.\n"
            "Используйте нормальное имя без мата и оскорблений."
        )

    if not is_valid_name(real_name):
        return False, (
            "Имя должно содержать только русские буквы, пробелы или дефис.\n"
            "Пример: `Денис`, `Анна Мария`, `Анна-Мария`."
        )

    unregistered_role = get_unregistered_role(guild)
    member_role = get_member_role(guild)

    if not unregistered_role:
        return False, "Роль `Не зарегистрирован` не найдена."
    if not member_role:
        return False, "Роль `Участник` не найдена."

    already_registered = unregistered_role not in member.roles
    registration = get_registration(member.id, guild.id)

    if already_registered and not rename_mode:
        return False, "Вы уже зарегистрированы. Используйте кнопку **«Изменить имя»**."

    if rename_mode and not already_registered:
        return False, "Сначала завершите обычную регистрацию."

    if rename_mode:
        cooldown_hours = int(config.get("rename_cooldown_hours", 24))
        last_rename = get_last_rename_time(member.id, guild.id)

        if last_rename:
            next_allowed = last_rename + timedelta(hours=cooldown_hours)
            now = datetime.now(timezone.utc)

            if now < next_allowed:
                remaining = next_allowed - now
                total_seconds = int(remaining.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                return False, (
                    f"Имя можно менять не чаще одного раза в {cooldown_hours} ч.\n"
                    f"Попробуйте снова через **{hours} ч. {minutes} мин.**"
                )

    base_name = extract_base_name(member)
    new_nick = build_nickname(base_name, real_name)

    try:
        await member.edit(nick=new_nick, reason="Регистрация / смена имени")
    except discord.Forbidden:
        return False, (
            "Я не смог изменить ник.\n"
            "Проверьте права `Manage Nicknames` и что роль бота выше роли пользователя."
        )
    except discord.HTTPException:
        return False, "Discord вернул ошибку при изменении ника."

    try:
        if not already_registered:
            if unregistered_role in member.roles:
                await member.remove_roles(unregistered_role, reason="Регистрация завершена")

            if member_role not in member.roles:
                await member.add_roles(member_role, reason="Регистрация завершена")
    except discord.Forbidden:
        return False, (
            "Ник изменён, но я не смог обновить роли.\n"
            "Проверьте право `Manage Roles` и позицию роли бота."
        )
    except discord.HTTPException:
        return False, "Ник изменён, но при обновлении ролей произошла ошибка."

    old_real_name = registration["real_name"] if registration else None

    upsert_registration(
        user_id=member.id,
        guild_id=guild.id,
        discord_name=member.name,
        real_name=real_name,
        nickname=new_nick
    )

    add_rename_history(
        user_id=member.id,
        guild_id=guild.id,
        old_real_name=old_real_name,
        new_real_name=real_name
    )

    if rename_mode:
        await send_log(
            guild,
            LogType.RENAME,
            "Имя изменено",
            f"Пользователь изменил имя на **{real_name}**.\nНовый ник: **{new_nick}**",
            member=member
        )
    else:
        await send_log(
            guild,
            LogType.REGISTER,
            "Пользователь зарегистрирован",
            f"Регистрация завершена.\nИмя: **{real_name}**\nНик: **{new_nick}**",
            member=member
        )

        welcome_channel = get_welcome_channel(guild)
        if welcome_channel:
            embed = make_welcome_embed(member, new_nick)
            try:
                await welcome_channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    return True, new_nick


async def restore_member_from_db(member: discord.Member) -> tuple[bool, str]:
    registration = get_registration(member.id, member.guild.id)
    if not registration:
        return False, "Пользователь не найден в базе."

    real_name = registration["real_name"]
    new_nick = build_nickname(member.name, real_name)

    unregistered_role = get_unregistered_role(member.guild)
    member_role = get_member_role(member.guild)

    try:
        await member.edit(
            nick=new_nick,
            reason="Автовосстановление зарегистрированного пользователя"
        )
    except discord.Forbidden:
        return False, "Не удалось восстановить ник: нет прав."
    except discord.HTTPException:
        return False, "Не удалось восстановить ник из-за ошибки Discord."

    try:
        if unregistered_role and unregistered_role in member.roles:
            await member.remove_roles(
                unregistered_role,
                reason="Пользователь уже зарегистрирован в базе"
            )

        if member_role and member_role not in member.roles:
            await member.add_roles(
                member_role,
                reason="Восстановление роли зарегистрированного пользователя"
            )
    except discord.Forbidden:
        return False, "Ник восстановлен, но не удалось обновить роли."
    except discord.HTTPException:
        return False, "Ник восстановлен, но произошла ошибка при обновлении ролей."

    upsert_registration(
        user_id=member.id,
        guild_id=member.guild.id,
        discord_name=member.name,
        real_name=real_name,
        nickname=new_nick
    )

    await send_log(
        member.guild,
        LogType.RESTORE,
        "Автовосстановление пользователя",
        f"Пользователь уже был в базе.\nИмя восстановлено: **{real_name}**\nНик: **{new_nick}**",
        member=member
    )

    return True, new_nick


# =========================
# Админ-действия
# =========================

async def execute_admin_action(
    guild: discord.Guild,
    action: AdminAction,
    member: discord.Member,
    moderator: discord.abc.User
) -> tuple[bool, str]:
    if action == AdminAction.DELETE_DB_USER:
        registration = get_registration(member.id, guild.id)
        if not registration:
            return False, "У этого пользователя нет записи в базе."

        deleted_registration = delete_registration(member.id, guild.id)
        deleted_history_count = delete_rename_history(member.id, guild.id)

        if not deleted_registration:
            return False, "Не удалось удалить запись из базы."

        await send_log(
            guild,
            LogType.ADMIN,
            "Запись удалена из базы",
            f"Запись пользователя удалена.\nУдалено записей истории имён: **{deleted_history_count}**",
            member=member,
            moderator=moderator
        )

        return True, (
            f"Пользователь **{member}** удалён из базы.\n"
            f"История смен имени: **{deleted_history_count}** записей."
        )

    if action == AdminAction.RESET_DB_USER:
        deleted_registration = delete_registration(member.id, guild.id)
        deleted_history_count = delete_rename_history(member.id, guild.id)

        unregistered_role = get_unregistered_role(guild)
        member_role = get_member_role(guild)

        try:
            await member.edit(nick=None, reason="Администратор сбросил регистрацию")

            if member_role and member_role in member.roles:
                await member.remove_roles(member_role, reason="Сброс регистрации")

            if unregistered_role and unregistered_role not in member.roles:
                await member.add_roles(unregistered_role, reason="Сброс регистрации")
        except discord.Forbidden:
            return False, "Не удалось обновить ник или роли пользователя."
        except discord.HTTPException:
            return False, "Discord вернул ошибку при сбросе пользователя."

        await send_log(
            guild,
            LogType.ADMIN,
            "Регистрация пользователя сброшена",
            f"Пользователь переведён обратно в незарегистрированные.\n"
            f"Запись в базе удалена: **{'да' if deleted_registration else 'нет'}**\n"
            f"История смен имени удалена: **{deleted_history_count}**",
            member=member,
            moderator=moderator
        )

        return True, (
            f"Регистрация пользователя {member.mention} сброшена.\n"
            "Он должен будет пройти регистрацию заново."
        )

    return False, "Неизвестное действие."


# =========================
# UI
# =========================

class NameModal(discord.ui.Modal):
    def __init__(self, rename_mode: bool = False):
        super().__init__(title="Изменить имя" if rename_mode else "Регистрация")
        self.rename_mode = rename_mode

        self.name_input = discord.ui.TextInput(
            label="Введите ваше имя",
            placeholder="Например: Денис",
            min_length=2,
            max_length=20,
            required=True
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Неверное место",
                    "Эту кнопку нужно использовать на сервере."
                ),
                ephemeral=True
            )
            return

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Пользователь не найден",
                    "Не удалось найти вас на сервере."
                ),
                ephemeral=True
            )
            return

        ok, result = await apply_registration(
            member=member,
            real_name=str(self.name_input).strip(),
            rename_mode=self.rename_mode
        )

        if ok:
            await interaction.response.send_message(
                embed=make_success_embed(
                    "Готово",
                    f"Ваш ник теперь: **{result}**"
                ),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Ошибка",
                    result
                ),
                ephemeral=True
            )


class RegistrationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Указать имя",
        style=discord.ButtonStyle.primary,
        emoji="📝",
        custom_id="registration_register"
    )
    async def register_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Неверное место",
                    "Эта кнопка работает только на сервере."
                ),
                ephemeral=True
            )
            return

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Пользователь не найден",
                    "Не удалось найти вас на сервере."
                ),
                ephemeral=True
            )
            return

        unregistered_role = get_unregistered_role(interaction.guild)
        if unregistered_role and unregistered_role not in member.roles:
            await interaction.response.send_message(
                embed=make_warning_embed(
                    "Вы уже зарегистрированы",
                    "Используйте кнопку **«Изменить имя»**."
                ),
                ephemeral=True
            )
            return

        await interaction.response.send_modal(NameModal(rename_mode=False))

    @discord.ui.button(
        label="Изменить имя",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
        custom_id="registration_rename"
    )
    async def rename_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Неверное место",
                    "Эта кнопка работает только на сервере."
                ),
                ephemeral=True
            )
            return

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Пользователь не найден",
                    "Не удалось найти вас на сервере."
                ),
                ephemeral=True
            )
            return

        unregistered_role = get_unregistered_role(interaction.guild)
        if unregistered_role and unregistered_role in member.roles:
            await interaction.response.send_message(
                embed=make_warning_embed(
                    "Сначала регистрация",
                    "Сначала нажмите **«Указать имя»**."
                ),
                ephemeral=True
            )
            return

        await interaction.response.send_modal(NameModal(rename_mode=True))


class ConfirmAdminActionView(discord.ui.View):
    def __init__(
        self,
        requester_id: int,
        target_member_id: int,
        action: AdminAction
    ):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.target_member_id = target_member_id
        self.action = action

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=make_warning_embed(
                    "Нет доступа",
                    "Это подтверждение предназначено не для вас."
                ),
                ephemeral=True
            )
            return False
        return True

    async def disable_all_buttons(self, message: discord.Message) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            await message.edit(view=self)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Неверное место",
                    "Эта кнопка работает только на сервере."
                ),
                ephemeral=True
            )
            return

        member = interaction.guild.get_member(self.target_member_id)
        if member is None:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Пользователь не найден",
                    "Не удалось найти пользователя на сервере."
                ),
                ephemeral=True
            )
            return

        ok, result = await execute_admin_action(
            guild=interaction.guild,
            action=self.action,
            member=member,
            moderator=interaction.user
        )

        await self.disable_all_buttons(interaction.message)

        if ok:
            await interaction.response.send_message(
                embed=make_success_embed(
                    "Готово",
                    result
                ),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=make_error_embed(
                    "Ошибка",
                    result
                ),
                ephemeral=True
            )

        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ) -> None:
        await self.disable_all_buttons(interaction.message)
        await interaction.response.send_message(
            embed=make_warning_embed(
                "Отменено",
                "Действие отменено."
            ),
            ephemeral=True
        )
        self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


# =========================
# Events
# =========================

@bot.event
async def on_ready():
    init_db()
    bot.add_view(RegistrationView())

    guild_obj = discord.Object(id=GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild_obj)
        print(f"Синхронизировано slash-команд: {len(synced)}")
    except Exception as e:
        print(f"Ошибка синхронизации slash-команд: {e}")

    real_guild = bot.get_guild(GUILD_ID)
    if real_guild is not None:
        await ensure_registration_message(real_guild)
        await refresh_registration_message(real_guild)

    print(f"Бот запущен как {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    registration = get_registration(member.id, member.guild.id)

    if registration is not None:
        ok, result = await restore_member_from_db(member)

        if ok:
            try:
                await member.send(
                    embed=make_success_embed(
                        "С возвращением",
                        f"Вы уже были зарегистрированы ранее.\n"
                        f"Ваше имя восстановлено автоматически.\n"
                        f"Текущий ник: **{result}**"
                    )
                )
            except discord.Forbidden:
                pass
            return
        else:
            await send_log(
                member.guild,
                LogType.ERROR,
                "Ошибка автовосстановления",
                result,
                member=member
            )

    unregistered_role = get_unregistered_role(member.guild)

    if unregistered_role:
        try:
            await member.add_roles(
                unregistered_role,
                reason="Новый пользователь ожидает регистрацию"
            )
        except discord.Forbidden:
            print("Нет прав на выдачу роли 'Не зарегистрирован'.")
        except discord.HTTPException as e:
            print(f"Ошибка Discord при выдаче роли: {e}")

    try:
        await member.send(
            embed=make_info_embed(
                "Добро пожаловать",
                "Чтобы завершить регистрацию, перейдите в канал регистрации на сервере "
                "и нажмите кнопку **«Указать имя»**."
            )
        )
    except discord.Forbidden:
        pass

    await send_log(
        member.guild,
        LogType.JOIN,
        "Новый вход",
        "Пользователь зашёл на сервер и ожидает регистрацию.",
        member=member
    )


# =========================
# Slash-команды
# =========================

@bot.tree.command(
    name="setup_registration",
    description="Создать или обновить постоянное сообщение регистрации",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_registration_slash(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    msg = await ensure_registration_message(interaction.guild)
    if msg is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Ошибка",
                "Не удалось создать или найти сообщение регистрации."
            ),
            ephemeral=True
        )
        return

    await refresh_registration_message(interaction.guild)

    await interaction.response.send_message(
        embed=make_success_embed(
            "Готово",
            "Постоянное сообщение регистрации создано или обновлено."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="set_registration_channel",
    description="Установить канал регистрации",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def set_registration_channel_slash(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    config["registration_channel_id"] = channel.id
    config["registration_message_id"] = 0
    save_config(config)

    await interaction.response.send_message(
        embed=make_success_embed(
            "Настройки обновлены",
            f"Канал регистрации изменён на {channel.mention}.\n"
            "ID сообщения регистрации сброшен. Выполните `/setup_registration`."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="set_log_channel",
    description="Установить лог-канал",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def set_log_channel_slash(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    config["log_channel_id"] = channel.id
    save_config(config)

    await interaction.response.send_message(
        embed=make_success_embed(
            "Настройки обновлены",
            f"Лог-канал изменён на {channel.mention}."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="set_welcome_channel",
    description="Установить welcome-канал",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def set_welcome_channel_slash(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    config["welcome_channel_id"] = channel.id
    save_config(config)

    await interaction.response.send_message(
        embed=make_success_embed(
            "Настройки обновлены",
            f"Welcome-канал изменён на {channel.mention}."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="set_unregistered_role",
    description="Установить роль незарегистрированных",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def set_unregistered_role_slash(
    interaction: discord.Interaction,
    role: discord.Role
):
    config["unregistered_role_id"] = role.id
    save_config(config)

    await interaction.response.send_message(
        embed=make_success_embed(
            "Настройки обновлены",
            f"Роль незарегистрированных изменена на **{role.name}**."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="set_member_role",
    description="Установить роль участников",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def set_member_role_slash(
    interaction: discord.Interaction,
    role: discord.Role
):
    config["member_role_id"] = role.id
    save_config(config)

    await interaction.response.send_message(
        embed=make_success_embed(
            "Настройки обновлены",
            f"Роль участников изменена на **{role.name}**."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="set_rename_cooldown",
    description="Установить задержку между сменами имени в часах",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def set_rename_cooldown_slash(
    interaction: discord.Interaction,
    hours: app_commands.Range[int, 0]
):
    config["rename_cooldown_hours"] = hours
    save_config(config)

    await interaction.response.send_message(
        embed=make_success_embed(
            "Настройки обновлены",
            f"Задержка между сменами имени: **{hours} ч.**"
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="whois",
    description="Показать данные регистрации пользователя",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def whois_slash(
    interaction: discord.Interaction,
    member: discord.Member
):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    registration = get_registration(member.id, interaction.guild.id)
    if not registration:
        await interaction.response.send_message(
            embed=make_warning_embed(
                "Нет данных",
                "Этот пользователь ещё не зарегистрирован в базе."
            ),
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🧾 Информация о пользователе",
        description=f"Данные регистрации для {member.mention}",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Discord username", value=registration["discord_name"], inline=False)
    embed.add_field(name="Указанное имя", value=registration["real_name"], inline=False)
    embed.add_field(name="Текущий ник", value=registration["nickname"], inline=False)
    embed.add_field(name="Первая регистрация", value=format_dt(registration["registered_at"]), inline=False)
    embed.add_field(name="Последнее обновление", value=format_dt(registration["updated_at"]), inline=False)
    embed.set_footer(text="Просмотр данных пользователя")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="registrations_count",
    description="Показать число зарегистрированных пользователей",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def registrations_count_slash(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM registrations
        WHERE guild_id = ?
    """, (interaction.guild.id,))
    row = cur.fetchone()
    conn.close()

    await interaction.response.send_message(
        embed=make_info_embed(
            "Статистика",
            f"Всего зарегистрированных пользователей: **{row['count']}**"
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="db_user",
    description="Посмотреть запись пользователя в базе",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def db_user_slash(
    interaction: discord.Interaction,
    member: discord.Member
):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    registration = get_registration(member.id, interaction.guild.id)
    if not registration:
        await interaction.response.send_message(
            embed=make_warning_embed(
                "Запись не найдена",
                "У этого пользователя нет записи в базе."
            ),
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🗂️ Запись пользователя в базе",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Пользователь", value=f"{member.mention}\n`{member.id}`", inline=False)
    embed.add_field(name="Discord username", value=registration["discord_name"], inline=True)
    embed.add_field(name="Имя", value=registration["real_name"], inline=True)
    embed.add_field(name="Ник", value=registration["nickname"], inline=False)
    embed.add_field(name="Первая регистрация", value=format_dt(registration["registered_at"]), inline=False)
    embed.add_field(name="Последнее обновление", value=format_dt(registration["updated_at"]), inline=False)
    embed.set_footer(text="Database Viewer")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="db_recent",
    description="Показать последние записи в базе",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def db_recent_slash(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 20] = 10
):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    rows = get_last_registrations(interaction.guild.id, limit=limit)
    if not rows:
        await interaction.response.send_message(
            embed=make_warning_embed(
                "База пуста",
                "В базе пока нет зарегистрированных пользователей."
            ),
            ephemeral=True
        )
        return

    lines = []
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"**{idx}.** <@{row['user_id']}>\n"
            f"Имя: **{row['real_name']}**\n"
            f"Ник: `{row['nickname']}`\n"
            f"Обновлено: `{format_dt(row['updated_at'])}`"
        )

    embed = discord.Embed(
        title="📚 Последние регистрации",
        description="\n\n".join(lines),
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Показано записей: {len(rows)}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="db_export",
    description="Выгрузить базу регистраций в CSV",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def db_export_slash(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    file_path = export_registrations_to_csv(interaction.guild.id)
    if file_path is None:
        await interaction.response.send_message(
            embed=make_warning_embed(
                "Нет данных",
                "В базе нет записей для экспорта."
            ),
            ephemeral=True
        )
        return

    await send_log(
        interaction.guild,
        LogType.ADMIN,
        "Экспорт базы",
        f"База регистраций экспортирована в CSV.\nФайл: **{file_path.name}**",
        moderator=interaction.user
    )

    await interaction.response.send_message(
        embed=make_success_embed(
            "Экспорт готов",
            "CSV-файл с регистрациями прикреплён ниже."
        ),
        file=discord.File(file_path),
        ephemeral=True
    )


@bot.tree.command(
    name="db_delete_user",
    description="Удалить пользователя из базы регистраций",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def db_delete_user_slash(
    interaction: discord.Interaction,
    member: discord.Member
):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    registration = get_registration(member.id, interaction.guild.id)
    if not registration:
        await interaction.response.send_message(
            embed=make_warning_embed(
                "Нечего удалять",
                "У этого пользователя нет записи в базе."
            ),
            ephemeral=True
        )
        return

    view = ConfirmAdminActionView(
        requester_id=interaction.user.id,
        target_member_id=member.id,
        action=AdminAction.DELETE_DB_USER
    )

    await interaction.response.send_message(
        embed=make_warning_embed(
            "Подтверждение удаления",
            f"Вы точно хотите удалить **{member}** из базы?\n"
            "Это удалит запись регистрации и историю смен имени."
        ),
        view=view,
        ephemeral=True
    )


@bot.tree.command(
    name="db_reset_user",
    description="Сбросить регистрацию пользователя и вернуть его в незарегистрированные",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def db_reset_user_slash(
    interaction: discord.Interaction,
    member: discord.Member
):
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_error_embed(
                "Неверное место",
                "Эта команда работает только на сервере."
            ),
            ephemeral=True
        )
        return

    view = ConfirmAdminActionView(
        requester_id=interaction.user.id,
        target_member_id=member.id,
        action=AdminAction.RESET_DB_USER
    )

    await interaction.response.send_message(
        embed=make_warning_embed(
            "Подтверждение сброса",
            f"Вы точно хотите сбросить регистрацию пользователя {member.mention}?\n"
            "Будет удалена запись из базы, история имён, ник сбросится, а пользователь вернётся в незарегистрированные."
        ),
        view=view,
        ephemeral=True
    )


# =========================
# Ошибки
# =========================

@setup_registration_slash.error
@set_registration_channel_slash.error
@set_log_channel_slash.error
@set_welcome_channel_slash.error
@set_unregistered_role_slash.error
@set_member_role_slash.error
@set_rename_cooldown_slash.error
@whois_slash.error
@registrations_count_slash.error
@db_user_slash.error
@db_recent_slash.error
@db_export_slash.error
@db_delete_user_slash.error
@db_reset_user_slash.error
async def slash_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.MissingPermissions):
        message_embed = make_error_embed(
            "Нет доступа",
            "У вас нет прав администратора для этой команды."
        )
    else:
        message_embed = make_error_embed(
            "Ошибка",
            str(error)
        )

    if interaction.response.is_done():
        await interaction.followup.send(embed=message_embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=message_embed, ephemeral=True)


if not TOKEN:
    raise ValueError("Не найден DISCORD_TOKEN в .env")

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
