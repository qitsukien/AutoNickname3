from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from discord.ext import tasks

from .config import BACKUPS_DIR, BADWORDS_PATH, CONFIG_PATH, DB_PATH, ensure_directories, load_config, save_config

log = logging.getLogger(__name__)


class BackupManager:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.loop.start()

    def cog_unload(self) -> None:
        self.loop.cancel()

    def should_run_backup(self) -> bool:
        cfg = load_config()
        hours = int(cfg.get("backup_interval_hours", 24) or 24)
        last_backup = cfg.get("last_auto_backup_at")
        if not last_backup:
            return True
        try:
            last_dt = datetime.fromisoformat(last_backup)
        except ValueError:
            return True
        return datetime.now(timezone.utc) >= last_dt + timedelta(hours=hours)

    def create_backup(self) -> Path:
        ensure_directories()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        temp_dir = BACKUPS_DIR / f"backup_{timestamp}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            for file_path in (DB_PATH, CONFIG_PATH, BADWORDS_PATH):
                if file_path.exists():
                    shutil.copy2(file_path, temp_dir / file_path.name)
            zip_base = BACKUPS_DIR / f"backup_{timestamp}"
            archive = shutil.make_archive(str(zip_base), "zip", temp_dir)
            zip_path = Path(archive)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        self.cleanup_old_backups()
        cfg = load_config()
        cfg["last_auto_backup_at"] = datetime.now(timezone.utc).isoformat()
        save_config(cfg)
        return zip_path

    def cleanup_old_backups(self) -> None:
        max_backups = int(load_config().get("backup_max_files", 5) or 5)
        backups = sorted(BACKUPS_DIR.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[max_backups:]:
            old.unlink(missing_ok=True)

    @tasks.loop(minutes=5)
    async def loop(self) -> None:
        try:
            if self.should_run_backup():
                self.create_backup()
        except Exception:
            log.exception("Auto backup failed")

    @loop.before_loop
    async def before_loop(self) -> None:
        await self.bot.wait_until_ready()
