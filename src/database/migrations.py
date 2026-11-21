"""Database schema migration manager and version tracker."""
import sqlite3
from datetime import datetime, timezone
from typing import List, Tuple
from src.database.connection import DatabaseManager
from src.core.logger import get_logger

logger = get_logger("studio.database.migrations")

# Ordered list of registered migrations: (version, name, up_sql)
MIGRATIONS: List[Tuple[int, str, str]] = [
    (
        1,
        "001_initial_schema",
        """
        -- Schema migrations history table
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        -- Channels Table (ADR-005: Multi-Channel Logical Architecture)
        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            niche TEXT NOT NULL,
            default_language TEXT NOT NULL DEFAULT 'en',
            supported_locales TEXT NOT NULL DEFAULT '["en"]',
            coppa_default TEXT NOT NULL DEFAULT 'not_made_for_kids',
            synthetic_media_declaration INTEGER NOT NULL DEFAULT 1,
            watermark_path TEXT,
            watermark_position TEXT DEFAULT 'top_right',
            primary_font TEXT,
            primary_color TEXT,
            default_aspect_ratio TEXT NOT NULL DEFAULT '9:16',
            default_template_id TEXT,
            default_tts_voice_id TEXT,
            default_generation_mode TEXT NOT NULL DEFAULT 'image_to_video',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_channels_handle ON channels(handle);
        CREATE INDEX IF NOT EXISTS idx_channels_active ON channels(is_active);

        -- Baseline Projects Table (to anchor channel scoping & foreign keys)
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            aspect_ratio TEXT NOT NULL DEFAULT '9:16',
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_projects_channel_id ON projects(channel_id);
        CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
        """,
    ),
    (
        2,
        "002_task_3_4_schema",
        """
        -- Enhance projects table with description, target duration, is_archived, metadata_json
        ALTER TABLE projects ADD COLUMN description TEXT DEFAULT '';
        ALTER TABLE projects ADD COLUMN target_duration_sec REAL DEFAULT 60.0;
        ALTER TABLE projects ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE projects ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';

        CREATE INDEX IF NOT EXISTS idx_projects_archived ON projects(is_archived);

        -- Scenes Table (Scene Management)
        CREATE TABLE IF NOT EXISTS scenes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            sequence_number INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            narration_text TEXT NOT NULL DEFAULT '',
            visual_prompt TEXT NOT NULL DEFAULT '',
            duration_seconds REAL NOT NULL DEFAULT 5.0,
            scene_type TEXT NOT NULL DEFAULT 'standard',
            status TEXT NOT NULL DEFAULT 'draft',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (project_id, sequence_number)
        );

        CREATE INDEX IF NOT EXISTS idx_scenes_project_id ON scenes(project_id);
        CREATE INDEX IF NOT EXISTS idx_scenes_channel_id ON scenes(channel_id);
        CREATE INDEX IF NOT EXISTS idx_scenes_seq ON scenes(project_id, sequence_number);

        -- Media Assets Table (Central MAM - media_vault)
        CREATE TABLE IF NOT EXISTS media_assets (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
            scene_id TEXT REFERENCES scenes(id) ON DELETE SET NULL,
            character_id TEXT,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            mime_type TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            asset_category TEXT NOT NULL DEFAULT 'general',
            is_active INTEGER NOT NULL DEFAULT 1,
            is_archived INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_assets_channel ON media_assets(channel_id);
        CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON media_assets(channel_id, file_sha256);
        CREATE INDEX IF NOT EXISTS idx_assets_type ON media_assets(channel_id, asset_type);
        CREATE INDEX IF NOT EXISTS idx_assets_project ON media_assets(project_id);
        CREATE INDEX IF NOT EXISTS idx_assets_scene ON media_assets(scene_id);

        -- Asset Licenses Table (License Management & Export Gate)
        CREATE TABLE IF NOT EXISTS asset_licenses (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL UNIQUE REFERENCES media_assets(id) ON DELETE CASCADE,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            legal_basis TEXT NOT NULL,
            verification_status TEXT NOT NULL DEFAULT 'pending_review',
            commercial_use_allowed INTEGER NOT NULL DEFAULT 0,
            attribution_required INTEGER NOT NULL DEFAULT 0,
            attribution_text TEXT,
            source_uri TEXT,
            rights_owner TEXT,
            proof_file_path TEXT,
            proof_sha256 TEXT,
            notes TEXT,
            verified_at TEXT,
            verified_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_licenses_asset ON asset_licenses(asset_id);
        CREATE INDEX IF NOT EXISTS idx_licenses_channel ON asset_licenses(channel_id);
        CREATE INDEX IF NOT EXISTS idx_licenses_status ON asset_licenses(verification_status);

        -- File Operations Table (Crash Recovery Journal)
        CREATE TABLE IF NOT EXISTS file_operations (
            id TEXT PRIMARY KEY,
            operation_type TEXT NOT NULL,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            asset_id TEXT,
            source_path TEXT,
            temp_path TEXT NOT NULL,
            target_path TEXT NOT NULL,
            file_sha256 TEXT,
            file_size_bytes INTEGER,
            state TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_file_ops_state ON file_operations(state);
        CREATE INDEX IF NOT EXISTS idx_file_ops_channel ON file_operations(channel_id);

        -- Audit Chain State Table (Anchoring latest trusted hash)
        CREATE TABLE IF NOT EXISTS audit_chain_state (
            channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
            last_seq INTEGER NOT NULL DEFAULT 0,
            latest_audit_hash TEXT NOT NULL DEFAULT '0000000000000000000000000000000000000000000000000000000000000000',
            is_locked INTEGER NOT NULL DEFAULT 0,
            lock_reason TEXT,
            updated_at TEXT NOT NULL
        );

        -- Audit Events Outbox Table (Transactional Outbox Pattern)
        CREATE TABLE IF NOT EXISTS audit_events_outbox (
            seq INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'system',
            payload_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            file_flushed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (channel_id, seq),
            UNIQUE (channel_id, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_outbox_unflushed ON audit_events_outbox(channel_id, file_flushed) WHERE file_flushed = 0;
        """,
    ),
    (
        3,
        "003_task_queue_schema",
        """
        -- Enhance channels table with last_task_dispatched_at for fair scheduling
        ALTER TABLE channels ADD COLUMN last_task_dispatched_at TEXT;

        -- 1. Tasks Table (Persistent Queue & State Machine)
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
            scene_id TEXT REFERENCES scenes(id) ON DELETE SET NULL,
            task_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            priority INTEGER NOT NULL DEFAULT 10,
            dependencies_json TEXT NOT NULL DEFAULT '[]',
            payload_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            error_message TEXT,
            error_details_json TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            scheduled_at TEXT NOT NULL,
            claimed_at TEXT,
            heartbeat_at TEXT,
            lease_expires_at TEXT,
            execution_token INTEGER NOT NULL DEFAULT 0,
            worker_id TEXT,
            current_step TEXT,
            total_steps INTEGER DEFAULT 1,
            completed_steps INTEGER DEFAULT 0,
            external_job_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (channel_id, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_dequeue ON tasks(status, scheduled_at, priority DESC, created_at ASC);
        CREATE INDEX IF NOT EXISTS idx_tasks_channel_status ON tasks(channel_id, status);
        CREATE INDEX IF NOT EXISTS idx_tasks_reaper ON tasks(status, lease_expires_at) WHERE status = 'running';
        CREATE INDEX IF NOT EXISTS idx_tasks_pending_deps ON tasks(status, channel_id) WHERE status = 'pending';
        CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);

        -- 2. Active Channel Leases Table (Relational Enforcement of max_concurrent_per_channel = 1)
        CREATE TABLE IF NOT EXISTS active_channel_leases (
            channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
            worker_id TEXT NOT NULL,
            execution_token INTEGER NOT NULL,
            lease_expires_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_leases_expires ON active_channel_leases(lease_expires_at);

        -- 3. Task Checkpoints Table (Step-Level Resumption with Token Guard)
        CREATE TABLE IF NOT EXISTS task_checkpoints (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            step_name TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            step_data_json TEXT NOT NULL DEFAULT '{}',
            execution_token INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (task_id, step_name)
        );

        CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON task_checkpoints(task_id, step_index ASC);
        """,
    ),
    (
        4,
        "004_task_3_9_storage_schema",
        """
        -- Add storage_provider, storage_key, and license_reference to media_assets (Task 3.9)
        ALTER TABLE media_assets ADD COLUMN storage_provider TEXT NOT NULL DEFAULT 'local';
        ALTER TABLE media_assets ADD COLUMN storage_key TEXT DEFAULT '';
        ALTER TABLE media_assets ADD COLUMN license_reference TEXT DEFAULT '';

        -- Backfill existing asset records: populate canonical storage_key
        UPDATE media_assets 
        SET storage_key = CASE 
            WHEN file_path LIKE 'channels/%' THEN file_path 
            ELSE 'channels/' || channel_id || '/' || file_path 
        END
        WHERE storage_key = '' OR storage_key IS NULL OR storage_key NOT LIKE 'channels/%';

        CREATE INDEX IF NOT EXISTS idx_assets_storage_key ON media_assets(channel_id, storage_key);
        """,
    ),
    (
        5,
        "005_task_3_11_flow_prompts",
        """
        -- Scene Prompts Table with explicit versioning and Google Flow generation instructions (Task 3.11)
        CREATE TABLE IF NOT EXISTS scene_prompts (
            id TEXT PRIMARY KEY,
            scene_id TEXT NOT NULL,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            generation_mode TEXT NOT NULL DEFAULT 'text_to_video',
            master_prompt TEXT NOT NULL DEFAULT '',
            visual_prompt TEXT NOT NULL,
            negative_prompt TEXT DEFAULT '',
            camera_instructions TEXT DEFAULT '',
            lighting_instructions TEXT DEFAULT '',
            motion_instructions TEXT DEFAULT '',
            continuity_instructions TEXT DEFAULT '',
            generation_specs_json TEXT NOT NULL DEFAULT '{}',
            character_refs_json TEXT NOT NULL DEFAULT '[]',
            style_refs_json TEXT NOT NULL DEFAULT '[]',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (scene_id, version)
        );

        CREATE INDEX IF NOT EXISTS idx_scene_prompts_scene_ver ON scene_prompts(scene_id, version DESC);
        CREATE INDEX IF NOT EXISTS idx_scene_prompts_project ON scene_prompts(project_id);

        -- Flow Prompt Packages Table: Records manifest metadata & storage keys (Task 3.11)
        CREATE TABLE IF NOT EXISTS prompt_packages (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL,
            package_version INTEGER NOT NULL DEFAULT 1,
            title TEXT NOT NULL,
            flow_project_name TEXT DEFAULT '',
            flow_workspace_ref TEXT DEFAULT '',
            content_hash TEXT NOT NULL,
            json_storage_key TEXT NOT NULL,
            markdown_storage_key TEXT NOT NULL,
            scene_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE (project_id, package_version)
        );

        CREATE INDEX IF NOT EXISTS idx_prompt_packages_project ON prompt_packages(project_id, package_version DESC);
        CREATE INDEX IF NOT EXISTS idx_prompt_packages_channel ON prompt_packages(channel_id);
        """,
    ),
    (
        6,
        "006_task_3_13_flow_inbound",
        """
        -- Flow Scene Takes & Versioning Table (Task 3.13)
        CREATE TABLE IF NOT EXISTS flow_scene_takes (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            scene_id TEXT NOT NULL,
            prompt_version INTEGER NOT NULL DEFAULT 1,
            take_number INTEGER NOT NULL DEFAULT 1,
            asset_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
            asset_role TEXT NOT NULL DEFAULT 'video',
            file_sha256 TEXT NOT NULL,
            source_file_name TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            duration_sec REAL,
            width INTEGER,
            height INTEGER,
            fps REAL,
            is_active INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE (project_id, scene_id, take_number, asset_role)
        );

        CREATE INDEX IF NOT EXISTS idx_flow_takes_scene ON flow_scene_takes(project_id, scene_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_flow_takes_asset ON flow_scene_takes(asset_id);
        CREATE INDEX IF NOT EXISTS idx_flow_takes_sha ON flow_scene_takes(channel_id, file_sha256);

        -- Flow Inbound Ingestion Records Table (Task 3.13)
        CREATE TABLE IF NOT EXISTS flow_inbound_records (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            package_id TEXT,
            source_type TEXT NOT NULL,
            source_identifier TEXT NOT NULL,
            total_inspected INTEGER NOT NULL DEFAULT 0,
            imported_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            report_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_flow_inbound_proj ON flow_inbound_records(project_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_flow_inbound_chan ON flow_inbound_records(channel_id);
        """,
    ),
]


class MigrationManager:
    """Applies and tracks deterministic database migrations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def get_applied_versions(self) -> List[int]:
        """Query list of applied migration versions."""
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            table_check = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations';"
            ).fetchone()
            if not table_check:
                return []
            rows = cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;").fetchall()
            return [row["version"] for row in rows]

    def run_migrations(self) -> List[int]:
        """Execute all pending migrations in sequential order.

        Returns:
            List[int]: List of newly applied migration versions.
        """
        applied_versions = set(self.get_applied_versions())
        newly_applied: List[int] = []

        for version, name, sql in MIGRATIONS:
            if version in applied_versions:
                continue

            logger.info(f"Applying database migration {version}: {name}")
            conn = self.db_manager.get_raw_connection()
            try:
                conn.executescript(sql)
                now_utc = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?);",
                    (version, name, now_utc),
                )
                conn.commit()
                newly_applied.append(version)
            finally:
                conn.close()

        if newly_applied:
            logger.info(f"Successfully applied {len(newly_applied)} migration(s): {newly_applied}")
        else:
            logger.debug("Database schema is up to date.")

        return newly_applied

    # Convenient alias
    apply_all_migrations = run_migrations
