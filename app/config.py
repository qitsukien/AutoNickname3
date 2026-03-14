from __future__ import annotations

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


def load_config() -> dict[str, Any]:
    cfg = load_json(CONFIG_PATH, {})
    if not isinstance(cfg, dict):
        raise ValueError("config.json должен содержать JSON-объект")
    cfg.setdefault("guild_id", 0)
    cfg.setdefault("registration_channel_id", 0)
    cfg.setdefault("log_channel_id", 0)
    cfg.setdefault("welcome_channel_id", 0)
    cfg.setdefault("unregistered_role_id", 0)
    cfg.setdefault("member_role_id", 0)
    cfg.setdefault("rename_cooldown_hours", 24)
    cfg.setdefault("registration_message_id", 0)
    cfg.setdefault("backup_interval_hours", 24)
    cfg.setdefault("backup_max_files", cfg.get("max_backups", 5))
    cfg.setdefault("last_auto_backup_at", cfg.get("last_backup_at"))
    cfg.setdefault("name_min_length", 2)
    cfg.setdefault("name_max_length", 32)
    cfg.setdefault("auto_restore_on_join", True)
    cfg.setdefault("allow_restore_button", True)
    cfg.setdefault("allow_name_change", True)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    cfg = dict(cfg)
    if "max_backups" in cfg and "backup_max_files" not in cfg:
        cfg["backup_max_files"] = cfg["max_backups"]
    if "last_backup_at" in cfg and "last_auto_backup_at" not in cfg:
        cfg["last_auto_backup_at"] = cfg["last_backup_at"]
    save_json(CONFIG_PATH, cfg)


def load_badwords() -> list[str]:
    data = load_json(BADWORDS_PATH, [])
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]
