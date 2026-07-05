"""baseline schema: 7 tenant tables + extensions

Revision ID: 0001
Revises:
Create Date: 2026-07-05

Mirrors RENDER-MIGRATION.md §3.1. Enables the pgcrypto extension
(gen_random_uuid server defaults) and the pgvector extension (used by the W3
cross-run embedding index), then creates all seven tables and their indexes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extensions first ─────────────────────────────────────────────────────
    # pgcrypto -> gen_random_uuid() used by UUID primary-key server defaults.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # pgvector -> vector type used by the W3 cross-run similarity index.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("google_sub", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("google_sub"),
    )

    # ── sessions ───────────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id"])

    # ── user_credentials ───────────────────────────────────────────────────────
    op.create_table(
        "user_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column(
            "key_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "kind", "provider", name="uq_user_credentials_scope"
        ),
    )
    op.create_index("ix_creds_user", "user_credentials", ["user_id"])

    # ── runs ───────────────────────────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("legacy_run_id", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("progress", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "current_step", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "config", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("pdf_object_key", sa.Text(), nullable=True),
        sa.Column("tree_object_key", sa.Text(), nullable=True),
        sa.Column("node_index_object_key", sa.Text(), nullable=True),
        sa.Column("coverage_pct", sa.Float(), nullable=True),
        sa.Column("task_count", sa.Integer(), nullable=True),
        sa.Column("worker_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runs_user", "runs", ["user_id", sa.text("created_at DESC")]
    )

    # ── tasks ──────────────────────────────────────────────────────────────────
    # NOTE: id has no server default; it is ManagedTask.id, supplied by the app.
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("short_description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "continues_to_next",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "acceptance_criteria",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "dependencies",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "merged_from",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("jira_issue_key", sa.Text(), nullable=True),
        sa.Column("jira_issue_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_run", "tasks", ["run_id"])
    op.create_index("ix_tasks_user", "tasks", ["user_id"])

    # ── coverage_reports ───────────────────────────────────────────────────────
    op.create_table(
        "coverage_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column(
            "report", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cov_run", "coverage_reports", ["run_id"])
    # Row-level isolation index on the tenant key (additive to §3.1).
    op.create_index("ix_cov_user", "coverage_reports", ["user_id"])

    # ── audit_log ──────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "llm_tokens_used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "llm_model", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_run", "audit_log", ["run_id", "id"])
    # Row-level isolation index on the tenant key (additive to §3.1).
    op.create_index("ix_audit_user", "audit_log", ["user_id"])


def downgrade() -> None:
    # Reverse order of creation so FK dependencies drop cleanly.
    op.drop_index("ix_audit_user", table_name="audit_log")
    op.drop_index("ix_audit_run", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_cov_user", table_name="coverage_reports")
    op.drop_index("ix_cov_run", table_name="coverage_reports")
    op.drop_table("coverage_reports")

    op.drop_index("ix_tasks_user", table_name="tasks")
    op.drop_index("ix_tasks_run", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_runs_user", table_name="runs")
    op.drop_table("runs")

    op.drop_index("ix_creds_user", table_name="user_credentials")
    op.drop_table("user_credentials")

    op.drop_index("ix_sessions_user", table_name="sessions")
    op.drop_table("sessions")

    op.drop_table("users")

    # Extensions are left in place: they may be shared and CREATE used
    # IF NOT EXISTS. Dropping them on downgrade risks breaking other objects.
