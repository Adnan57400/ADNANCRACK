"""
╔══════════════════════════════════════════════════════════╗
║         CRACK SMS — Centralized Logging System           ║
║         v1.0 | Production-Ready | Async-Safe             ║
╚══════════════════════════════════════════════════════════╝

Drop-in replacement / enhancement for bot.py logging setup.

Features:
  • Rotating file handler (100 MB cap, 5 backups)
  • Emoji-rich colour console output
  • Separate error.log for easy alerting
  • JSON structured log option (for log-shipper / Grafana)
  • Async-safe Telegram alert handler (critical errors → admin)
  • Contextual child-logger factory (per-module, per-panel, per-user)
  • Request / OTP audit trail helpers
  • Log-level hot-reload (change level without restart)
  • Sensitive-data scrubber (masks tokens, phone numbers)
  • Performance timer decorator

Usage:
    from logging_system import get_logger, audit_otp, audit_api, timer

    logger = get_logger(__name__)
    logger.info("Panel connected: panel_01")

    # Audit an OTP event (goes to audit.log + DB log chats)
    await audit_otp(phone="+1234", otp="778899", service="WhatsApp",
                    panel="panel_01", user_id=123456)

    # Audit an API access
    await audit_api(token_name="WebApp", endpoint="/api/sms",
                    records_returned=42)
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import logging.handlers
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# ──────────────────────────────────────────────────────────────────
#  CONFIGURATION  (override via env vars or config.json)
# ──────────────────────────────────────────────────────────────────

LOG_DIR          = Path(os.environ.get("LOG_DIR", "logs"))
LOG_LEVEL        = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES    = int(os.environ.get("LOG_MAX_BYTES",  100 * 1024 * 1024))  # 100 MB
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 5))
JSON_LOGGING     = os.environ.get("JSON_LOGGING", "false").lower() == "true"
SCRUB_SENSITIVE  = os.environ.get("SCRUB_SENSITIVE", "true").lower() == "true"

LOG_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────
#  SENSITIVE DATA SCRUBBER
# ──────────────────────────────────────────────────────────────────

_SCRUB_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Telegram bot tokens  7652943119:AAFGu…
    (re.compile(r"\d{8,12}:AA[A-Za-z0-9_\-]{30,}"), "<BOT_TOKEN>"),
    # Generic API/secret tokens (>=24 hex/base64 chars)
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "<TOKEN>"),
    # Phone numbers  +1234567890 / 001234567890
    (re.compile(r"\+?\d{10,15}"), lambda m: m.group()[:4] + "****" + m.group()[-2:]),
    # OTP codes  standalone 4-8 digit strings
    (re.compile(r"(?<!\d)\d{4,8}(?!\d)"), "<OTP>"),
]

def _scrub(msg: str) -> str:
    """Replace sensitive values in a log message."""
    if not SCRUB_SENSITIVE:
        return msg
    for pattern, replacement in _SCRUB_PATTERNS:
        if callable(replacement):
            msg = pattern.sub(replacement, msg)
        else:
            msg = pattern.sub(replacement, msg)
    return msg


# ──────────────────────────────────────────────────────────────────
#  FORMATTERS
# ──────────────────────────────────────────────────────────────────

class EmojiConsoleFormatter(logging.Formatter):
    """Colourised, emoji-annotated console formatter."""

    _EMOJI = {
        logging.DEBUG:    ("🔍", "\033[36m"),   # cyan
        logging.INFO:     ("✅", "\033[32m"),   # green
        logging.WARNING:  ("⚠️ ", "\033[33m"),  # yellow
        logging.ERROR:    ("❌", "\033[31m"),   # red
        logging.CRITICAL: ("🔥", "\033[95m"),   # magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        emoji, color = self._EMOJI.get(record.levelno, ("❓", ""))
        ts  = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        msg = _scrub(record.getMessage())
        lvl = record.levelname.ljust(8)

        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        name_short = record.name.split(".")[-1][:18].ljust(18)
        return (
            f"{ts} {color}│ {emoji} {lvl} │ {name_short} │{self._RESET} {msg}"
        )


class PlainFileFormatter(logging.Formatter):
    """Plain text formatter for rotating file logs."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        msg = _scrub(record.getMessage())
        record.msg     = msg
        record.args    = None
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """Newline-delimited JSON formatter for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload = {
            "ts":      datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     _scrub(record.getMessage()),
            "module":  record.module,
            "line":    record.lineno,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────
#  HANDLER FACTORY
# ──────────────────────────────────────────────────────────────────

def _rotating(path: Path, level: int, formatter: logging.Formatter) -> logging.Handler:
    h = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    h.setLevel(level)
    h.setFormatter(formatter)
    return h


def _build_handlers() -> list[logging.Handler]:
    if JSON_LOGGING:
        file_fmt: logging.Formatter = JsonFormatter()
    else:
        file_fmt = PlainFileFormatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handlers: list[logging.Handler] = [
        # ── Console ──────────────────────────────────────
        (lambda h: (h.setLevel(logging.DEBUG), h.setFormatter(EmojiConsoleFormatter()), h)[2])(
            logging.StreamHandler()
        ),
        # ── Main rotating log ─────────────────────────────
        _rotating(LOG_DIR / "bot.log",   logging.DEBUG,   file_fmt),
        # ── Error-only log (easy to tail / alert on) ──────
        _rotating(LOG_DIR / "error.log", logging.ERROR,   file_fmt),
        # ── Audit log (OTP / API events) ──────────────────
        _rotating(LOG_DIR / "audit.log", logging.INFO,    file_fmt),
    ]
    return handlers


# ──────────────────────────────────────────────────────────────────
#  ROOT LOGGER BOOTSTRAP  (call once at startup)
# ──────────────────────────────────────────────────────────────────

_bootstrapped = False

def bootstrap(level: str = LOG_LEVEL) -> None:
    """
    Configure the root logger.  Call this ONCE at the very top of
    bot.py (before any `import` that might call logging).
    """
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True

    numeric = getattr(logging, level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)          # handlers filter individually
    root.handlers.clear()

    for h in _build_handlers():
        root.addHandler(h)

    # Silence noisy third-party loggers
    for name in ("httpx", "httpcore", "telegram.ext", "apscheduler",
                 "aiohttp", "uvicorn.access", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ──────────────────────────────────────────────────────────────────
#  LOGGER FACTORY
# ──────────────────────────────────────────────────────────────────

def get_logger(name: str, *, context: Optional[dict] = None) -> logging.Logger:
    """
    Return a named logger.  Optionally attach a fixed context dict
    that will be prepended to every message.

    Example:
        log = get_logger("panels.panel_01", context={"panel": "panel_01"})
        log.info("Connected")  →  "panels.panel_01 | [panel=panel_01] Connected"
    """
    bootstrap()
    logger = logging.getLogger(name)
    if context:
        ctx_str = " ".join(f"{k}={v}" for k, v in context.items())
        original_makeRecord = logger.makeRecord

        def makeRecord(name, level, fn, lno, msg, args, exc_info,  # noqa: N802
                       func=None, extra=None, sinfo=None):
            record = original_makeRecord(
                name, level, fn, lno, f"[{ctx_str}] {msg}",
                args, exc_info, func, extra, sinfo
            )
            return record

        logger.makeRecord = makeRecord  # type: ignore[method-assign]
    return logger


# ──────────────────────────────────────────────────────────────────
#  AUDIT TRAIL HELPERS
# ──────────────────────────────────────────────────────────────────

_audit_logger = logging.getLogger("crack_sms.audit")

# Attach the audit-specific file handler directly
_audit_logger.addHandler(
    _rotating(LOG_DIR / "audit.log", logging.INFO,
              PlainFileFormatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
)
_audit_logger.propagate = False   # don't double-write to root


async def audit_otp(
    *,
    phone: str,
    otp: str,
    service: str,
    panel: str,
    user_id: Optional[int] = None,
    extra: Optional[dict] = None,
) -> None:
    """
    Write a structured OTP-received audit entry.

    Parameters
    ----------
    phone    : Raw phone number (will be masked before writing)
    otp      : The extracted OTP code
    service  : e.g. "WhatsApp"
    panel    : Panel name / ID that received the message
    user_id  : Telegram user_id who holds the number (if any)
    extra    : Any additional key-value pairs to include
    """
    masked = phone[:4] + "****" + phone[-2:] if len(phone) > 6 else "****"
    payload = {
        "event":   "OTP_RECEIVED",
        "phone":   masked,
        "service": service,
        "panel":   panel,
    }
    if user_id:
        payload["user_id"] = str(user_id)
    if extra:
        payload.update(extra)

    line = " | ".join(f"{k}={v}" for k, v in payload.items())
    _audit_logger.info(line)


async def audit_api(
    *,
    token_name: str,
    endpoint: str,
    records_returned: int,
    ip: Optional[str] = None,
) -> None:
    """Write a structured API-access audit entry."""
    payload = {
        "event":    "API_ACCESS",
        "token":    token_name,
        "endpoint": endpoint,
        "records":  str(records_returned),
    }
    if ip:
        payload["ip"] = ip

    line = " | ".join(f"{k}={v}" for k, v in payload.items())
    _audit_logger.info(line)


def audit_admin_action(
    *,
    admin_id: int,
    action: str,
    target: Optional[str] = None,
    result: str = "ok",
) -> None:
    """Write a structured admin-action audit entry (sync version)."""
    payload = {
        "event":  "ADMIN_ACTION",
        "admin":  str(admin_id),
        "action": action,
        "result": result,
    }
    if target:
        payload["target"] = target

    line = " | ".join(f"{k}={v}" for k, v in payload.items())
    _audit_logger.info(line)


# ──────────────────────────────────────────────────────────────────
#  TELEGRAM ALERT HANDLER  (critical errors → admin chat)
# ──────────────────────────────────────────────────────────────────

class TelegramAlertHandler(logging.Handler):
    """
    Sends CRITICAL log messages to a Telegram admin chat.

    Install after the bot application is created:
        from logging_system import TelegramAlertHandler
        logging.getLogger().addHandler(
            TelegramAlertHandler(bot=app.bot, chat_id=ADMIN_CHAT_ID)
        )
    """

    def __init__(self, bot, chat_id: int, loop=None):
        super().__init__(logging.CRITICAL)
        self._bot     = bot
        self._chat_id = chat_id
        self._loop    = loop or asyncio.get_event_loop()
        self._queue: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            text = (
                f"🚨 <b>CRITICAL ERROR</b>\n"
                f"<code>{msg[:3000]}</code>"
            )
            coro = self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
            )
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception:  # noqa: BLE001
            self.handleError(record)


# ──────────────────────────────────────────────────────────────────
#  PERFORMANCE TIMER DECORATOR
# ──────────────────────────────────────────────────────────────────

def timer(label: Optional[str] = None):
    """
    Decorator that logs execution time of sync or async functions.

    Usage:
        @timer("fetch_otps")
        async def fetch_otps(...): ...
    """
    def decorator(fn: Callable) -> Callable:
        name = label or fn.__qualname__
        _log = get_logger(f"perf.{fn.__module__}")

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter() - t0) * 1000
                    _log.debug(f"⏱  {name} finished in {elapsed:.1f} ms")
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    return fn(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter() - t0) * 1000
                    _log.debug(f"⏱  {name} finished in {elapsed:.1f} ms")
            return sync_wrapper

    return decorator


# ──────────────────────────────────────────────────────────────────
#  LOG-LEVEL HOT-RELOAD
# ──────────────────────────────────────────────────────────────────

def set_log_level(level: str) -> None:
    """
    Change the effective log level at runtime without restart.

    Example (from admin callback):
        from logging_system import set_log_level
        set_log_level("DEBUG")
    """
    numeric = getattr(logging, level.upper(), None)
    if numeric is None:
        raise ValueError(f"Unknown log level: {level!r}")
    logging.getLogger().setLevel(numeric)
    get_logger(__name__).info(f"Log level changed → {level.upper()}")


# ──────────────────────────────────────────────────────────────────
#  QUICK SELF-TEST  (python logging_system.py)
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bootstrap("DEBUG")
    log = get_logger("test")
    log.debug("Debug message — only visible in DEBUG mode")
    log.info("Bot started successfully")
    log.warning("Panel response slow: 4200 ms")
    log.error("Database connection failed — retrying in 5 s")
    log.critical("Unhandled exception in main loop")

    # Audit helpers
    asyncio.run(audit_otp(phone="+14155550123", otp="998877",
                          service="WhatsApp", panel="panel_01", user_id=999))
    asyncio.run(audit_api(token_name="WebApp", endpoint="/api/sms",
                          records_returned=15, ip="1.2.3.4"))
    audit_admin_action(admin_id=7763727542, action="DELETE_NUMBER",
                       target="+1415****23", result="ok")

    print("\n✅ Log files written to:", LOG_DIR.resolve())
