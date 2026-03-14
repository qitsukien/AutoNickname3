from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from . import db
from .config import load_badwords, load_config
from .logging_utils import send_log
from .utils import (
    ServiceResult,
    UserFacingError,
    bot_can_manage_role,
    can_manage_nickname,
    contains_badword,
    is_name_allowed_chars,
    normalize_display_name,
    strip_invisible,
)

log = logging.getLogger(__name__)
_user_locks: dict[int, asyncio.Lock] = {}


def _lock_for_user(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


async def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    cfg = load_config()
    channel_id = int(cfg.get("log_channel_id", 0) or 0)
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def build_member_nickname(member: discord.Member, normalized_name: str) -> str:
    login = strip_invisible(member.name).strip()
    login = login or f"user{member.id}"
    return f"{login} ({normalized_name})"


def validate_name(raw_name: str, member: discord.Member | None = None) -> ServiceResult:
    cfg = load_config()
    name = normalize_display_name(strip_invisible(raw_name))
    if not name:
        return ServiceResult(False, "", "Имя не может быть пустым.")

    min_len = int(cfg.get("name_min_length", 2) or 2)
    max_len = int(cfg.get("name_max_length", 32) or 32)
    if len(name) < min_len:
        return ServiceResult(False, "", f"Имя слишком короткое. Минимум: {min_len}.")
    if len(name) > max_len:
        return ServiceResult(False, "", f"Имя слишком длинное. Максимум: {max_len}.")
    if member is not None:
        target_nick = build_member_nickname(member, name)
        if len(target_nick) > 32:
            return ServiceResult(False, "", "Ник получается слишком длинным вместе с логином. Сделай имя короче.")
    if not is_name_allowed_chars(name):
        return ServiceResult(False, "", "Имя содержит недопустимые символы.")
    if contains_badword(name, load_badwords()):
        return ServiceResult(False, "", "Имя не прошло проверку. Выбери другое имя.")
    return ServiceResult(True, "OK", normalized_name=name)


def _get_roles(member: discord.Member) -> tuple[discord.Role | None, discord.Role | None, discord.Member]:
    cfg = load_config()
    unregistered_role = member.guild.get_role(int(cfg.get("unregistered_role_id", 0) or 0))
    member_role = member.guild.get_role(int(cfg.get("member_role_id", 0) or 0))
    me = member.guild.me
    if me is None:
        raise UserFacingError("Не удалось определить роль бота на сервере.")
    return unregistered_role, member_role, me


async def _check_registration_permissions(member: discord.Member) -> tuple[discord.Role | None, discord.Role, discord.Member]:
    unregistered_role, member_role, me = _get_roles(member)
    if member_role is None:
        raise UserFacingError("Роль участника не найдена в config.json.")
    if not bot_can_manage_role(me, member_role):
        raise UserFacingError("Бот не может выдать роль участника. Проверь позиции ролей.")
    if unregistered_role and not bot_can_manage_role(me, unregistered_role):
        raise UserFacingError("Бот не может снять роль незарегистрированного. Проверь позиции ролей.")
    if not can_manage_nickname(me, member):
        raise UserFacingError("Бот не может изменить ник этому пользователю. Проверь иерархию ролей.")
    return unregistered_role, member_role, me


async def _apply_roles(member: discord.Member) -> tuple[bool, bool, discord.Role | None, discord.Role]:
    unregistered_role, member_role, _ = await _check_registration_permissions(member)
    removed_unreg = False
    added_member = False
    if unregistered_role and unregistered_role in member.roles:
        await member.remove_roles(unregistered_role, reason="Registration completed")
        removed_unreg = True
    if member_role not in member.roles:
        await member.add_roles(member_role, reason="Registration completed")
        added_member = True
    return removed_unreg, added_member, unregistered_role, member_role


async def _rollback_roles(member: discord.Member, removed_unreg: bool, added_member: bool, unregistered_role: discord.Role | None, member_role: discord.Role | None) -> None:
    try:
        if added_member and member_role and member_role in member.roles:
            await member.remove_roles(member_role, reason="Rollback registration")
        if removed_unreg and unregistered_role and unregistered_role not in member.roles:
            await member.add_roles(unregistered_role, reason="Rollback registration")
    except Exception:
        log.exception("Failed to rollback roles for %s", member.id)


def _rename_cooldown_ok(member: discord.Member) -> bool:
    cfg = load_config()
    hours = int(cfg.get("rename_cooldown_hours", 24) or 24)
    if hours <= 0:
        return True
    row = db.get_user(member.id)
    if not row:
        return True
    last = row.get("last_name_change_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) >= last_dt + timedelta(hours=hours)


async def register_member(member: discord.Member, requested_name: str, actor_id: int | None = None) -> ServiceResult:
    async with _lock_for_user(member.id):
        checked = validate_name(requested_name, member)
        if not checked.ok or not checked.normalized_name:
            return checked

        cfg = load_config()
        if not bool(cfg.get("allow_name_change", True)) and db.get_user(member.id):
            return ServiceResult(False, "", "Смена имени временно отключена администратором.")
        if db.get_user(member.id) and not _rename_cooldown_ok(member):
            return ServiceResult(False, "", "Имя недавно уже меняли. Попробуй позже.")

        old_display_name = member.display_name
        target_nickname = build_member_nickname(member, checked.normalized_name)
        removed_unreg = False
        added_member = False
        unregistered_role = None
        member_role = None
        try:
            removed_unreg, added_member, unregistered_role, member_role = await _apply_roles(member)
            await member.edit(nick=target_nickname, reason="User registration")
            db.save_user(member.id, str(member), checked.normalized_name)
            db.add_name_history(member.id, old_display_name, checked.normalized_name, "register", actor_id)
            log_channel = await get_log_channel(member.guild)
            await send_log(
                log_channel,
                title="Регистрация",
                description=(
                    f"Пользователь: {member.mention}\n"
                    f"ID: `{member.id}`\n"
                    f"Имя: **{checked.normalized_name}**\n"
                    f"Ник в Discord: **{target_nickname}**"
                ),
                color=0x57F287,
            )
            return ServiceResult(True, "Регистрация завершена.", normalized_name=checked.normalized_name)
        except UserFacingError as e:
            return ServiceResult(False, "", e.public_message)
        except discord.Forbidden:
            await _rollback_roles(member, removed_unreg, added_member, unregistered_role, member_role)
            return ServiceResult(False, "", "У бота не хватает прав для завершения регистрации.")
        except discord.HTTPException:
            await _rollback_roles(member, removed_unreg, added_member, unregistered_role, member_role)
            return ServiceResult(False, "", "Discord временно отклонил операцию. Попробуй ещё раз.")
        except Exception:
            await _rollback_roles(member, removed_unreg, added_member, unregistered_role, member_role)
            log.exception("Register member failed for %s", member.id)
            return ServiceResult(False, "", "Не удалось завершить регистрацию.")


async def restore_member(member: discord.Member, actor_id: int | None = None) -> ServiceResult:
    async with _lock_for_user(member.id):
        row = db.get_user(member.id)
        if not row:
            return ServiceResult(False, "", "Пользователь не найден в базе.")
        target_name = row["normalized_name"]
        target_nickname = build_member_nickname(member, target_name)
        try:
            await _apply_roles(member)
            await member.edit(nick=target_nickname, reason="Restore from database")
            db.increment_restore_count(member.id)
            db.add_name_history(member.id, member.display_name, target_name, "restore", actor_id)
            log_channel = await get_log_channel(member.guild)
            await send_log(
                log_channel,
                title="Восстановление из БД",
                description=(
                    f"Пользователь: {member.mention}\n"
                    f"ID: `{member.id}`\n"
                    f"Имя: **{target_name}**\n"
                    f"Ник в Discord: **{target_nickname}**"
                ),
                color=0xFEE75C,
            )
            return ServiceResult(True, "Восстановление завершено.", normalized_name=target_name)
        except UserFacingError as e:
            return ServiceResult(False, "", e.public_message)
        except discord.Forbidden:
            return ServiceResult(False, "", "У бота не хватает прав для восстановления пользователя.")
        except discord.HTTPException:
            return ServiceResult(False, "", "Discord временно отклонил операцию. Попробуй ещё раз.")
        except Exception:
            log.exception("Restore member failed for %s", member.id)
            return ServiceResult(False, "", "Не удалось восстановить пользователя.")


async def send_welcome(member: discord.Member) -> None:
    cfg = load_config()
    channel = member.guild.get_channel(int(cfg.get("welcome_channel_id", 0) or 0))
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(f"Добро пожаловать, {member.mention}!")
        except Exception:
            pass
