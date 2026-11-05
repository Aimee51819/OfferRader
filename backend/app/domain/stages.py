"""Summary-stage transition policy. Events are history; the stage is a summary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import EVENT_TO_STAGE, SENSITIVE_EVENT_TYPES, STAGE_RANK, TERMINAL_STAGES


@dataclass
class StageDecision:
    new_stage: str | None
    needs_review: bool
    reason: str


def decide_stage(
    *,
    current_stage: str,
    stage_evidence_at: datetime | None,
    manual_lock: bool,
    event_type: str,
    event_at: datetime,
    source: str,
) -> StageDecision:
    target = EVENT_TO_STAGE.get(event_type)
    if target is None:
        return StageDecision(None, False, "event does not imply a stage")
    if target == current_stage:
        return StageDecision(None, False, "already at this stage")
    if manual_lock and source != "manual":
        return StageDecision(None, True, "stage is manually pinned; review before changing")
    if stage_evidence_at is not None and event_at < stage_evidence_at:
        return StageDecision(None, False, "older than the evidence for the current stage; kept as history")
    if current_stage in TERMINAL_STAGES:
        if target in TERMINAL_STAGES:
            return StageDecision(target, True, f"terminal change {current_stage} -> {target}")
        return StageDecision(target, True, f"newer activity after {current_stage}; process may have reopened")
    if target in TERMINAL_STAGES:
        return StageDecision(target, event_type in SENSITIVE_EVENT_TYPES, "decision")
    if STAGE_RANK.get(target, 0) < STAGE_RANK.get(current_stage, 0):
        return StageDecision(None, False, f"{target} is behind {current_stage}; kept as history")
    return StageDecision(target, False, "forward progress")
