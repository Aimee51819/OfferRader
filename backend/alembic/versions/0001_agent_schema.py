"""Agent schema: companies, postings, applications, evidence, review, reminders.

Revision ID: 0001_agent_schema
Revises: (none)

Upgrade path from the original application-tracker schema
---------------------------------------------------------
The original app created tables with ``create_all`` and integer IDs. When such
a database is found, this migration:

1. renames every legacy table to ``legacy_<name>`` (nothing is dropped),
2. creates the new schema,
3. copies applications -> companies + applications (+ an import event),
   status_events -> application_events, and IMAP accounts -> email_accounts
   (disabled; the encrypted password ciphertext is copied to ``credentials``).

``downgrade`` drops the new tables and renames the legacy tables back, so the
original app can run again. ``app.db.init_db`` also takes a SQLite backup in
DATA_DIR/backups before any migration runs.
"""
import json
import re
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0001_agent_schema"
down_revision = None
branch_labels = None
depends_on = None

LEGACY_TABLES = (
    "applications",
    "status_events",
    "dismissed_duplicates",
    "email_accounts",
    "processed_emails",
    "suggestions",
    "llm_providers",
    "settings",
)

_STAGE_MAP = {
    "saved": "tracked",
    "applied": "submitted",
    "online_assessment": "assessment_invited",
    "phone_screen": "screening",
    "interview": "interviewing",
    "offer": "offer",
    "rejected": "rejected",
    "ghosted": "closed",
    "accepted": "accepted",
}


def _uid() -> str:
    return str(uuid.uuid4())


def _iso(value) -> str | None:
    """Legacy columns are naive UTC DATETIME text; emit the new ISO-Z format."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _norm_company(name: str) -> str:
    # Frozen copy of the normalizer at the time of this migration.
    text = name.lower().replace("&", " and ")
    text = re.sub(r"^the\s+", "", text)
    text = re.sub(
        r"\b(incorporated|inc|llc|ltd|limited|corp|corporation|co|company|plc|gmbh)\b\.?",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_title(title: str) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _is_legacy(bind) -> bool:
    insp = sa.inspect(bind)
    if "applications" not in insp.get_table_names():
        return False
    cols = {c["name"] for c in insp.get_columns("applications")}
    return "status" in cols and "stage" not in cols


def upgrade() -> None:
    bind = op.get_bind()
    legacy = _is_legacy(bind)
    if legacy:
        existing = set(sa.inspect(bind).get_table_names())
        for name in LEGACY_TABLES:
            if name in existing:
                op.rename_table(name, f"legacy_{name}")
    _create_schema()
    if legacy:
        _copy_legacy_data(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _drop_schema()
    existing = set(sa.inspect(bind).get_table_names())
    for name in LEGACY_TABLES:
        if f"legacy_{name}" in existing:
            op.rename_table(f"legacy_{name}", name)


def _copy_legacy_data(bind) -> None:
    now = _iso(datetime.now(timezone.utc))
    tables = set(sa.inspect(bind).get_table_names())
    company_ids: dict[str, str] = {}
    app_ids: dict[int, str] = {}

    rows = bind.execute(sa.text("SELECT * FROM legacy_applications")).mappings().all()
    for row in rows:
        name = (row["company"] or "Unknown company").strip()
        norm = _norm_company(name) or "unknown company"
        cid = company_ids.get(norm)
        if cid is None:
            cid = _uid()
            company_ids[norm] = cid
            bind.execute(
                sa.text(
                    "INSERT INTO companies (id, canonical_name, normalized_name, created_at, updated_at)"
                    " VALUES (:id, :n, :nn, :t, :t)"
                ),
                {"id": cid, "n": name, "nn": norm, "t": now},
            )
        stage = _STAGE_MAP.get((row["status"] or "saved").lower(), "tracked")
        submitted = stage not in {"tracked", "ready"}
        aid = _uid()
        app_ids[row["id"]] = aid
        created = _iso(row["created_at"]) or now
        applied = _iso(row["date_applied"]) if submitted else None
        evidence = None
        if submitted and applied:
            evidence = "acknowledgment_email" if (row["source"] or "") == "email" else "manual_confirmed"
        notes = row["notes"]
        legacy_note = f"Migrated from legacy tracker (legacy id {row['id']}, status {row['status']})."
        bind.execute(
            sa.text(
                "INSERT INTO applications (id, company_id, role_title, normalized_role_title,"
                " external_job_id, posting_url, location, stage, stage_updated_at, submitted_at,"
                " submission_evidence_type, source_reference, notes, manual_stage_lock,"
                " created_at, updated_at)"
                " VALUES (:id, :cid, :title, :nt, :jid, :url, :loc, :stage, :t, :sub, :ev, :src,"
                " :notes, 0, :created, :t)"
            ),
            {
                "id": aid,
                "cid": cid,
                "title": row["title"] or "Unknown",
                "nt": _norm_title(row["title"] or ""),
                "jid": row["job_id"] if "job_id" in row.keys() else None,
                "url": row["url"],
                "loc": row["location"],
                "stage": stage,
                "t": now,
                "sub": applied,
                "ev": evidence,
                "src": f"legacy:applications:{row['id']}",
                "notes": (notes + "\n\n" if notes else "") + legacy_note,
                "created": created,
            },
        )
        bind.execute(
            sa.text(
                "INSERT INTO application_events (id, application_id, event_type, source, event_at,"
                " recorded_at, to_stage, description, is_approved, idempotency_key, actor)"
                " VALUES (:id, :aid, 'imported', 'import', :at, :t, :stage, :d, 1, :k, 'migration')"
            ),
            {
                "id": _uid(),
                "aid": aid,
                "at": created,
                "t": now,
                "stage": stage,
                "d": legacy_note,
                "k": f"legacy:applications:{row['id']}",
            },
        )

    if "legacy_status_events" in tables:
        events = bind.execute(sa.text("SELECT * FROM legacy_status_events")).mappings().all()
        for ev in events:
            aid = app_ids.get(ev["application_id"])
            if aid is None:
                continue
            src = str(ev["source"] or "manual").lower()
            src = src if src in {"email", "manual"} else "import"
            bind.execute(
                sa.text(
                    "INSERT INTO application_events (id, application_id, event_type, source,"
                    " event_at, recorded_at, from_stage, to_stage, description, is_approved,"
                    " idempotency_key, actor)"
                    " VALUES (:id, :aid, 'stage_change', :src, :at, :t, :f, :to, :d, 1, :k, 'migration')"
                ),
                {
                    "id": _uid(),
                    "aid": aid,
                    "src": src,
                    "at": _iso(ev["created_at"]) or now,
                    "t": now,
                    "f": _STAGE_MAP.get(str(ev["from_status"] or "").lower()),
                    "to": _STAGE_MAP.get(str(ev["to_status"] or "").lower()),
                    "d": ev["note"] or "Legacy status change",
                    "k": f"legacy:status_events:{ev['id']}",
                },
            )

    if "legacy_email_accounts" in tables:
        accounts = bind.execute(sa.text("SELECT * FROM legacy_email_accounts")).mappings().all()
        for acc in accounts:
            cred_id = None
            if acc["password_encrypted"]:
                cred_id = _uid()
                bind.execute(
                    sa.text(
                        "INSERT INTO credentials (id, kind, ciphertext, created_at, updated_at)"
                        " VALUES (:id, 'imap_password', :c, :t, :t)"
                    ),
                    {"id": cred_id, "c": acc["password_encrypted"], "t": now},
                )
            scope = {
                "imap_host": acc["imap_host"],
                "imap_port": acc["imap_port"],
                "use_ssl": bool(acc["use_ssl"]),
                "folders": [acc["folder"] or "INBOX"],
            }
            bind.execute(
                sa.text(
                    "INSERT INTO email_accounts (id, provider, address, display_name, auth_method,"
                    " credential_ref, enabled, sync_scope, sync_cursor, created_at, updated_at)"
                    " VALUES (:id, 'imap', :addr, :name, 'imap_password', :cred, 0, :scope, :cur, :t, :t)"
                ),
                {
                    "id": _uid(),
                    "addr": acc["username"],
                    "name": acc["name"],
                    "cred": cred_id,
                    "scope": json.dumps(scope),
                    "cur": json.dumps({"INBOX": acc["last_seen_uid"]}) if acc["last_seen_uid"] else None,
                    "t": now,
                },
            )


def _create_schema() -> None:
    op.create_table('companies',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('canonical_name', sa.Text(), nullable=False),
    sa.Column('normalized_name', sa.Text(), nullable=False),
    sa.Column('website_domain', sa.Text(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('normalized_name')
    )
    op.create_table('credentials',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('email_accounts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=True),
    sa.Column('auth_method', sa.String(length=32), nullable=False),
    sa.Column('credential_ref', sa.Text(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('sync_scope', sa.JSON(), nullable=False),
    sa.Column('sync_cursor', sa.Text(), nullable=True),
    sa.Column('last_successful_sync_at', sa.String(length=32), nullable=True),
    sa.Column('last_attempt_at', sa.String(length=32), nullable=True),
    sa.Column('last_error_code', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'address', name='uq_account_address')
    )
    op.create_table('notification_rules',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('lead_time_minutes', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('resume_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('filename', sa.Text(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('local_path', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('settings',
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('value', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('company_aliases',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('alias', sa.Text(), nullable=False),
    sa.Column('normalized_alias', sa.Text(), nullable=False),
    sa.Column('alias_type', sa.String(length=16), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'normalized_alias', 'alias_type', name='uq_alias')
    )
    with op.batch_alter_table('company_aliases', schema=None) as batch_op:
        batch_op.create_index('ix_alias_lookup', ['normalized_alias', 'alias_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_company_aliases_company_id'), ['company_id'], unique=False)

    op.create_table('email_messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email_account_id', sa.String(length=36), nullable=False),
    sa.Column('provider_message_id', sa.Text(), nullable=False),
    sa.Column('provider_thread_id', sa.Text(), nullable=True),
    sa.Column('internet_message_id', sa.Text(), nullable=True),
    sa.Column('sender_address', sa.Text(), nullable=True),
    sa.Column('sender_name', sa.Text(), nullable=True),
    sa.Column('recipient_address', sa.Text(), nullable=True),
    sa.Column('subject', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.String(length=32), nullable=True),
    sa.Column('received_at', sa.String(length=32), nullable=True),
    sa.Column('body_hash', sa.String(length=64), nullable=False),
    sa.Column('evidence_excerpt', sa.Text(), nullable=True),
    sa.Column('body_cache_ref', sa.Text(), nullable=True),
    sa.Column('web_link', sa.Text(), nullable=True),
    sa.Column('ingested_at', sa.String(length=32), nullable=False),
    sa.Column('processing_state', sa.String(length=16), nullable=False),
    sa.Column('processing_error', sa.Text(), nullable=True),
    sa.Column('processing_attempts', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['email_account_id'], ['email_accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email_account_id', 'provider_message_id', name='uq_account_message')
    )
    with op.batch_alter_table('email_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_messages_email_account_id'), ['email_account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_messages_provider_thread_id'), ['provider_thread_id'], unique=False)
        batch_op.create_index('ix_msg_account_received', ['email_account_id', 'received_at'], unique=False)
        batch_op.create_index('ix_msg_internet_id', ['internet_message_id'], unique=False)
        batch_op.create_index('ix_msg_state', ['processing_state'], unique=False)

    op.create_table('job_postings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('normalized_title', sa.Text(), nullable=False),
    sa.Column('external_job_id', sa.Text(), nullable=True),
    sa.Column('posting_url', sa.Text(), nullable=True),
    sa.Column('canonical_posting_url', sa.Text(), nullable=True),
    sa.Column('ats_platform', sa.Text(), nullable=True),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('work_mode', sa.Text(), nullable=True),
    sa.Column('employment_type', sa.Text(), nullable=True),
    sa.Column('season', sa.Text(), nullable=True),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('first_seen_at', sa.String(length=32), nullable=False),
    sa.Column('last_seen_at', sa.String(length=32), nullable=False),
    sa.Column('description_excerpt', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('job_postings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_postings_company_id'), ['company_id'], unique=False)
        batch_op.create_index('ix_posting_canonical_url', ['canonical_posting_url'], unique=False)
        batch_op.create_index('ix_posting_company_title', ['company_id', 'normalized_title'], unique=False)
        batch_op.create_index('ix_posting_external_id', ['external_job_id'], unique=False)

    op.create_table('sync_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email_account_id', sa.String(length=36), nullable=False),
    sa.Column('started_at', sa.String(length=32), nullable=False),
    sa.Column('finished_at', sa.String(length=32), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('messages_seen', sa.Integer(), nullable=False),
    sa.Column('messages_new', sa.Integer(), nullable=False),
    sa.Column('messages_parsed', sa.Integer(), nullable=False),
    sa.Column('proposals_created', sa.Integer(), nullable=False),
    sa.Column('events_applied', sa.Integer(), nullable=False),
    sa.Column('error_count', sa.Integer(), nullable=False),
    sa.Column('safe_error_summary', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['email_account_id'], ['email_accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sync_runs_email_account_id'), ['email_account_id'], unique=False)

    op.create_table('applications',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('job_posting_id', sa.String(length=36), nullable=True),
    sa.Column('role_title', sa.Text(), nullable=False),
    sa.Column('normalized_role_title', sa.Text(), nullable=False),
    sa.Column('external_job_id', sa.Text(), nullable=True),
    sa.Column('posting_url', sa.Text(), nullable=True),
    sa.Column('canonical_posting_url', sa.Text(), nullable=True),
    sa.Column('ats_platform', sa.Text(), nullable=True),
    sa.Column('recruiting_cycle', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=8), nullable=True),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('application_channel', sa.String(length=16), nullable=True),
    sa.Column('resume_version_id', sa.String(length=36), nullable=True),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('stage_updated_at', sa.String(length=32), nullable=False),
    sa.Column('stage_evidence_at', sa.String(length=32), nullable=True),
    sa.Column('submitted_at', sa.String(length=32), nullable=True),
    sa.Column('acknowledged_at', sa.String(length=32), nullable=True),
    sa.Column('submission_evidence_type', sa.String(length=32), nullable=True),
    sa.Column('submission_receipt_id', sa.Text(), nullable=True),
    sa.Column('submission_idempotency_key', sa.Text(), nullable=True),
    sa.Column('source_reference', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('manual_stage_lock', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resume_version_id'], ['resume_versions.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('submission_idempotency_key')
    )
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.create_index('ix_app_company_role', ['company_id', 'normalized_role_title'], unique=False)
        batch_op.create_index('ix_app_external_job_id', ['external_job_id'], unique=False)
        batch_op.create_index('ix_app_stage_updated', ['stage', 'updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_applications_canonical_posting_url'), ['canonical_posting_url'], unique=False)
        batch_op.create_index(batch_op.f('ix_applications_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_applications_job_posting_id'), ['job_posting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_applications_resume_version_id'), ['resume_version_id'], unique=False)

    op.create_table('parsed_messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email_message_id', sa.String(length=36), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('model_name', sa.Text(), nullable=False),
    sa.Column('model_parameters', sa.JSON(), nullable=False),
    sa.Column('is_job_related', sa.Boolean(), nullable=False),
    sa.Column('extracted_payload', sa.JSON(), nullable=True),
    sa.Column('overall_confidence', sa.Float(), nullable=True),
    sa.Column('validation_errors', sa.JSON(), nullable=True),
    sa.Column('parsed_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['email_message_id'], ['email_messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('parsed_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_parsed_messages_email_message_id'), ['email_message_id'], unique=False)

    op.create_table('application_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('application_id', sa.String(length=36), nullable=True),
    sa.Column('email_message_id', sa.String(length=36), nullable=True),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('event_at', sa.String(length=32), nullable=False),
    sa.Column('recorded_at', sa.String(length=32), nullable=False),
    sa.Column('from_stage', sa.String(length=32), nullable=True),
    sa.Column('to_stage', sa.String(length=32), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('evidence_quote', sa.Text(), nullable=True),
    sa.Column('evidence_locator', sa.JSON(), nullable=True),
    sa.Column('is_approved', sa.Boolean(), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('supersedes_event_id', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['email_message_id'], ['email_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['supersedes_event_id'], ['application_events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('application_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_application_events_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_application_events_email_message_id'), ['email_message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_application_events_supersedes_event_id'), ['supersedes_event_id'], unique=False)
        batch_op.create_index('ix_event_app_time', ['application_id', 'event_at'], unique=False)

    op.create_table('review_proposals',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email_message_id', sa.String(length=36), nullable=True),
    sa.Column('parsed_message_id', sa.String(length=36), nullable=True),
    sa.Column('target_application_id', sa.String(length=36), nullable=True),
    sa.Column('proposal_type', sa.String(length=24), nullable=False),
    sa.Column('proposed_changes', sa.JSON(), nullable=False),
    sa.Column('alternatives', sa.JSON(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('reviewed_at', sa.String(length=32), nullable=True),
    sa.Column('review_notes', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.Text(), nullable=True),
    sa.Column('applied_event_ids', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['email_message_id'], ['email_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parsed_message_id'], ['parsed_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['target_application_id'], ['applications.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('review_proposals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_proposals_email_message_id'), ['email_message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_proposals_parsed_message_id'), ['parsed_message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_proposals_target_application_id'), ['target_application_id'], unique=False)
        batch_op.create_index('ix_review_state_created', ['state', 'created_at'], unique=False)

    op.create_table('assessments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('application_id', sa.String(length=36), nullable=False),
    sa.Column('invitation_event_id', sa.String(length=36), nullable=True),
    sa.Column('provider', sa.Text(), nullable=True),
    sa.Column('assessment_type', sa.String(length=16), nullable=False),
    sa.Column('assessment_url', sa.Text(), nullable=True),
    sa.Column('invited_at', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.String(length=32), nullable=True),
    sa.Column('completed_at', sa.String(length=32), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['invitation_event_id'], ['application_events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('assessments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assessments_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_assessments_invitation_event_id'), ['invitation_event_id'], unique=False)

    op.create_table('interviews',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('application_id', sa.String(length=36), nullable=False),
    sa.Column('source_event_id', sa.String(length=36), nullable=True),
    sa.Column('round_label', sa.Text(), nullable=True),
    sa.Column('starts_at', sa.String(length=32), nullable=True),
    sa.Column('ends_at', sa.String(length=32), nullable=True),
    sa.Column('original_timezone', sa.Text(), nullable=True),
    sa.Column('location_or_video_url', sa.Text(), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_event_id'], ['application_events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_interviews_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_interviews_source_event_id'), ['source_event_id'], unique=False)

    op.create_table('action_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('application_id', sa.String(length=36), nullable=False),
    sa.Column('assessment_id', sa.String(length=36), nullable=True),
    sa.Column('interview_id', sa.String(length=36), nullable=True),
    sa.Column('source_event_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('instruction', sa.Text(), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('due_at', sa.String(length=32), nullable=True),
    sa.Column('due_date', sa.String(length=10), nullable=True),
    sa.Column('due_timezone', sa.Text(), nullable=True),
    sa.Column('due_precision', sa.String(length=16), nullable=False),
    sa.Column('due_expression_raw', sa.Text(), nullable=True),
    sa.Column('due_anchor', sa.Text(), nullable=True),
    sa.Column('deadline_assumption', sa.Text(), nullable=True),
    sa.Column('deadline_confidence', sa.Float(), nullable=True),
    sa.Column('evidence_quote', sa.Text(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.Column('completed_at', sa.String(length=32), nullable=True),
    sa.CheckConstraint("due_precision IN ('instant','day','relative','unknown')", name='ck_due_precision'),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['assessment_id'], ['assessments.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_event_id'], ['application_events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('action_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_action_items_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_action_items_assessment_id'), ['assessment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_action_items_interview_id'), ['interview_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_action_items_source_event_id'), ['source_event_id'], unique=False)
        batch_op.create_index('ix_action_state_due', ['state', 'due_at'], unique=False)

    op.create_table('notification_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('action_item_id', sa.String(length=36), nullable=False),
    sa.Column('rule_id', sa.String(length=36), nullable=False),
    sa.Column('scheduled_for_at', sa.String(length=32), nullable=False),
    sa.Column('triggered_at', sa.String(length=32), nullable=False),
    sa.Column('delivery_state', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('dismissed', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['action_item_id'], ['action_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_id'], ['notification_rules.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('action_item_id', 'rule_id', 'scheduled_for_at', name='uq_notification_once')
    )
    with op.batch_alter_table('notification_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_log_action_item_id'), ['action_item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_log_rule_id'), ['rule_id'], unique=False)



def _drop_schema() -> None:
    with op.batch_alter_table('notification_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_log_rule_id'))
        batch_op.drop_index(batch_op.f('ix_notification_log_action_item_id'))

    op.drop_table('notification_log')
    with op.batch_alter_table('action_items', schema=None) as batch_op:
        batch_op.drop_index('ix_action_state_due')
        batch_op.drop_index(batch_op.f('ix_action_items_source_event_id'))
        batch_op.drop_index(batch_op.f('ix_action_items_interview_id'))
        batch_op.drop_index(batch_op.f('ix_action_items_assessment_id'))
        batch_op.drop_index(batch_op.f('ix_action_items_application_id'))

    op.drop_table('action_items')
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_interviews_source_event_id'))
        batch_op.drop_index(batch_op.f('ix_interviews_application_id'))

    op.drop_table('interviews')
    with op.batch_alter_table('assessments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assessments_invitation_event_id'))
        batch_op.drop_index(batch_op.f('ix_assessments_application_id'))

    op.drop_table('assessments')
    with op.batch_alter_table('review_proposals', schema=None) as batch_op:
        batch_op.drop_index('ix_review_state_created')
        batch_op.drop_index(batch_op.f('ix_review_proposals_target_application_id'))
        batch_op.drop_index(batch_op.f('ix_review_proposals_parsed_message_id'))
        batch_op.drop_index(batch_op.f('ix_review_proposals_email_message_id'))

    op.drop_table('review_proposals')
    with op.batch_alter_table('application_events', schema=None) as batch_op:
        batch_op.drop_index('ix_event_app_time')
        batch_op.drop_index(batch_op.f('ix_application_events_supersedes_event_id'))
        batch_op.drop_index(batch_op.f('ix_application_events_email_message_id'))
        batch_op.drop_index(batch_op.f('ix_application_events_application_id'))

    op.drop_table('application_events')
    with op.batch_alter_table('parsed_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_parsed_messages_email_message_id'))

    op.drop_table('parsed_messages')
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_applications_resume_version_id'))
        batch_op.drop_index(batch_op.f('ix_applications_job_posting_id'))
        batch_op.drop_index(batch_op.f('ix_applications_company_id'))
        batch_op.drop_index(batch_op.f('ix_applications_canonical_posting_url'))
        batch_op.drop_index('ix_app_stage_updated')
        batch_op.drop_index('ix_app_external_job_id')
        batch_op.drop_index('ix_app_company_role')

    op.drop_table('applications')
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sync_runs_email_account_id'))

    op.drop_table('sync_runs')
    with op.batch_alter_table('job_postings', schema=None) as batch_op:
        batch_op.drop_index('ix_posting_external_id')
        batch_op.drop_index('ix_posting_company_title')
        batch_op.drop_index('ix_posting_canonical_url')
        batch_op.drop_index(batch_op.f('ix_job_postings_company_id'))

    op.drop_table('job_postings')
    with op.batch_alter_table('email_messages', schema=None) as batch_op:
        batch_op.drop_index('ix_msg_state')
        batch_op.drop_index('ix_msg_internet_id')
        batch_op.drop_index('ix_msg_account_received')
        batch_op.drop_index(batch_op.f('ix_email_messages_provider_thread_id'))
        batch_op.drop_index(batch_op.f('ix_email_messages_email_account_id'))

    op.drop_table('email_messages')
    with op.batch_alter_table('company_aliases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_aliases_company_id'))
        batch_op.drop_index('ix_alias_lookup')

    op.drop_table('company_aliases')
    op.drop_table('settings')
    op.drop_table('resume_versions')
    op.drop_table('notification_rules')
    op.drop_table('email_accounts')
    op.drop_table('credentials')
    op.drop_table('companies')
