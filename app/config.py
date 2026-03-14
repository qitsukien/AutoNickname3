from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "registrations.db"
BADWORDS_PATH = BASE_DIR / "badwords.json"
BACKUPS_DIR = BASE_DIR / "backups"
EXPORTS_DIR = BASE_DIR / "exports"
TOKEN = os.getenv("DISCORD_TOKEN", "")


def ensure_directories() -> None:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def default_config() -> dict[str, Any]:
    return {
        "guild_id": 0,
        "registration_channel_id": 0,
        "log_channel_id": 0,
        "welcome_channel_id": 0,
        "unregistered_role_id": 0,
        "member_role_id": 0,
        "rename_cooldown_hours": 24,
        "registration_message_id": 0,
        "backup_interval_hours": 24,
        "backup_max_files": 5,
        "last_auto_backup_at": None,
        "allow_name_change": True,
        "allow_restore_button": False,
        "auto_restore_on_join": True,
        "name_min_length": 2,
        "name_max_length": 20,
        "nickname_format": "{login} ({name})",
        "name_whitelist_regex": r"^[A-Za-zА-Яа-яЁё0-9\- ']+$",
        "name_blacklist_regex_list": [],
        "panel_title": "Регистрация участников",
        "panel_description": "Нажми кнопку ниже, чтобы зарегистрироваться.",
        "panel_help_text": "Если возникла ошибка — обратись к администратору.",
        "last_panel_refresh_at": None,
        "last_panel_message_hash": None,
    }


def load_config() -> dict[str, Any]:
    cfg = default_config()
    raw = load_json(CONFIG_PATH, {})
    if not isinstance(raw, dict):
        raise ValueError("config.json должен содержать JSON-объект")
    cfg.update(raw)
    if "max_backups" in raw and "backup_max_files" not in raw:
        cfg["backup_max_files"] = raw["max_backups"]
    if "last_backup_at" in raw and not cfg.get("last_auto_backup_at"):
        cfg["last_auto_backup_at"] = raw["last_backup_at"]
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    save_json(CONFIG_PATH, cfg)


def load_badwords() -> list[str]:
    data = load_json(BADWORDS_PATH, [])
    return [str(x).strip() for x in data if str(x).strip()] if isinstance(data, list) else []


def panel_hash(title: str, description: str, fmt: str) -> str:
    payload = f"{title}\n{description}\n{fmt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
