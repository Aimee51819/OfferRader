"""Command-line utilities.

  python -m app.cli migrate                      # backup + upgrade schema
  python -m app.cli check-ollama                 # verify Ollama + model tag
  python -m app.cli reparse-fixtures [--backend ollama|replay|rules] [names...]
                                                 # parse synthetic fixtures in an in-memory sandbox
  python -m app.cli eval [--backend ollama|rules|replay] [--json out.json]
                                                 # precision/recall/accuracy on the synthetic corpus
  python -m app.cli demo [--reset]               # load synthetic demo mail into DATA_DIR
  python -m app.cli sync-once                    # one sync of all enabled accounts
  python -m app.cli integration-token            # print the local integration token
  python -m app.cli backup                       # SQLite snapshot into DATA_DIR/backups
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "emails"


def _load_fixtures(names: list[str] | None = None) -> list[dict]:
    items = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(FIXTURES.glob("*.json"))]
    if names:
        items = [i for i in items if i["name"] in names]
    return items


def _backend(kind: str, fixtures: list[dict]):
    from .services.llm.offline import ReplayBackend, RulesBackend
    from .services.llm.ollama import default_backend

    if kind == "replay":
        return ReplayBackend({f["subject"]: f["model_output"] for f in fixtures})
    if kind == "rules":
        return RulesBackend()
    backend = default_backend()
    h = backend.health()
    if not h.get("ok"):
        sys.exit(f"Ollama not ready: {h.get('error') or h.get('hint')}")
    return backend


def cmd_check_ollama(_args) -> None:
    from .services.llm.ollama import default_backend

    print(json.dumps(default_backend().health(), indent=2))


def cmd_reparse(args) -> None:
    """Parse fixtures with the chosen backend. Never touches DATA_DIR (in-memory only)."""
    from .domain.deadlines import normalize_deadline
    from .services.extraction.extractor import MessageInput, extract
    from .services.mail.fake import to_provider_message

    fixtures = _load_fixtures(args.names)
    backend = _backend(args.backend, fixtures)
    for f in fixtures:
        pm = to_provider_message(f)
        out = extract(MessageInput(f"{pm.sender_name} <{pm.sender_address}>", pm.subject, pm.body_text, pm.received_at, pm.sent_at), backend)
        ext = out.extraction
        print(f"== {f['name']}  [{out.model_name}{' skipped:' + out.skipped_reason if out.skipped_reason else ''}]")
        if ext is None:
            print("   INVALID:", out.validation_errors)
            continue
        print(f"   job_related={ext.is_job_related} company={ext.company.name!r} role={ext.role.title!r} req={ext.role.requisition_id!r}")
        for e in ext.events:
            d = None
            if e.deadline and (e.deadline.raw_expression or e.deadline.date_only):
                d = normalize_deadline(e.deadline.raw_expression, received_at=pm.received_at, sent_at=pm.sent_at,
                                       timezone_hint=e.deadline.timezone_text, date_only_hint=e.deadline.date_only).as_dict()
            print(f"   - {e.type} conf={e.confidence:.2f} deadline={d and {k: d[k] for k in ('due_at', 'due_date', 'precision', 'needs_review')}}")
        if out.validation_errors:
            print("   notes:", out.validation_errors)


def cmd_eval(args) -> None:
    from .services.evaluation import evaluate_corpus

    fixtures = _load_fixtures()
    report = evaluate_corpus(fixtures, _backend(args.backend, fixtures))
    print(json.dumps(report["summary"], indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"full report: {args.json}")


def cmd_demo(args) -> None:
    from .services.demo import load_demo

    print(json.dumps(load_demo(reset=args.reset), indent=2, default=str))


def cmd_migrate(_args) -> None:
    from .db import init_db

    init_db()
    print("database is at the latest schema")


def cmd_sync_once(_args) -> None:
    from .db import init_db
    from .services.sync import sync_all

    init_db()
    print(sync_all())


def cmd_token(_args) -> None:
    from .db import SessionLocal, init_db
    from .services.settings_service import integration_token

    init_db()
    db = SessionLocal()
    try:
        print(integration_token(db))
    finally:
        db.close()


def cmd_backup(_args) -> None:
    from .db import backup_database

    print(backup_database("manual"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("check-ollama").set_defaults(fn=cmd_check_ollama)
    rp = sub.add_parser("reparse-fixtures")
    rp.add_argument("--backend", default="ollama", choices=["ollama", "replay", "rules"])
    rp.add_argument("names", nargs="*")
    rp.set_defaults(fn=cmd_reparse)
    ev = sub.add_parser("eval")
    ev.add_argument("--backend", default="ollama", choices=["ollama", "replay", "rules"])
    ev.add_argument("--json")
    ev.set_defaults(fn=cmd_eval)
    dm = sub.add_parser("demo")
    dm.add_argument("--reset", action="store_true")
    dm.set_defaults(fn=cmd_demo)
    sub.add_parser("sync-once").set_defaults(fn=cmd_sync_once)
    sub.add_parser("integration-token").set_defaults(fn=cmd_token)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
