"""Recover the last removals: a small per-kind stack of row snapshots.

Before an application (with its events, assessments, interviews and deadlines) or a
deadline record is deleted, its rows are copied here. ``restore_last`` re-inserts the
most recent snapshot with the same ids, so links and history come back unchanged.
Snapshots live in the local ``settings`` table; nothing leaves the machine.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from ..models import ActionItem, Application, ApplicationEvent, Assessment, Interview
from . import settings_service

KINDS = ("application", "deadline")
MAX_ENTRIES = 20
# Insert order respects foreign keys (parents first).
_ORDER: list[Table] = [
    Application.__table__,
    ApplicationEvent.__table__,
    Assessment.__table__,
    Interview.__table__,
    ActionItem.__table__,
]


class TrashError(Exception):
    pass


def _key(kind: str) -> str:
    if kind not in KINDS:
        raise TrashError(f"unknown kind {kind!r}")
    return f"trash:{kind}"


def _encode(v: Any) -> Any:
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, date):
        return {"__date__": v.isoformat()}
    return v


def _decode(v: Any) -> Any:
    if isinstance(v, dict) and set(v) == {"__dt__"}:
        return datetime.fromisoformat(v["__dt__"])
    if isinstance(v, dict) and set(v) == {"__date__"}:
        return date.fromisoformat(v["__date__"])
    return v


def _rows(db: Session, table: Table, column: str, value: str) -> list[dict]:
    rows = db.execute(select(table).where(table.c[column] == value)).mappings().all()
    return [{k: _encode(v) for k, v in r.items()} for r in rows]


def snapshot_application(db: Session, app_id: str, label: str) -> None:
    tables = {Application.__tablename__: _rows(db, Application.__table__, "id", app_id)}
    for t in _ORDER[1:]:
        tables[t.name] = _rows(db, t, "application_id", app_id)
    _push(db, "application", {"id": app_id, "label": label, "tables": tables})


def snapshot_deadline(db: Session, item_id: str, label: str) -> None:
    tables = {ActionItem.__tablename__: _rows(db, ActionItem.__table__, "id", item_id)}
    _push(db, "deadline", {"id": item_id, "label": label, "tables": tables})


def _push(db: Session, kind: str, entry: dict) -> None:
    stack = list(settings_service.get(db, _key(kind)) or [])
    entry["deleted_at"] = datetime.now(timezone.utc).isoformat()
    stack.append(entry)
    settings_service.set_value(db, _key(kind), stack[-MAX_ENTRIES:])


def peek(db: Session, kind: str) -> dict | None:
    stack = settings_service.get(db, _key(kind)) or []
    if not stack:
        return None
    last = stack[-1]
    return {"id": last["id"], "label": last["label"], "deleted_at": last["deleted_at"], "count": len(stack)}


def restore_last(db: Session, kind: str) -> dict:
    stack = list(settings_service.get(db, _key(kind)) or [])
    if not stack:
        raise TrashError("nothing to recover")
    entry = stack[-1]
    tables = entry["tables"]
    if kind == "deadline":
        (row,) = tables[ActionItem.__tablename__] or [None]
        if row is None:
            raise TrashError("snapshot is empty")
        if db.get(Application, row["application_id"]) is None:
            raise TrashError("its application has been removed; recover the application first")
    for t in _ORDER:
        rows = tables.get(t.name, [])
        if t.name == ApplicationEvent.__tablename__:
            rows = sorted(rows, key=lambda r: str(r.get("recorded_at")))
        for row in rows:
            values = {k: _decode(v) for k, v in row.items() if k in t.c}
            # Optional links to rows that no longer exist are dropped rather than failing.
            for fk_col, parent in (("assessment_id", Assessment), ("interview_id", Interview),
                                   ("source_event_id", ApplicationEvent),
                                   ("supersedes_event_id", ApplicationEvent)):
                if values.get(fk_col) and db.get(parent, values[fk_col]) is None and not any(
                    r.get("id") == values[fk_col] for r in tables.get(parent.__tablename__, [])
                ):
                    values[fk_col] = None
            if db.execute(select(t.c.id).where(t.c.id == values["id"])).first() is None:
                db.execute(t.insert().values(**values))
    stack.pop()
    settings_service.set_value(db, _key(kind), stack)
    db.flush()
    return {"restored": entry["id"], "label": entry["label"]}
