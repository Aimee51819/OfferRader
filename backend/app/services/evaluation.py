"""Fixture-level evaluation: event classification P/R, company+role, requisition, deadlines.

Scores are reported per backend; nothing here assumes the model is right.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from ..domain.deadlines import normalize_deadline
from ..domain.normalize import normalize_company, normalize_req_id, title_similarity
from .extraction.extractor import MessageInput, extract
from .llm.ollama import LLMError
from .mail.fake import to_provider_message

_IGNORED_TYPES = {"other"}


def _pred_deadlines(ext, pm) -> list[dict]:
    out = []
    for e in ext.events:
        if e.deadline and (e.deadline.raw_expression or e.deadline.date_only):
            out.append(
                normalize_deadline(
                    e.deadline.raw_expression, received_at=pm.received_at, sent_at=pm.sent_at,
                    timezone_hint=e.deadline.timezone_text, date_only_hint=e.deadline.date_only,
                    anchor_hint=e.deadline.anchor,
                ).as_dict()
            )
    return out


def _deadline_match(exp: dict, got: dict) -> bool:
    if exp.get("due_at"):
        if not got.get("due_at"):
            return False
        a = datetime.fromisoformat(exp["due_at"])
        b = datetime.fromisoformat(got["due_at"])
        if abs((a - b).total_seconds()) > 60:
            return False
    elif got.get("due_at"):
        return False  # expected no instant (date-only / ambiguous) but one was fabricated
    if exp.get("due_date") and exp["due_date"] != got.get("due_date"):
        return False
    if exp.get("precision") and exp["precision"] != got.get("precision"):
        return False
    if exp.get("needs_review") and not got.get("needs_review"):
        return False
    return True


def evaluate_corpus(fixtures: list[dict], backend) -> dict:
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    rows = []
    jr_correct = jr_total = 0
    cr_correct = cr_total = 0
    req_correct = req_total = 0
    dl_correct = dl_total = 0
    fabricated_instants = 0
    errors = 0
    for f in fixtures:
        exp = f["expected"]
        pm = to_provider_message(f)
        try:
            out = extract(
                MessageInput(f"{pm.sender_name} <{pm.sender_address}>", pm.subject, pm.body_text, pm.received_at, pm.sent_at),
                backend,
            )
        except LLMError as exc:
            errors += 1
            rows.append({"name": f["name"], "error": str(exc)[:200]})
            continue
        ext = out.extraction
        if ext is None:
            errors += 1
            rows.append({"name": f["name"], "error": "invalid output", "notes": out.validation_errors})
            continue
        got_types = Counter(e.type for e in ext.events if e.type not in _IGNORED_TYPES)
        exp_types = Counter(t for t in exp.get("events", []) if t not in _IGNORED_TYPES)
        for t in set(got_types) | set(exp_types):
            m = min(got_types[t], exp_types[t])
            tp[t] += m
            fp[t] += got_types[t] - m
            fn[t] += exp_types[t] - m
        jr_total += 1
        jr_correct += bool(ext.is_job_related) == bool(exp["is_job_related"])
        row = {"name": f["name"], "backend": out.model_name, "events": dict(got_types), "expected_events": dict(exp_types)}
        if exp.get("company"):
            cr_total += 1
            ok = normalize_company(ext.company.name) == normalize_company(exp["company"]) and (
                exp.get("role") is None or title_similarity(ext.role.title, exp["role"]) >= 0.92
            )
            cr_correct += ok
            row["company_role_ok"] = ok
        if exp.get("requisition_id"):
            req_total += 1
            ok = normalize_req_id(ext.role.requisition_id) == normalize_req_id(exp["requisition_id"])
            req_correct += ok
            row["requisition_ok"] = ok
        exp_dl = exp.get("deadlines") or []
        got_dl = _pred_deadlines(ext, pm)
        for i, d in enumerate(exp_dl):
            dl_total += 1
            ok = i < len(got_dl) and _deadline_match(d, got_dl[i])
            dl_correct += ok
            if not d.get("due_at") and i < len(got_dl) and got_dl[i].get("due_at"):
                fabricated_instants += 1
        row["deadlines"] = got_dl
        rows.append(row)

    def pr(t: str | None = None) -> dict:
        if t is None:
            T, P, N = sum(tp.values()), sum(fp.values()), sum(fn.values())
        else:
            T, P, N = tp[t], fp[t], fn[t]
        return {
            "precision": round(T / (T + P), 3) if T + P else None,
            "recall": round(T / (T + N), 3) if T + N else None,
            "tp": T, "fp": P, "fn": N,
        }

    types = sorted(set(tp) | set(fp) | set(fn))

    def ratio(a: int, b: int):
        return round(a / b, 3) if b else None

    return {
        "summary": {
            "backend": getattr(backend, "name", "?"),
            "fixtures": len(fixtures),
            "errors": errors,
            "event_classification_micro": pr(),
            "event_classification_by_type": {t: pr(t) for t in types},
            "is_job_related_accuracy": ratio(jr_correct, jr_total),
            "company_role_accuracy": ratio(cr_correct, cr_total),
            "requisition_accuracy": ratio(req_correct, req_total),
            "deadline_accuracy": ratio(dl_correct, dl_total),
            "deadlines_evaluated": dl_total,
            "fabricated_deadline_instants": fabricated_instants,
        },
        "rows": rows,
    }
