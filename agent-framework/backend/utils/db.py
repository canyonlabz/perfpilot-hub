"""Async PostgreSQL connection pool for the `perfagent_state` database.

Provides one shared `asyncpg` pool per agent process. Used by the runtime
data-access modules (`session_store.py`, future `task_store.py`,
`checkpointer.py`, etc.). DDL provisioning lives in `agent-framework/sql/`
and uses the synchronous `psycopg2` driver instead - this module is for the
hot path only.

Design choices:

- **Lazy initialization.** The pool is created on first call to `get_pool()`
  so importing this module does not require a running database (matters for
  smoke tests and IDE indexing).
- **Singleton-per-event-loop.** A single pool object is reused for the
  lifetime of the process. Concurrent `get_pool()` calls are guarded by an
  `asyncio.Lock` so we do not race-create two pools.
- **Environment-driven config.** Connection settings come from the
  PERFAGENT_STATE_* variables in `agent-framework/.env`. No hardcoded
  hostnames or credentials.
- **Clean shutdown.** `close_pool()` releases all connections and resets the
  module-level state so the next `get_pool()` re-creates the pool. Called
  during agent process shutdown.

Heavy imports (`asyncpg`) are deferred into the function that needs them so
this module can be imported in environments without asyncpg installed
(structural smoke tests, IDE indexing, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_MIN_SIZE = 2
DEFAULT_MAX_SIZE = 10

# Module-level cached pool. Reset by close_pool().
_pool: Optional[Any] = None
_pool_lock: Optional[asyncio.Lock] = None


@dataclass(frozen=True)
class DbSettings:
    """Connection settings for the `perfagent_state` pool."""

    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str = "prefer"
    sslrootcert: Optional[str] = None
    min_size: int = DEFAULT_MIN_SIZE
    max_size: int = DEFAULT_MAX_SIZE


def load_settings_from_env(env_file: Optional[Path] = None) -> DbSettings:
    """Build `DbSettings` from `PERFAGENT_STATE_*` environment variables.

    Optionally loads `agent-framework/.env` first so this works whether the
    caller already loaded the file or not. Existing env vars are not
    overridden by the .env file (`override=False`).

    Args:
        env_file: Explicit path to a `.env` file. Defaults to
            `agent-framework/.env` resolved from this module's location.

    Returns:
        Frozen `DbSettings` instance.

    Raises:
        RuntimeError: If a required PERFAGENT_STATE_* variable is missing.
    """
    if env_file is None:
        env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except ImportError:
            log.debug("python-dotenv not installed; skipping .env load")

    required = ("PERFAGENT_STATE_HOST", "PERFAGENT_STATE_PORT",
                "PERFAGENT_STATE_DB", "PERFAGENT_STATE_USER",
                "PERFAGENT_STATE_PASSWORD")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "Missing required perfagent_state env vars: " + ", ".join(missing)
        )

    sslrootcert = os.environ.get("PERFAGENT_STATE_SSLROOTCERT", "").strip() or None

    return DbSettings(
        host=os.environ["PERFAGENT_STATE_HOST"],
        port=int(os.environ["PERFAGENT_STATE_PORT"]),
        database=os.environ["PERFAGENT_STATE_DB"],
        user=os.environ["PERFAGENT_STATE_USER"],
        password=os.environ["PERFAGENT_STATE_PASSWORD"],
        sslmode=os.environ.get("PERFAGENT_STATE_SSLMODE", "prefer"),
        sslrootcert=sslrootcert,
    )


def _build_dsn(settings: DbSettings) -> str:
    """Translate `DbSettings` into a libpq-style connection URI for asyncpg."""
    parts = [
        f"postgresql://{settings.user}:{settings.password}",
        f"@{settings.host}:{settings.port}/{settings.database}",
    ]
    query = []
    if settings.sslmode:
        query.append(f"sslmode={settings.sslmode}")
    if settings.sslrootcert:
        query.append(f"sslrootcert={settings.sslrootcert}")
    if query:
        parts.append("?" + "&".join(query))
    return "".join(parts)


async def get_pool(settings: Optional[DbSettings] = None) -> Any:
    """Return the singleton asyncpg pool, creating it on first call.

    Args:
        settings: Optional override. If omitted, settings are loaded from the
            environment via `load_settings_from_env()`.

    Returns:
        An `asyncpg.Pool` ready for use.
    """
    global _pool, _pool_lock

    if _pool is not None:
        return _pool

    if _pool_lock is None:
        _pool_lock = asyncio.Lock()

    async with _pool_lock:
        if _pool is not None:
            return _pool

        if settings is None:
            settings = load_settings_from_env()

        # Heavy import deferred to avoid requiring asyncpg at module load time.
        import asyncpg

        log.info(
            "Creating asyncpg pool for %s@%s:%s/%s (min=%d, max=%d)",
            settings.user,
            settings.host,
            settings.port,
            settings.database,
            settings.min_size,
            settings.max_size,
        )
        _pool = await asyncpg.create_pool(
            dsn=_build_dsn(settings),
            min_size=settings.min_size,
            max_size=settings.max_size,
        )
        return _pool


async def close_pool() -> None:
    """Close the cached pool and reset the module-level state.

    Safe to call multiple times. Called during agent process shutdown.
    """
    global _pool
    if _pool is None:
        return
    log.info("Closing asyncpg pool for perfagent_state")
    try:
        await _pool.close()
    except Exception:
        log.warning("Error closing asyncpg pool", exc_info=True)
    finally:
        _pool = None


async def health_check(settings: Optional[DbSettings] = None) -> bool:
    """Round-trip a `SELECT 1` against the pool. Returns True on success."""
    pool = await get_pool(settings)
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval("SELECT 1")
            return value == 1
    except Exception:
        log.exception("perfagent_state health check failed")
        return False
