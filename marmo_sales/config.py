"""الإعدادات: الأسرار من متغيرات البيئة فقط."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(__file__).parent / "data"


def _ids(raw: str) -> frozenset:
    return frozenset(x.strip() for x in (raw or "").split(",") if x.strip().isdigit())


def _int(raw: str, default):
    raw = (raw or "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else default


@dataclass(frozen=True)
class Settings:
    tg_token: str
    dry_run: bool                  # تجربة بلا اتصال بتيليجرام
    db_path: str
    tz: ZoneInfo
    port: int                      # منفذ استقبال الطلبات من بوت العميل (شبكة داخلية فقط)
    manager_ids: frozenset         # معرّفات تيليجرام للمديرين
    ingest_token: str              # رمز استقبال الطلبات من بوت العميل
    assign_mode: str               # balanced (الأقل حملاً) أو manual
    brief_hour: int
    work_days: tuple               # بترقيم بايثون: الاثنين 0 ... الأحد 6
    alert_uncontacted_hours: int | None
    stock_stale_hours: int
    stock_source: str              # file أو odoo
    protect_content: bool          # يمنع إعادة توجيه الرسائل وحفظها
    odoo_url: str
    odoo_db: str
    odoo_login: str
    odoo_api_key: str
    odoo_timeout: int = 15

    @property
    def odoo_enabled(self) -> bool:
        return all((self.odoo_url, self.odoo_db, self.odoo_login, self.odoo_api_key))

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        import os
        e = os.environ if env is None else env
        g = lambda k, d="": (e.get(k, d) or "").strip()
        days = tuple(int(x) for x in g("WORK_DAYS", "6,0,1,2,3").split(",") if x.strip().isdigit())
        return cls(
            tg_token=g("TELEGRAM_TOKEN"), dry_run=g("DRY_RUN").lower() in ("1", "true", "yes"),
            db_path=g("DB_PATH", "/data/sales.db"), tz=ZoneInfo(g("TIMEZONE", "Asia/Riyadh")),
            port=_int(g("PORT"), 8080), manager_ids=_ids(g("MANAGER_IDS")),
            ingest_token=g("INGEST_TOKEN"), assign_mode=g("ASSIGN_MODE", "balanced"),
            brief_hour=_int(g("BRIEF_HOUR"), 8), work_days=days,
            alert_uncontacted_hours=_int(g("ALERT_UNCONTACTED_HOURS"), None),
            stock_stale_hours=_int(g("STOCK_STALE_HOURS"), 48), stock_source=g("STOCK_SOURCE", "file"),
            protect_content=g("PROTECT_CONTENT", "1").lower() in ("1", "true", "yes"),
            odoo_url=g("ODOO_URL").rstrip("/"), odoo_db=g("ODOO_DB"),
            odoo_login=g("ODOO_LOGIN"), odoo_api_key=g("ODOO_API_KEY"),
        )

    def validate_for_serving(self):
        missing = []
        if not self.dry_run and not self.tg_token:
            missing.append("TELEGRAM_TOKEN")
        if missing:
            raise SystemExit(f"متغيرات ناقصة: {', '.join(missing)}. اضبطها بالأمر fly secrets set")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
