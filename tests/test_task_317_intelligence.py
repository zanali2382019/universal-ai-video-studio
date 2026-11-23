"""Test Suite for Task 3.17: Production Intelligence & Resource Tracking.

Validates:
1. AST isolation: Zero direct sqlite3 imports in src/web/.
2. Database Schema Migration 9: flow_credit_ledger and production_cost_configurations.
3. Intelligence Repository: Credit transactions, sequential balances, cost configuration.
4. Local Resource Monitoring: Storage footprints by category/project, abstracted host metrics.
5. Production Cost Intelligence: Separation of Known Cost ($0.00) vs Estimated Cost via unit rates.
6. Google Flow Manual Resource Tracking: Ledger operations, explicit manual attribution, audit events.
7. Batch & Render Performance Analytics: Render throughput, success rate, timing, retries.
8. Deterministic Insights Engine: 100% deterministic rule-based operational warnings and efficiency ratings.
9. Unified Production History: Chronological timeline of tasks, takes, packages, and credit events.
10. Safe Report Generation: JSON and CSV exports with strict Path Traversal protection.
11. WebUI Routes: GET /intelligence, POST /flow-credits, POST /cost-config, reports, api/summary.
12. CLI Integration: summary, credit, cost, and export subcommands.
"""

import ast
import csv
import json
import os
import time
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import (
    ChannelCreate,
    ProjectCreate,
    SceneCreate,
    MediaAssetCreate,
    AssetType,
    TaskCreate,
    TaskType,
    TaskStatus,
    FlowCreditTransactionCreate,
    ProductionCostConfigUpdate,
)
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.database.repositories.export_repository import ExportRepository
from src.database.repositories.audit_repository import AuditRepository
from src.database.repositories.intelligence_repository import IntelligenceRepository
from src.services.audit_service import AuditService
from src.services.intelligence_service import IntelligenceService, TaskResourceMonitor
from src.storage.service import StorageService
from src.web.app import create_app
from src.web.dependencies import reset_service_container
from src.cli.intelligence import main as cli_main


class TestTask317ProductionIntelligence(unittest.TestCase):
    """Comprehensive test suite for Task 3.17 Production Intelligence."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="task317_test_")
        self.base_path = Path(self.temp_dir)
        self.db_path = self.base_path / "test_intelligence.db"
        self.storage_dir = self.base_path / "storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.db_mgr = DatabaseManager(db_path=self.db_path)
        self.migrator = MigrationManager(self.db_mgr)
        self.migrator.run_migrations()

        self.storage_service = StorageService(local_base_dir=self.storage_dir)
        self.channel_repo = ChannelRepository(self.db_mgr)
        self.project_repo = ProjectRepository(self.db_mgr)
        self.scene_repo = SceneRepository(self.db_mgr)
        self.asset_repo = AssetRepository(self.db_mgr)
        self.task_repo = TaskRepository(self.db_mgr)
        self.flow_repo = FlowInboundRepository(self.db_mgr)
        self.export_repo = ExportRepository(self.db_mgr)
        self.audit_repo = AuditRepository(self.db_mgr)
        self.audit_service = AuditService(self.audit_repo)
        self.intel_repo = IntelligenceRepository(self.db_mgr)

        self.service = IntelligenceService(
            db_manager=self.db_mgr,
            storage_service=self.storage_service,
            intelligence_repository=self.intel_repo,
            channel_repository=self.channel_repo,
            project_repository=self.project_repo,
            task_repository=self.task_repo,
            flow_inbound_repo=self.flow_repo,
            export_repository=self.export_repo,
            asset_repository=self.asset_repo,
            scene_repository=self.scene_repo,
            audit_service=self.audit_service,
        )

        # Seed sample channel
        self.channel = self.channel_repo.create(
            ChannelCreate(id="ch_intel_test", name="Intel Channel", handle="@intel_test", niche="tech")
        )
        self.project = self.project_repo.create(
            ProjectCreate(id="proj_intel_01", channel_id=self.channel.id, title="Intel Episode 01")
        )

    def tearDown(self):
        reset_service_container()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Architectural AST Scan
    # -------------------------------------------------------------------------
    def test_01_ast_zero_sqlite3_in_src_web(self):
        """Verify src/web/ contains strictly ZERO direct sqlite3 imports."""
        web_dir = Path(__file__).resolve().parent.parent / "src" / "web"
        self.assertTrue(web_dir.exists())

        violations = []
        for py_file in web_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "sqlite" in alias.name.lower():
                            violations.append((py_file.name, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "sqlite" in node.module.lower():
                        violations.append((py_file.name, node.module))

        self.assertEqual(violations, [], f"AST Isolation Failure in src/web: {violations}")

    # -------------------------------------------------------------------------
    # 2. Schema Migration 9 Verification
    # -------------------------------------------------------------------------
    def test_02_schema_migration_9_tables_and_indexes(self):
        """Verify Migration 9 creates flow_credit_ledger and cost configuration tables."""
        applied = self.migrator.get_applied_versions()
        self.assertIn(9, applied)

        with self.db_mgr.connection() as conn:
            cursor = conn.cursor()
            tables = [
                row["name"]
                for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            ]
            self.assertIn("flow_credit_ledger", tables)
            self.assertIn("production_cost_configurations", tables)

            # Check indexes
            indexes = [
                row["name"]
                for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()
            ]
            self.assertIn("idx_flow_credits_chan", indexes)
            self.assertIn("idx_cost_config_chan", indexes)

    # -------------------------------------------------------------------------
    # 3. Intelligence Repository Operations
    # -------------------------------------------------------------------------
    def test_03_intelligence_repository_ledger_and_costs(self):
        """Verify credit ledger math and cost configuration persistence."""
        # Initial balance must be 0.0
        self.assertEqual(self.intel_repo.get_credit_balance(self.channel.id), 0.0)

        # Record topup
        tx1 = self.intel_repo.record_credit_transaction(
            FlowCreditTransactionCreate(
                channel_id=self.channel.id,
                transaction_type="topup",
                credits_amount=150.0,
                operator_note="Monthly grant",
            )
        )
        self.assertEqual(tx1.balance_after, 150.0)
        self.assertEqual(self.intel_repo.get_credit_balance(self.channel.id), 150.0)

        # Record usage
        tx2 = self.intel_repo.record_credit_transaction(
            FlowCreditTransactionCreate(
                channel_id=self.channel.id,
                transaction_type="usage",
                credits_amount=-25.5,
                operator_note="Generated 2 scenes",
            )
        )
        self.assertEqual(tx2.balance_after, 124.5)
        self.assertEqual(self.intel_repo.get_credit_balance(self.channel.id), 124.5)

        # Cost config default & update
        cfg = self.intel_repo.get_cost_config(self.channel.id)
        self.assertEqual(cfg.currency, "USD")
        self.assertEqual(cfg.cost_per_render_second, 0.0)

        updated_cfg = self.intel_repo.set_cost_config(
            self.channel.id,
            ProductionCostConfigUpdate(
                cost_per_render_second=0.005,
                cost_per_storage_gb_month=0.02,
                cost_per_api_take=0.10,
                currency="EUR",
            ),
        )
        self.assertEqual(updated_cfg.currency, "EUR")
        self.assertEqual(updated_cfg.cost_per_render_second, 0.005)
        self.assertEqual(updated_cfg.cost_per_storage_gb_month, 0.02)
        self.assertEqual(updated_cfg.cost_per_api_take, 0.10)

    # -------------------------------------------------------------------------
    # 4. Local Resource Monitoring (Storage & System Telemetry)
    # -------------------------------------------------------------------------
    def test_04_storage_footprint_and_system_metrics_isolation(self):
        """Verify storage categorizations, project footprints, and safe host metrics."""
        # Seed media assets
        dummy_vid = self.base_path / "video1.mp4"
        dummy_vid.write_bytes(b"V" * 1024 * 1024)  # 1 MB
        dummy_aud = self.base_path / "audio1.aac"
        dummy_aud.write_bytes(b"A" * 512 * 1024)   # 512 KB

        self.asset_repo.create(
            MediaAssetCreate(
                id="ast_v1",
                channel_id=self.channel.id,
                project_id=self.project.id,
                file_name="video1.mp4",
                file_path=str(dummy_vid),
                file_sha256="V" * 64,
                file_size_bytes=dummy_vid.stat().st_size,
                mime_type="video/mp4",
                asset_type=AssetType.VIDEO,
            )
        )
        self.asset_repo.create(
            MediaAssetCreate(
                id="ast_a1",
                channel_id=self.channel.id,
                project_id=self.project.id,
                file_name="audio1.aac",
                file_path=str(dummy_aud),
                file_sha256="A" * 64,
                file_size_bytes=dummy_aud.stat().st_size,
                mime_type="audio/aac",
                asset_type=AssetType.AUDIO,
            )
        )

        storage_data = self.service.get_channel_storage_breakdown(self.channel.id)
        self.assertGreaterEqual(storage_data["total_bytes"], 1.5 * 1024 * 1024)
        self.assertEqual(storage_data["by_category"]["video"]["bytes"], 1024 * 1024)
        self.assertEqual(storage_data["by_category"]["audio"]["bytes"], 512 * 1024)
        self.assertEqual(len(storage_data["by_project"]), 1)
        self.assertEqual(storage_data["by_project"][0]["project_id"], self.project.id)

        # Host telemetry - must NOT leak sensitive path strings
        sys_metrics = self.service.get_system_resources()
        self.assertGreaterEqual(sys_metrics["cpu_cores"], 1)
        self.assertIn("percent_used", sys_metrics["disk"])
        self.assertIn("free_human", sys_metrics["disk"])
        # String representation must not expose absolute user path
        self.assertNotIn(str(Path.home()), str(sys_metrics))

    # -------------------------------------------------------------------------
    # 5. Production Cost Intelligence (Known vs Estimated)
    # -------------------------------------------------------------------------
    def test_05_cost_intelligence_separation_and_unit_rates(self):
        """Verify strict separation between Known Cost ($0.00 default) and Estimated Cost."""
        # Set unit rates
        self.service.update_cost_config(
            self.channel.id,
            ProductionCostConfigUpdate(
                cost_per_render_second=0.01,
                cost_per_storage_gb_month=0.05,
                cost_per_api_take=0.20,
                currency="USD",
            ),
        )

        # Seed completed render task with 60s duration
        t_cost = self.task_repo.create_task(
            TaskCreate(
                channel_id=self.channel.id,
                project_id=self.project.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="t_cost_01",
                status=TaskStatus.QUEUED,
            )
        )
        self.task_repo.update_task_status(
            t_cost.id,
            TaskStatus.COMPLETED,
            execution_token=t_cost.execution_token,
            result={"duration_sec": 60.0, "file_size_bytes": 1000},
        )

        cost_res = self.service.calculate_costs(self.channel.id)
        # Known Cost must strictly remain 0.00
        self.assertEqual(cost_res["actual_known_cost"], 0.00)
        self.assertIn("0.00 USD (Known / Default)", cost_res["actual_cost_label"])

        # Estimated render cost = 60s * 0.01 = 0.60 USD
        self.assertGreaterEqual(cost_res["sub_costs"]["render_cost"], 0.60)
        self.assertGreater(cost_res["estimated_cost"], 0.00)

    # -------------------------------------------------------------------------
    # 6. Google Flow Manual Resource Tracking & Audit Log
    # -------------------------------------------------------------------------
    def test_06_flow_credit_ledger_attribution_and_audit(self):
        """Verify explicit 'manual_user_entry' attribution and hash-chained audit logging."""
        tx = self.service.record_flow_credit_entry(
            self.channel.id,
            FlowCreditTransactionCreate(
                channel_id=self.channel.id,
                transaction_type="topup",
                credits_amount=100.0,
                operator_note="Initial studio budget",
            ),
        )
        self.assertEqual(tx.source, "manual_user_entry")
        self.assertEqual(tx.balance_after, 100.0)

        summary = self.service.get_flow_credit_summary(self.channel.id)
        self.assertEqual(summary["current_balance"], 100.0)
        self.assertIn("Manual User Entry", summary["attribution"])
        self.assertEqual(len(summary["transactions"]), 1)

        # Verify audit outbox event was recorded
        outbox_events = self.audit_repo.get_unflushed_events(self.channel.id)
        # Since drain_outbox was called, check chain state or audit events
        chain = self.audit_repo.get_or_init_chain_state(self.channel.id)
        self.assertGreaterEqual(chain.last_seq, 1)

    # -------------------------------------------------------------------------
    # 7. Batch & Render Performance Analytics
    # -------------------------------------------------------------------------
    def test_07_render_analytics_and_unmeasured_duration_handling(self):
        """Verify analytics properly calculates success rate and flags unmeasured timing."""
        # Empty channel: must report 'Not Recorded' rather than fake 0 duration
        empty_analytics = self.service.get_render_analytics("ch_empty_test")
        self.assertEqual(empty_analytics["total_renders"], 0)
        self.assertEqual(empty_analytics["timing"]["avg_formatted"], "Not Recorded")
        self.assertIsNone(empty_analytics["timing"]["avg_seconds"])

        # Seed 1 completed task (15s) and 1 failed task
        t_pass = self.task_repo.create_task(
            TaskCreate(
                channel_id=self.channel.id,
                project_id=self.project.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="t_render_pass",
                status=TaskStatus.QUEUED,
            )
        )
        self.task_repo.update_task_status(
            t_pass.id,
            TaskStatus.COMPLETED,
            execution_token=t_pass.execution_token,
            result={"duration_sec": 15.0},
        )
        self.task_repo.create_task(
            TaskCreate(
                channel_id=self.channel.id,
                project_id=self.project.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="t_render_fail",
                status=TaskStatus.FAILED,
                error_message="Simulation failure",
            )
        )

        analytics = self.service.get_render_analytics(self.channel.id)
        self.assertEqual(analytics["total_renders"], 2)
        self.assertEqual(analytics["completed_count"], 1)
        self.assertEqual(analytics["failed_count"], 1)
        self.assertEqual(analytics["success_rate_percent"], 50.0)
        self.assertEqual(analytics["timing"]["avg_seconds"], 15.0)

    # -------------------------------------------------------------------------
    # 8. Deterministic Production Insights Engine
    # -------------------------------------------------------------------------
    def test_08_deterministic_insights_engine_rules(self):
        """Verify rule-based deterministic warnings for credit depletion and high failure rate."""
        # Balance = 0.0 -> must trigger 'flow_credits_exhausted'
        insights = self.service.generate_deterministic_insights(self.channel.id)
        rules = [i["rule"] for i in insights]
        self.assertIn("flow_credits_exhausted", rules)

        # Top up to 10.0 (< 15.0) -> must trigger 'flow_credits_low'
        self.service.record_flow_credit_entry(
            self.channel.id,
            FlowCreditTransactionCreate(
                channel_id=self.channel.id,
                transaction_type="topup",
                credits_amount=10.0,
            ),
        )
        insights2 = self.service.generate_deterministic_insights(self.channel.id)
        rules2 = [i["rule"] for i in insights2]
        self.assertIn("flow_credits_low", rules2)

    # -------------------------------------------------------------------------
    # 9. Unified Production Version History
    # -------------------------------------------------------------------------
    def test_09_unified_production_history_timeline(self):
        """Verify timeline chronological aggregation from tasks and credit ledger."""
        self.service.record_flow_credit_entry(
            self.channel.id,
            FlowCreditTransactionCreate(
                channel_id=self.channel.id,
                transaction_type="topup",
                credits_amount=50.0,
                operator_note="Budget allocation",
            ),
        )
        self.task_repo.create_task(
            TaskCreate(
                channel_id=self.channel.id,
                project_id=self.project.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="t_hist_01",
                status=TaskStatus.COMPLETED,
            )
        )

        history = self.service.get_unified_production_history(self.channel.id)
        self.assertGreaterEqual(len(history), 2)
        types = [h["type"] for h in history]
        self.assertIn("credit_ledger", types)
        self.assertIn("render_task", types)

    # -------------------------------------------------------------------------
    # 10. Safe Report Exports (JSON & CSV) & Path Traversal Prevention
    # -------------------------------------------------------------------------
    def test_10_safe_report_exports_and_path_traversal_check(self):
        """Verify valid JSON/CSV report serialization and zero Path Traversal vulnerability."""
        # JSON export
        json_filename, json_content = self.service.export_production_report(self.channel.id, "json")
        self.assertFalse("/" in json_filename or "\\" in json_filename)
        data = json.loads(json_content)
        self.assertIn("storage", data)
        self.assertIn("cost_intelligence", data)
        self.assertIn("flow_credits", data)

        # CSV export
        csv_filename, csv_content = self.service.export_production_report(self.channel.id, "csv")
        self.assertFalse("/" in csv_filename or "\\" in csv_filename)
        self.assertIn("PRODUCTION INTELLIGENCE REPORT", csv_content)
        self.assertIn("STORAGE FOOTPRINT", csv_content)

        # Path traversal attack simulation in channel_id
        evil_id = "../../etc/passwd"
        safe_fn, _ = self.service.export_production_report(evil_id, "json")
        self.assertNotIn("..", safe_fn)
        self.assertNotIn("/", safe_fn)
        self.assertNotIn("\\", safe_fn)

    # -------------------------------------------------------------------------
    # 11. WebUI Endpoints Integration
    # -------------------------------------------------------------------------
    def test_11_webui_intelligence_endpoints(self):
        """Test GET /intelligence, POST /flow-credits, POST /cost-config, and report downloads."""
        app = create_app(db_manager=self.db_mgr, storage_service=self.storage_service)
        app.state.service_container.render_service.stop_worker_pool()
        client = TestClient(app)

        # 1. GET /intelligence
        resp = client.get(f"/intelligence?channel_id={self.channel.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Production Intelligence", resp.text)
        self.assertIn("Channel Storage Footprint", resp.text)

        # 2. POST /intelligence/flow-credits (JSON accept)
        cred_payload = {
            "channel_id": self.channel.id,
            "credits_amount": 75.0,
            "transaction_type": "topup",
            "operator_note": "WebUI Test Grant",
        }
        resp_cred = client.post(
            "/intelligence/flow-credits", data=cred_payload, headers={"accept": "application/json"}
        )
        self.assertEqual(resp_cred.status_code, 200)
        self.assertEqual(resp_cred.json()["transaction"]["balance_after"], 75.0)

        # 3. POST /intelligence/cost-config
        cost_payload = {
            "channel_id": self.channel.id,
            "cost_per_render_second": 0.008,
            "cost_per_storage_gb_month": 0.025,
            "cost_per_api_take": 0.12,
            "currency": "USD",
        }
        resp_cost = client.post(
            "/intelligence/cost-config", data=cost_payload, headers={"accept": "application/json"}
        )
        self.assertEqual(resp_cost.status_code, 200)
        self.assertEqual(resp_cost.json()["config"]["cost_per_render_second"], 0.008)

        # 4. GET /intelligence/report/json
        resp_r_json = client.get(f"/intelligence/report/json?channel_id={self.channel.id}")
        self.assertEqual(resp_r_json.status_code, 200)
        self.assertEqual(resp_r_json.headers["content-type"], "application/json")
        self.assertIn("attachment", resp_r_json.headers["content-disposition"])

        # 5. GET /intelligence/report/csv
        resp_r_csv = client.get(f"/intelligence/report/csv?channel_id={self.channel.id}")
        self.assertEqual(resp_r_csv.status_code, 200)
        self.assertIn("text/csv", resp_r_csv.headers["content-type"])
        self.assertIn("STORAGE FOOTPRINT", resp_r_csv.text)

        # 6. GET /intelligence/api/summary
        resp_api = client.get(f"/intelligence/api/summary?channel_id={self.channel.id}")
        self.assertEqual(resp_api.status_code, 200)
        self.assertEqual(resp_api.json()["channel_id"], self.channel.id)

    # -------------------------------------------------------------------------
    # 12. CLI Integration
    # -------------------------------------------------------------------------
    def test_12_cli_commands_execution(self):
        """Test CLI summary, credit logging, cost update, and report export."""
        # Summary
        code_sum = cli_main(
            ["summary", "--channel", self.channel.id],
            db_manager=self.db_mgr,
            storage_service=self.storage_service,
        )
        self.assertEqual(code_sum, 0)

        # Credit
        code_crd = cli_main(
            ["credit", "--channel", self.channel.id, "--amount", "50", "--type", "topup", "--note", "CLI Test"],
            db_manager=self.db_mgr,
            storage_service=self.storage_service,
        )
        self.assertEqual(code_crd, 0)

        # Cost
        code_cst = cli_main(
            [
                "cost", "--channel", self.channel.id,
                "--render-rate", "0.004", "--storage-rate", "0.01", "--take-rate", "0.05",
                "--currency", "USD"
            ],
            db_manager=self.db_mgr,
            storage_service=self.storage_service,
        )
        self.assertEqual(code_cst, 0)

        # Export
        out_f = self.base_path / "cli_test_report.json"
        code_exp = cli_main(
            ["export", "--channel", self.channel.id, "--format", "json", "--output", str(out_f)],
            db_manager=self.db_mgr,
            storage_service=self.storage_service,
        )
        self.assertEqual(code_exp, 0)
        self.assertTrue(out_f.is_file())
        self.assertGreater(out_f.stat().st_size, 0)

    def test_13_task_resource_monitor_lifecycle(self):
        """Test TaskResourceMonitor lifecycle: start, sample, link FFmpeg PID, stop, and clean up."""
        import subprocess, sys

        # Spawn a short child process to simulate FFmpeg
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
        monitor = TaskResourceMonitor(
            task_id="tsk_render_test_lifecycle",
            ffmpeg_pid=child.pid,
            sampling_interval_sec=0.05,
            active_workers=2,
        )

        monitor.start()
        self.assertTrue(monitor._is_running)
        self.assertIsNotNone(monitor._thread)
        self.assertTrue(monitor._thread.is_alive())

        # Wait for child to exit
        child.wait(timeout=2.0)
        time.sleep(0.1)

        summary = monitor.stop()
        self.assertFalse(monitor._is_running)
        self.assertFalse(monitor._thread.is_alive())

        self.assertEqual(summary["task_id"], "tsk_render_test_lifecycle")
        self.assertEqual(summary["ffmpeg_pid"], child.pid)
        self.assertEqual(summary["active_workers"], 2)
        self.assertEqual(summary["lifecycle_status"], "stopped")
        self.assertGreaterEqual(summary["sample_count"], 2)
        self.assertGreaterEqual(summary["render_duration_sec"], 0.2)
        self.assertGreaterEqual(summary["ram_usage_avg_mb"], 1.0)
        self.assertGreaterEqual(summary["ram_usage_peak_mb"], summary["ram_usage_avg_mb"])
        self.assertGreaterEqual(summary["cpu_usage_avg_percent"], 0.0)

    def test_14_production_cost_accuracy_and_energy_rules(self):
        """Test strict cost rules: N/A for unconfigured electricity, formula accuracy, and no double counting."""
        # 1. Unconfigured electricity
        unconf_summary = self.service.get_cost_summary(self.channel.id)
        self.assertEqual(unconf_summary["energy_cost"]["amount"], "N/A")
        self.assertEqual(unconf_summary["energy_cost"]["status"], "Not Configured")
        self.assertIn("N/A", unconf_summary["energy_cost"]["label"])
        self.assertEqual(unconf_summary["actual_known_cost"]["status"], "Manually Recorded")
        self.assertEqual(unconf_summary["actual_known_cost"]["amount"], 0.00)

        # 2. Configured power: 300 Watts, $0.15/kWh, with 15s measured render time
        # Record a completed render task with 15.0s duration
        task = self.task_repo.create_task(
            TaskCreate(
                channel_id=self.channel.id,
                project_id=self.project.id,
                task_type=TaskType.VIDEO_RENDERING,
                idempotency_key="energy_test_render_task",
            )
        )
        self.task_repo.update_task_status(
            task.id,
            TaskStatus.COMPLETED,
            execution_token=task.execution_token,
            result={"duration_sec": 15.0},
        )

        conf_summary = self.service.get_cost_summary(
            self.channel.id,
            power_consumption_watts=300.0,
            electricity_price_kwh=0.15,
        )

        # Verify energy calculation: (300W * 15s) / 3,600,000 = 0.00125 kWh
        # Cost = 0.00125 kWh * $0.15 = 0.0001875 USD -> 0.0002 USD
        self.assertEqual(conf_summary["energy_cost"]["status"], "Estimated")
        self.assertAlmostEqual(conf_summary["energy_cost"]["energy_consumed_kwh"], 0.00125, places=5)
        self.assertAlmostEqual(conf_summary["energy_cost"]["amount"], 0.0002, places=4)
        self.assertIn("Estimated", conf_summary["energy_cost"]["label"])

        # Check anti-double-counting guarantee
        self.assertIn("independent line items", conf_summary["anti_double_counting_guarantee"])

    def test_15_markdown_report_verification_and_total_matching(self):
        """Test Markdown report generation, Path Traversal defense, and 100% total matching across JSON/CSV/MD."""
        # Export all 3 formats
        fn_json, content_json = self.service.export_production_report(self.channel.id, "json")
        fn_csv, content_csv = self.service.export_production_report(self.channel.id, "csv")
        fn_md, content_md = self.service.export_production_report(self.channel.id, "markdown")

        # 1. Filenames valid
        self.assertTrue(fn_json.endswith(".json"))
        self.assertTrue(fn_csv.endswith(".csv"))
        self.assertTrue(fn_md.endswith(".md"))

        # 2. Parse JSON
        data_json = json.loads(content_json)
        storage_bytes_json = data_json["storage"]["total_bytes"]
        renders_total_json = data_json["render_analytics"]["total_renders"]
        estimated_cost_json = data_json["cost_intelligence"]["estimated_cost"]
        flow_balance_json = data_json["flow_credits"]["current_balance"]

        # 3. Verify totals in CSV
        self.assertIn(str(storage_bytes_json), content_csv)
        self.assertIn(str(renders_total_json), content_csv)
        self.assertIn(f"{estimated_cost_json:.2f}", content_csv)
        self.assertIn(str(flow_balance_json), content_csv)

        # 4. Verify totals in Markdown
        self.assertIn(str(storage_bytes_json), content_md)
        self.assertIn(str(renders_total_json), content_md)
        self.assertIn(f"{estimated_cost_json:.2f}", content_md)
        self.assertIn(str(flow_balance_json), content_md)
        self.assertIn("# تقرير ذكاء الإنتاج والموارد", content_md)

        # 5. Path Traversal Defense
        fn_escape, _ = self.service.export_production_report("../../etc/passwd", "markdown")
        self.assertNotIn("..", fn_escape)
        self.assertNotIn("/", fn_escape)
        self.assertNotIn("\\", fn_escape)

        # 6. Zero absolute Windows drive paths in Markdown report
        self.assertNotIn("C:\\", content_md)
        self.assertNotIn("E:\\", content_md)


if __name__ == "__main__":
    unittest.main()
