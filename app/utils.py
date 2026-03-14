from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import discord

LATIN_TO_CYR = str.maketrans({
    "a": "а", "b": "в", "c": "с", "e": "е", "k": "к", "m": "м",
    "h": "н", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
})


@dataclass(slots=True)
class ServiceResult:
    ok: bool
    message: str
    public_error: str | None = None
    normalized_name: str | None = None


class UserFacingError(Exception):
    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) not in {"Cf", "Cc"})


def normalize_display_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = collapse_spaces(strip_invisible(name))
    if not name:
        return name
    words: list[str] = []
    for part in name.split(" "):
        chunks = part.split("-")
        fixed = []
        for chunk in chunks:
            if not chunk:
                fixed.append(chunk)
                continue
            fixed.append(chunk[:1].upper() + chunk[1:].lower())
        words.append("-".join(fixed))
    return " ".join(words)


def simplify_for_badword_check(text: str) -> str:
    text = strip_invisible(text)
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("ё", "е")
    text = text.translate(LATIN_TO_CYR)
    text = re.sub(r"[^a-zа-я0-9]+", "", text)
    text = re.sub(r"(.)\1{1,}", r"\1", text)
    return text


def contains_badword(text: str, badwords: Iterable[str]) -> bool:
    simplified = simplify_for_badword_check(text)
    for word in badwords:
        current = simplify_for_badword_check(word)
        if current and current in simplified:
            return True
    return False


def is_name_allowed_chars(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9\- ']+", name))


def is_admin(member: discord.abc.User) -> bool:
    return isinstance(member, discord.Member) and member.guild_permissions.administrator


def require_admin_or_raise(interaction: discord.Interaction) -> None:
    if not is_admin(interaction.user):
        raise UserFacingError("Это действие доступно только администраторам.")


def bot_can_manage_role(bot_member: discord.Member, role: discord.Role) -> bool:
    return bot_member.guild_permissions.manage_roles and bot_member.top_role > role


def can_manage_nickname(bot_member: discord.Member, target: discord.Member) -> bool:
    return bot_member.guild_permissions.manage_nicknames and bot_member.top_role > target.top_role
