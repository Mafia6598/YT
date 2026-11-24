from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import event
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

log = logging.getLogger(__name__)
_engine = None


def engine():
    global _engine
    if _engine is None:
        url = get_settings().db_url
        _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragma(conn, _):  # WAL = safe concurrent reads while the worker writes
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
    return _engine


def init_db() -> None:
    from . import models  # noqa: F401
    SQLModel.metadata.create_all(engine())


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine(), expire_on_commit=False) as s:
        yield s


def get_session() -> Iterator[Session]:
    with Session(engine(), expire_on_commit=False) as s:
        yield s


def touch(obj: Any, *fields: str) -> None:
    """Mark JSON columns as modified so in-place dict edits are persisted."""
    for f in fields:
        flag_modified(obj, f)


def kv_get(key: str, default: dict | None = None) -> dict:
    from .models import KV
    with session() as s:
        row = s.get(KV, key)
        return dict(row.value) if row else dict(default or {})


def kv_set(key: str, value: dict) -> None:
    from .models import KV
    with session() as s:
        row = s.get(KV, key) or KV(key=key)
        row.value = value
        touch(row, "value")
        s.add(row)
        s.commit()
