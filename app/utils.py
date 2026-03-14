from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import discord

CYR_EQUIV = str.maketrans({
    "a": "а", "b": "в", "c": "с", "e": "е", "k": "к", "m": "м",
    "h": "н", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
    "i": "і", "j": "ј",
})


@dataclass(slots=True)
class ServiceResult:
    ok: bool
    message: str
    public_error: str | None = None
    normalized_name: str | None = None
    final_nickname: str | None = None


class UserFacingError(Exception):
    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_invisible(text: str) -> str:
    banned_categories = {"Cf", "Cc", "Cs"}
    return "".join(ch for ch in text if unicodedata.category(ch) not in banned_categories)


def normalize_display_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", strip_invisible(name))
    name = collapse_spaces(name)
    name = name.replace("`", "").replace('"', "").replace("'", "'")
    name = re.sub(r"[‐‑‒–—―]+", "-", name)
    name = re.sub(r"\s*-\s*", "-", name)
    if not name:
        return ""
    words: list[str] = []
    for part in name.split(" "):
        hy_chunks = []
        for chunk in part.split("-"):
            if not chunk:
                hy_chunks.append(chunk)
                continue
            if chunk.isupper() and len(chunk) <= 3:
                hy_chunks.append(chunk)
            else:
                hy_chunks.append(chunk[:1].upper() + chunk[1:].lower())
        words.append("-".join(hy_chunks))
    return " ".join(words)


def simplify_for_badword_check(text: str) -> str:
    text = strip_invisible(text)
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("ё", "е")
    text = text.translate(CYR_EQUIV)
    replacements = {
        "0": "о", "3": "з", "4": "а", "6": "б", "@": "а", "$": "с", "1": "i",
        "!": "i", "7": "т", "9": "д",
    }
    text = "".join(replacements.get(ch, ch) for ch in text)
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


def regex_blacklisted(text: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        try:
            if re.fullmatch(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def is_name_allowed_by_regex(name: str, whitelist_regex: str) -> bool:
    try:
        return bool(re.fullmatch(whitelist_regex, name))
    except re.error:
        return False


def render_nickname(template: str, *, login: str, name: str, display_name: str) -> str:
    try:
        rendered = template.format(login=login, name=name, display_name=display_name)
    except Exception:
        rendered = "{login} ({name})".format(login=login, name=name)
    return collapse_spaces(rendered).strip()


def is_admin(member: discord.abc.User) -> bool:
    return isinstance(member, discord.Member) and member.guild_permissions.administrator


def require_admin_or_raise(interaction: discord.Interaction) -> None:
    if not is_admin(interaction.user):
        raise UserFacingError("Это действие доступно только администраторам.")


def bot_can_manage_role(bot_member: discord.Member, role: discord.Role) -> bool:
    return bot_member.guild_permissions.manage_roles and bot_member.top_role > role


def can_manage_nickname(bot_member: discord.Member, target: discord.Member) -> bool:
    return bot_member.guild_permissions.manage_nicknames and bot_member.top_role > target.top_role
