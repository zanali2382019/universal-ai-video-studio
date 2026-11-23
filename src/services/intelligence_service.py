"""Production Intelligence & Resource Tracking Domain Service (Task 3.17).

Provides:
1. Local Resource Monitoring (channel & project storage breakdown, sanitized system metrics).
2. Production Cost Intelligence (Known $0.00 actual vs estimated costs from unit rates).
3. Google Flow Manual Resource Tracking (credit ledger with explicit manual attribution).
4. Batch & Render Performance Analytics (measured execution times, retry rates, failure rates).
5. Deterministic Production Insights Engine (100% deterministic rule-based insights).
6. Unified Production Version History (chronological timeline of renders, takes, packages, audits).
7. Safe Report Generation (JSON & CSV exports with Path Traversal protection).
"""

import os
import re
import sys
import time
import threading
import csv
import io
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from src.database.connection import DatabaseManager
from src.database.models import (
    FlowCreditTransactionModel,
    FlowCreditTransactionCreate,
    ProductionCostConfigModel,
    ProductionCostConfigUpdate,
    TaskStatus,
    TaskType,
)
from src.database.repositories.intelligence_repository import IntelligenceRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.task_repository import TaskRepository
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.database.repositories.export_repository import ExportRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.audit_repository import AuditRepository
from src.storage.service import StorageService
from src.services.audit_service import AuditService
from src.core.config import get_settings
from src.core.exceptions import ValidationException
from src.core.logger import get_logger

logger = get_logger("studio.services.intelligence")


def format_bytes(size_bytes: int) -> str:
    """Format bytes into human-readable string (B, KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.2f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def format_duration(seconds: Optional[float]) -> str:
    """Format seconds into human-readable duration (MM:SS or 'Not Recorded')."""
    if seconds is None:
        return "Not Recorded"
    secs = int(round(seconds))
    mins, rem_secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{rem_secs:02d}"
    return f"{mins:02d}:{rem_secs:02d}"


class TaskResourceMonitor:
    """Thread-safe resource sampler for studio render tasks and child FFmpeg processes.

    Strictly measures Studio worker and FFmpeg processes tied to the render task.
    Does not monitor or inspect unrelated third-party system applications.
    """

    def __init__(
        self,
        task_id: str,
        ffmpeg_pid: Optional[int] = None,
        sampling_interval_sec: float = 0.1,
        active_workers: int = 1,
    ) -> None:
        self.task_id = str(task_id)
        self.ffmpeg_pid = ffmpeg_pid
        self.sampling_interval_sec = max(0.05, sampling_interval_sec)
        self.active_workers = active_workers

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._samples_cpu: List[float] = []
        self._samples_ram_mb: List[float] = []
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._is_running = False

    def start(self) -> None:
        """Start isolated background resource sampling."""
        if self._is_running:
            return
        self._stop_event.clear()
        self._is_running = True
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._sampling_loop,
            name=f"task_res_monitor_{self.task_id[:12]}",
            daemon=True,
        )
        self._thread.start()

    def set_ffmpeg_pid(self, pid: int) -> None:
        """Dynamically link child FFmpeg process ID when spawned."""
        self.ffmpeg_pid = pid

    def stop(self) -> Dict[str, Any]:
        """Stop sampling, join thread, release handles, and return measurement metrics."""
        if not self._is_running:
            return self.get_summary()

        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._end_time = time.time()
        self._is_running = False
        return self.get_summary()

    def _sample_process_memory_mb(self, pid: int) -> float:
        """Sample WorkingSetSize (RAM in MB) for given PID via Windows psapi or fallback."""
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t),
                    ]

                handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
                if handle:
                    try:
                        pmc = PROCESS_MEMORY_COUNTERS_EX()
                        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
                        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                            return pmc.WorkingSetSize / (1024 * 1024)
                    finally:
                        ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass
        return 0.0

    def _sample_process_cpu_time(self, pid: int) -> int:
        """Sample cumulative kernel + user CPU time (100ns units) for given PID."""
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class FILETIME(ctypes.Structure):
                    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

                handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
                if handle:
                    try:
                        c, e, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
                        if ctypes.windll.kernel32.GetProcessTimes(
                            handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)
                        ):
                            k_time = (k.dwHighDateTime << 32) | k.dwLowDateTime
                            u_time = (u.dwHighDateTime << 32) | u.dwLowDateTime
                            return k_time + u_time
                    finally:
                        ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass
        return 0

    def _sampling_loop(self) -> None:
        """Background sampling loop restricted strictly to Studio process and FFmpeg child PID."""
        studio_pid = os.getpid()
        num_cores = os.cpu_count() or 1

        prev_time = time.time()
        prev_cpu = self._sample_process_cpu_time(studio_pid)
        if self.ffmpeg_pid:
            prev_cpu += self._sample_process_cpu_time(self.ffmpeg_pid)

        while not self._stop_event.is_set():
            time.sleep(self.sampling_interval_sec)
            now = time.time()
            dt = now - prev_time

            # RAM for studio process + ffmpeg child PID
            ram_mb = self._sample_process_memory_mb(studio_pid)
            if self.ffmpeg_pid:
                ram_mb += self._sample_process_memory_mb(self.ffmpeg_pid)

            # CPU
            cur_cpu = self._sample_process_cpu_time(studio_pid)
            if self.ffmpeg_pid:
                cur_cpu += self._sample_process_cpu_time(self.ffmpeg_pid)

            delta_cpu = cur_cpu - prev_cpu
            prev_cpu = cur_cpu
            prev_time = now

            if dt > 0 and num_cores > 0:
                cpu_seconds = delta_cpu * 1e-7
                cpu_percent = min(100.0, max(0.0, (cpu_seconds / (dt * num_cores)) * 100.0))
            else:
                cpu_percent = 0.0

            self._samples_cpu.append(round(cpu_percent, 2))
            self._samples_ram_mb.append(round(ram_mb, 2))

    def get_summary(self) -> Dict[str, Any]:
        """Compute consolidated measurement metrics."""
        dur = 0.0
        if self._start_time:
            end = self._end_time or time.time()
            dur = max(0.0, end - self._start_time)

        avg_cpu = round(sum(self._samples_cpu) / len(self._samples_cpu), 2) if self._samples_cpu else 0.0
        peak_cpu = round(max(self._samples_cpu), 2) if self._samples_cpu else 0.0
        avg_ram = round(sum(self._samples_ram_mb) / len(self._samples_ram_mb), 2) if self._samples_ram_mb else 0.0
        peak_ram = round(max(self._samples_ram_mb), 2) if self._samples_ram_mb else 0.0

        return {
            "task_id": self.task_id,
            "ffmpeg_pid": self.ffmpeg_pid,
            "render_duration_sec": round(dur, 3),
            "sampling_interval_sec": self.sampling_interval_sec,
            "sample_count": len(self._samples_cpu),
            "cpu_usage_avg_percent": avg_cpu,
            "cpu_usage_peak_percent": peak_cpu,
            "ram_usage_avg_mb": avg_ram,
            "ram_usage_peak_mb": peak_ram,
            "active_workers": self.active_workers,
            "scope": "Studio Process & Child FFmpeg Subprocesses (Isolated)",
            "lifecycle_status": "stopped" if not self._is_running else "running",
        }

    def __enter__(self) -> "TaskResourceMonitor":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class IntelligenceService:
    """Production Intelligence, resource tracking, and cost analytics coordinator."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        storage_service: Optional[StorageService] = None,
        intelligence_repository: Optional[IntelligenceRepository] = None,
        channel_repository: Optional[ChannelRepository] = None,
        project_repository: Optional[ProjectRepository] = None,
        task_repository: Optional[TaskRepository] = None,
        flow_inbound_repo: Optional[FlowInboundRepository] = None,
        export_repository: Optional[ExportRepository] = None,
        asset_repository: Optional[AssetRepository] = None,
        scene_repository: Optional[SceneRepository] = None,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self.db = db_manager or DatabaseManager()
        self.storage = storage_service or StorageService()
        self.intel_repo = intelligence_repository or IntelligenceRepository(self.db)
        self.channel_repo = channel_repository or ChannelRepository(self.db)
        self.project_repo = project_repository or ProjectRepository(self.db)
        self.task_repo = task_repository or TaskRepository(self.db)
        self.flow_repo = flow_inbound_repo or FlowInboundRepository(self.db)
        self.export_repo = export_repository or ExportRepository(self.db)
        self.asset_repo = asset_repository or AssetRepository(self.db)
        self.scene_repo = scene_repository or SceneRepository(self.db)
        self.audit_service = audit_service or AuditService(AuditRepository(self.db))

    # -------------------------------------------------------------------------
    # 1. Local Resource Monitoring
    # -------------------------------------------------------------------------
    def get_channel_storage_breakdown(self, channel_id: str) -> Dict[str, Any]:
        """Calculate detailed storage footprint for a channel and its projects.

        Returns storage categorized by type and broken down per project.
        """
        clean_ch = channel_id.strip()
        settings = get_settings()

        # Categories
        breakdown_by_category = {
            "video": 0,
            "audio": 0,
            "image": 0,
            "subtitles": 0,
            "packages": 0,
            "other": 0,
        }

        # Scan database registered media assets
        assets = self.asset_repo.list_by_channel(clean_ch)
        project_storage: Dict[str, Dict[str, Any]] = {}

        # Fetch projects for titles
        projects = self.project_repo.list_by_channel(clean_ch)
        for p in projects:
            project_storage[p.id] = {
                "project_id": p.id,
                "title": p.title,
                "total_bytes": 0,
                "file_count": 0,
                "human_size": "0 B",
            }

        total_channel_bytes = 0

        for a in assets:
            size = a.file_size_bytes or 0
            total_channel_bytes += size
            mime = (a.mime_type or "").lower()
            a_type = (a.asset_type.value if hasattr(a.asset_type, "value") else str(a.asset_type)).lower()

            if "video" in mime or "video" in a_type:
                breakdown_by_category["video"] += size
            elif "audio" in mime or "audio" in a_type:
                breakdown_by_category["audio"] += size
            elif "image" in mime or "image" in a_type:
                breakdown_by_category["image"] += size
            elif "subrip" in mime or "vtt" in mime:
                breakdown_by_category["subtitles"] += size
            else:
                breakdown_by_category["other"] += size

            if a.project_id:
                if a.project_id not in project_storage:
                    project_storage[a.project_id] = {
                        "project_id": a.project_id,
                        "title": a.project_id,
                        "total_bytes": 0,
                        "file_count": 0,
                        "human_size": "0 B",
                    }
                project_storage[a.project_id]["total_bytes"] += size
                project_storage[a.project_id]["file_count"] += 1

        # Also inspect on-disk export packages
        packages = self.export_repo.list_by_channel(clean_ch)
        for pkg in packages:
            if pkg.zip_path:
                try:
                    p_zip = Path(pkg.zip_path)
                    if p_zip.is_file():
                        zip_sz = p_zip.stat().st_size
                        breakdown_by_category["packages"] += zip_sz
                        total_channel_bytes += zip_sz
                        if pkg.project_id in project_storage:
                            project_storage[pkg.project_id]["total_bytes"] += zip_sz
                            project_storage[pkg.project_id]["file_count"] += 1
                except Exception:
                    pass

        # Human size updates
        for p_id, p_info in project_storage.items():
            p_info["human_size"] = format_bytes(p_info["total_bytes"])

        return {
            "channel_id": clean_ch,
            "total_bytes": total_channel_bytes,
            "human_size": format_bytes(total_channel_bytes),
            "total_gb": round(total_channel_bytes / (1024 ** 3), 4),
            "by_category": {
                k: {
                    "bytes": v,
                    "human_size": format_bytes(v),
                    "percent": round((v / total_channel_bytes * 100), 1) if total_channel_bytes > 0 else 0.0,
                }
                for k, v in breakdown_by_category.items()
            },
            "by_project": list(project_storage.values()),
        }

    def get_system_resources(self) -> Dict[str, Any]:
        """Query host CPU, memory, and disk usage safely without leaking paths."""
        settings = get_settings()

        # 1. CPU cores
        cpu_count = os.cpu_count() or 1

        # 2. Disk usage (abstracted, never leak host path)
        try:
            total, used, free = shutil.disk_usage(settings.studio_data_dir)
            disk_percent = round((used / total) * 100, 1) if total > 0 else 0.0
            disk_info = {
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "total_human": format_bytes(total),
                "used_human": format_bytes(used),
                "free_human": format_bytes(free),
                "percent_used": disk_percent,
                "status": "warning" if disk_percent > 90 else "healthy",
            }
        except Exception:
            disk_info = {
                "total_bytes": 0,
                "used_bytes": 0,
                "free_bytes": 0,
                "total_human": "Unknown",
                "used_human": "Unknown",
                "free_human": "Unknown",
                "percent_used": 0.0,
                "status": "unknown",
            }

        # 3. RAM usage
        memory_info = {
            "percent_used": None,
            "total_human": "Standard",
            "available_human": "Available",
        }
        try:
            import psutil
            mem = psutil.virtual_memory()
            memory_info = {
                "percent_used": round(mem.percent, 1),
                "total_human": format_bytes(mem.total),
                "available_human": format_bytes(mem.available),
            }
        except ImportError:
            pass

        return {
            "cpu_cores": cpu_count,
            "disk": disk_info,
            "memory": memory_info,
        }

    # -------------------------------------------------------------------------
    # 2. Batch & Render Performance Analytics
    # -------------------------------------------------------------------------
    def get_render_analytics(self, channel_id: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate render throughput, execution times, retries, and success rates."""
        all_tasks = self.task_repo.list_tasks(channel_id=channel_id)
        render_tasks = [t for t in all_tasks if t.task_type == TaskType.VIDEO_RENDERING]

        total_tasks = len(render_tasks)
        completed_tasks = [t for t in render_tasks if t.status == TaskStatus.COMPLETED]
        failed_tasks = [t for t in render_tasks if t.status == TaskStatus.FAILED]
        running_tasks = [t for t in render_tasks if t.status == TaskStatus.RUNNING]
        queued_tasks = [t for t in render_tasks if t.status == TaskStatus.QUEUED]
        cancelled_tasks = [t for t in render_tasks if t.status == TaskStatus.CANCELLED]

        # Calculate success rate
        decided_count = len(completed_tasks) + len(failed_tasks)
        if decided_count > 0:
            success_rate = round((len(completed_tasks) / decided_count) * 100, 1)
        else:
            success_rate = 100.0 if total_tasks == 0 else 0.0

        # Calculate measured durations
        measured_durations: List[float] = []
        for t in completed_tasks:
            # Check duration from result or execution timestamps
            res = t.result or {}
            dur = res.get("duration_sec")
            if dur is not None and isinstance(dur, (int, float)) and dur > 0:
                measured_durations.append(float(dur))
            elif t.claimed_at and t.completed_at:
                try:
                    dt_start = datetime.fromisoformat(t.claimed_at)
                    dt_end = datetime.fromisoformat(t.completed_at)
                    delta = (dt_end - dt_start).total_seconds()
                    if delta > 0:
                        measured_durations.append(delta)
                except Exception:
                    pass

        # Durations summary
        if measured_durations:
            avg_dur = round(sum(measured_durations) / len(measured_durations), 2)
            min_dur = round(min(measured_durations), 2)
            max_dur = round(max(measured_durations), 2)
            total_dur = round(sum(measured_durations), 2)
            dur_stats = {
                "measured_count": len(measured_durations),
                "avg_seconds": avg_dur,
                "min_seconds": min_dur,
                "max_seconds": max_dur,
                "total_seconds": total_dur,
                "avg_formatted": format_duration(avg_dur),
                "total_formatted": format_duration(total_dur),
            }
        else:
            dur_stats = {
                "measured_count": 0,
                "avg_seconds": None,
                "min_seconds": None,
                "max_seconds": None,
                "total_seconds": 0.0,
                "avg_formatted": "Not Recorded",
                "total_formatted": "Not Recorded",
            }

        # Retries analysis
        tasks_with_retries = [t for t in render_tasks if (t.retry_count or 0) > 0]
        retry_rate = round((len(tasks_with_retries) / total_tasks * 100), 1) if total_tasks > 0 else 0.0

        return {
            "total_renders": total_tasks,
            "completed_count": len(completed_tasks),
            "failed_count": len(failed_tasks),
            "running_count": len(running_tasks),
            "queued_count": len(queued_tasks),
            "cancelled_count": len(cancelled_tasks),
            "success_rate_percent": success_rate,
            "tasks_with_retries": len(tasks_with_retries),
            "retry_rate_percent": retry_rate,
            "timing": dur_stats,
        }

    # -------------------------------------------------------------------------
    # 3. Production Cost Intelligence
    # -------------------------------------------------------------------------
    def calculate_costs(self, channel_id: str) -> Dict[str, Any]:
        """Compute estimated costs using configured rates and contrast with actual known costs.

        Maintains strict separation between:
        - Actual Known Cost: default $0.00 (or verifiable invoices)
        - Estimated Cost: calculated via unit rates
        """
        clean_ch = channel_id.strip()
        config = self.intel_repo.get_cost_config(clean_ch)

        # 1. Render usage: total duration seconds
        analytics = self.get_render_analytics(clean_ch)
        total_render_secs = analytics["timing"]["total_seconds"] or 0.0
        render_cost = round(total_render_secs * config.cost_per_render_second, 2)

        # 2. Storage usage: current GB
        storage_info = self.get_channel_storage_breakdown(clean_ch)
        total_gb = storage_info["total_gb"]
        storage_cost = round(total_gb * config.cost_per_storage_gb_month, 2)

        # 3. Flow API takes usage: count of imported takes
        flow_takes = self.flow_repo.list_takes_for_project("")  # list all for channel
        # filter takes belonging to channel
        channel_projects = {p.id for p in self.project_repo.list_by_channel(clean_ch)}
        channel_take_count = sum(1 for t in flow_takes if t.get("project_id") in channel_projects)
        flow_api_cost = round(channel_take_count * config.cost_per_api_take, 2)

        total_estimated = round(render_cost + storage_cost + flow_api_cost, 2)

        return {
            "channel_id": clean_ch,
            "currency": config.currency,
            "actual_known_cost": 0.00,  # Explicit Known Cost default
            "actual_cost_label": f"0.00 {config.currency} (Known / Default)",
            "estimated_cost": total_estimated,
            "estimated_cost_label": f"{total_estimated:.2f} {config.currency} (Estimated)",
            "unit_rates": {
                "cost_per_render_second": config.cost_per_render_second,
                "cost_per_storage_gb_month": config.cost_per_storage_gb_month,
                "cost_per_api_take": config.cost_per_api_take,
            },
            "sub_costs": {
                "render_cost": render_cost,
                "render_seconds_billed": total_render_secs,
                "storage_cost": storage_cost,
                "storage_gb_billed": total_gb,
                "flow_api_cost": flow_api_cost,
                "flow_takes_billed": channel_take_count,
            },
            "attribution": "Cost estimates are computed deterministically from configured unit rates. Zero simulated invoices.",
        }

    def get_cost_summary(
        self,
        channel_id: str,
        power_consumption_watts: Optional[float] = None,
        electricity_price_kwh: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Generate authoritative production cost summary strictly distinguishing
        between Actual Known Cost, Estimated Unit-rate Costs, and Energy Overhead.

        Rules enforced:
        - If Electricity Price or Power Consumption is not configured, electricity cost is 'N/A' (Not '$0.00').
        - Term 'Actual Cost' is strictly reserved for verifiable manual cash outlays; never used for estimates.
        - Energy cost is separated from unit-rate compute estimates.
        - Every metric explicitly declares its status: 'Measured', 'Estimated', 'Manually Recorded', or 'Not Configured'.
        - Zero double counting.
        """
        clean_ch = channel_id.strip()
        config = self.intel_repo.get_cost_config(clean_ch)

        # 1. Measured usage metrics
        analytics = self.get_render_analytics(clean_ch)
        total_render_secs = analytics["timing"]["total_seconds"] or 0.0
        render_secs_status = "Measured" if analytics["timing"]["measured_count"] > 0 else "Not Recorded"

        storage_info = self.get_channel_storage_breakdown(clean_ch)
        total_gb = storage_info["total_gb"]
        storage_status = "Measured"

        channel_projects = {p.id for p in self.project_repo.list_by_channel(clean_ch)}
        flow_takes = self.flow_repo.list_takes_for_project("")
        channel_take_count = sum(1 for t in flow_takes if t.get("project_id") in channel_projects)
        take_status = "Measured"

        # 2. Electricity / Energy Cost Calculation
        if (
            power_consumption_watts is None
            or electricity_price_kwh is None
            or power_consumption_watts <= 0
            or electricity_price_kwh <= 0
        ):
            energy_kwh = None
            energy_cost = "N/A"
            energy_status = "Not Configured"
            energy_label = "N/A (Electricity Price or Power Consumption Not Configured)"
            energy_numeric = 0.0
        else:
            # Energy (kWh) = (Watts * seconds) / (1000 * 3600)
            energy_kwh = round((power_consumption_watts * total_render_secs) / 3600000.0, 6)
            energy_numeric = round(energy_kwh * electricity_price_kwh, 4)
            energy_cost = energy_numeric
            energy_status = "Estimated"
            energy_label = (
                f"{energy_numeric:.4f} {config.currency} "
                f"(Estimated: {power_consumption_watts:.1f}W @ {total_render_secs:.1f}s, {electricity_price_kwh:.4f}/kWh)"
            )

        # 3. Unit-rate sub-costs (Estimated)
        render_cost = round(total_render_secs * config.cost_per_render_second, 2)
        storage_cost = round(total_gb * config.cost_per_storage_gb_month, 2)
        flow_api_cost = round(channel_take_count * config.cost_per_api_take, 2)

        estimated_subtotal = round(render_cost + storage_cost + flow_api_cost, 2)
        total_estimated = round(estimated_subtotal + energy_numeric, 2)

        return {
            "channel_id": clean_ch,
            "currency": config.currency,
            "actual_known_cost": {
                "amount": 0.00,
                "label": f"0.00 {config.currency} (Manually Recorded / Invoiced)",
                "status": "Manually Recorded",
                "notes": "Verified direct external outlay only; zero estimate blending.",
            },
            "estimated_production_cost": {
                "amount": total_estimated,
                "label": f"{total_estimated:.2f} {config.currency} (Estimated)",
                "status": "Estimated",
            },
            "energy_cost": {
                "power_watts": power_consumption_watts,
                "electricity_price_kwh": electricity_price_kwh,
                "energy_consumed_kwh": energy_kwh,
                "amount": energy_cost,
                "label": energy_label,
                "status": energy_status,
                "metric_source": render_secs_status,
            },
            "sub_costs": {
                "render_compute": {
                    "amount": render_cost,
                    "unit_rate": config.cost_per_render_second,
                    "units_billed": total_render_secs,
                    "unit_name": "seconds",
                    "status": "Estimated",
                    "metric_source": render_secs_status,
                },
                "storage": {
                    "amount": storage_cost,
                    "unit_rate": config.cost_per_storage_gb_month,
                    "units_billed": total_gb,
                    "unit_name": "GB-month",
                    "status": "Estimated",
                    "metric_source": storage_status,
                },
                "flow_api_takes": {
                    "amount": flow_api_cost,
                    "unit_rate": config.cost_per_api_take,
                    "units_billed": channel_take_count,
                    "unit_name": "takes",
                    "status": "Estimated",
                    "metric_source": take_status,
                },
            },
            "anti_double_counting_guarantee": "Energy overhead and unit-rate compute are reported as independent line items without duplicating categories.",
        }

    def update_cost_config(
        self, channel_id: str, updates: ProductionCostConfigUpdate
    ) -> ProductionCostConfigModel:
        """Update channel unit rates."""
        return self.intel_repo.set_cost_config(channel_id, updates)

    # -------------------------------------------------------------------------
    # 4. Google Flow Manual Resource Tracking
    # -------------------------------------------------------------------------
    def get_flow_credit_summary(self, channel_id: str) -> Dict[str, Any]:
        """Retrieve Google Flow credit ledger balance and transaction history."""
        clean_ch = channel_id.strip()
        balance = self.intel_repo.get_credit_balance(clean_ch)
        transactions = self.intel_repo.list_credit_transactions(clean_ch, limit=50)

        total_topup = sum(t.credits_amount for t in transactions if t.credits_amount > 0)
        total_usage = abs(sum(t.credits_amount for t in transactions if t.credits_amount < 0))

        return {
            "channel_id": clean_ch,
            "current_balance": round(balance, 2),
            "total_topup": round(total_topup, 2),
            "total_consumed": round(total_usage, 2),
            "transaction_count": len(transactions),
            "attribution": "Manual User Entry (Zero unauthorized live API quota polling)",
            "transactions": [t.model_dump() for t in transactions],
        }

    def record_flow_credit_entry(
        self, channel_id: str, payload: FlowCreditTransactionCreate
    ) -> FlowCreditTransactionModel:
        """Record manual Google Flow credit entry and log immutable audit event."""
        clean_ch = channel_id.strip()
        payload.channel_id = clean_ch
        payload.source = "manual_user_entry"

        # Record in ledger
        tx = self.intel_repo.record_credit_transaction(payload)

        # Log audit event
        try:
            self.audit_service.record_event(
                channel_id=clean_ch,
                action="flow_credit_recorded",
                actor="studio_operator",
                payload={
                    "transaction_id": tx.id,
                    "type": tx.transaction_type,
                    "amount": tx.credits_amount,
                    "balance_after": tx.balance_after,
                    "note": tx.operator_note,
                    "source": tx.source,
                },
                idempotency_key=f"audit_crd_{tx.id}",
            )
        except Exception as e:
            logger.warning(f"Could not log audit event for credit transaction: {e}")

        return tx

    # -------------------------------------------------------------------------
    # 5. Deterministic Production Insights Engine
    # -------------------------------------------------------------------------
    def generate_deterministic_insights(self, channel_id: str) -> List[Dict[str, Any]]:
        """Evaluate 100% deterministic rules to provide operational warnings and recommendations."""
        clean_ch = channel_id.strip()
        insights: List[Dict[str, Any]] = []

        # 1. Google Flow Credits Balance Check
        balance = self.intel_repo.get_credit_balance(clean_ch)
        if balance <= 0.0:
            insights.append({
                "rule": "flow_credits_exhausted",
                "severity": "danger",
                "title_en": "Google Flow Credits Depleted",
                "title_ar": "رصيد Google Flow مستنفد تماماً",
                "message_en": f"Current balance is {balance:.1f} credits. Flow take generation will fail.",
                "message_ar": f"الرصيد الحالي هو {balance:.1f} نقطة. تعذر توليد لقطات جديدة من Flow.",
                "action_recommendation": "Top up credits via Google Flow manual entry ledger.",
            })
        elif balance < 15.0:
            insights.append({
                "rule": "flow_credits_low",
                "severity": "warning",
                "title_en": "Low Google Flow Credit Balance",
                "title_ar": "رصيد Google Flow منخفض",
                "message_en": f"Current balance is {balance:.1f} credits (Threshold: 15.0).",
                "message_ar": f"الرصيد المتبقي هو {balance:.1f} نقطة (حد التنبيه: 15.0).",
                "action_recommendation": "Consider logging credit replenishment before launching batch takes.",
            })

        # 2. Render Failure & Retry Rate Check
        analytics = self.get_render_analytics(clean_ch)
        if analytics["failed_count"] > 0:
            failure_rate = 100.0 - analytics["success_rate_percent"]
            if failure_rate >= 20.0:
                insights.append({
                    "rule": "high_render_failure_rate",
                    "severity": "warning",
                    "title_en": "High Render Failure Rate",
                    "title_ar": "نسبة فشل مرتفعة في عمليات الرندر",
                    "message_en": f"Render failure rate is {failure_rate:.1f}% ({analytics['failed_count']} failed tasks).",
                    "message_ar": f"نسبة الفشل تبلغ {failure_rate:.1f}% ({analytics['failed_count']} مهام فاشلة).",
                    "action_recommendation": "Inspect Render Queue error messages and verify media source integrity.",
                })

        if analytics["tasks_with_retries"] > 0:
            insights.append({
                "rule": "render_retries_detected",
                "severity": "info",
                "title_en": "Render Retries Recorded",
                "title_ar": "تم تسجيل إعادة محاولة لمهام رندر",
                "message_en": f"{analytics['tasks_with_retries']} render task(s) required automatic or manual retry.",
                "message_ar": f"{analytics['tasks_with_retries']} مهمة رندر احتاجت إعادة محاولة.",
                "action_recommendation": "Review WorkerPool logs for transient timeouts.",
            })

        # 3. Storage Footprint & System Disk Check
        sys_res = self.get_system_resources()
        disk_pct = sys_res["disk"]["percent_used"]
        if disk_pct >= 90.0:
            insights.append({
                "rule": "disk_space_critical",
                "severity": "danger",
                "title_en": "Critical Host Disk Space",
                "title_ar": "مساحة القرص منخفضة بشكل حرج",
                "message_en": f"Host disk is {disk_pct:.1f}% full ({sys_res['disk']['free_human']} free).",
                "message_ar": f"امتلاء القرص بلغ {disk_pct:.1f}% (المتبقي: {sys_res['disk']['free_human']}).",
                "action_recommendation": "Archive completed export packages and clean temporary render caches.",
            })

        # 4. Unrendered Projects / Draft Export Packages
        packages = self.export_repo.list_by_channel(clean_ch)
        draft_pkgs = [p for p in packages if p.status == "draft"]
        if draft_pkgs:
            insights.append({
                "rule": "draft_packages_pending",
                "severity": "info",
                "title_en": "Draft Export Packages Pending",
                "title_ar": "حزم نشر قيد المسودة بانتظار الاعتماد",
                "message_en": f"{len(draft_pkgs)} export package(s) remain in draft status.",
                "message_ar": f"{len(draft_pkgs)} حزمة نشر لا تزال بحالة مسودة.",
                "action_recommendation": "Verify license preflight and media quality gates to complete release.",
            })

        # 5. Production Efficiency Metric
        if analytics["total_renders"] > 0:
            if analytics["success_rate_percent"] >= 95.0:
                rating = "Optimal (ممتاز)"
                severity = "success"
            elif analytics["success_rate_percent"] >= 80.0:
                rating = "Moderate (متوسط)"
                severity = "info"
            else:
                rating = "Needs Attention (يتطلب تدخلاً)"
                severity = "warning"

            insights.append({
                "rule": "efficiency_rating",
                "severity": severity,
                "title_en": f"Production Efficiency: {rating}",
                "title_ar": f"مستوى كفاءة الإنتاج: {rating}",
                "message_en": f"Success rate: {analytics['success_rate_percent']}%, Average render: {analytics['timing']['avg_formatted']}.",
                "message_ar": f"نسبة النجاح: {analytics['success_rate_percent']}%, متوسط زمن الرندر: {analytics['timing']['avg_formatted']}.",
                "action_recommendation": "Production line operating deterministically.",
            })

        return insights

    # -------------------------------------------------------------------------
    # 6. Unified Production Version History
    # -------------------------------------------------------------------------
    def get_unified_production_history(
        self, channel_id: str, project_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Combine tasks, flow takes, export packages, and audit events into a chronological timeline."""
        clean_ch = channel_id.strip()
        timeline: List[Dict[str, Any]] = []

        # 1. Tasks
        tasks = self.task_repo.list_tasks(channel_id=clean_ch, project_id=project_id)
        for t in tasks:
            timeline.append({
                "timestamp": t.created_at,
                "type": "render_task" if t.task_type == TaskType.VIDEO_RENDERING else "task",
                "event_id": t.id,
                "project_id": t.project_id,
                "status": t.status.value,
                "title": f"Task: {t.task_type.value} ({t.status.value})",
                "details": f"Mode: {(t.payload or {}).get('mode', 'N/A')}, Duration: {format_duration((t.result or {}).get('duration_sec'))}",
            })

        # 2. Flow Takes
        takes = self.flow_repo.list_takes_for_project(project_id or "")
        for tk in takes:
            timeline.append({
                "timestamp": tk.get("created_at") or "",
                "type": "flow_take",
                "event_id": tk.get("id"),
                "project_id": tk.get("project_id"),
                "status": "active" if tk.get("is_active") else "candidate",
                "title": f"Flow Take #{tk.get('take_number')} (Scene {tk.get('scene_id')})",
                "details": f"Duration: {tk.get('duration_sec', 0.0):.1f}s, Asset: {tk.get('asset_id')}",
            })

        # 3. Export Packages
        packages = self.export_repo.list_by_channel(clean_ch)
        for pkg in packages:
            if not project_id or pkg.project_id == project_id:
                timeline.append({
                    "timestamp": pkg.created_at,
                    "type": "export_package",
                    "event_id": pkg.id,
                    "project_id": pkg.project_id,
                    "status": pkg.status,
                    "title": f"Export Package: {pkg.profile_name} (v{pkg.version})",
                    "details": f"Platform: {pkg.platform}, Status: {pkg.status}",
                })

        # 4. Flow Credit Ledger Events
        credits = self.intel_repo.list_credit_transactions(clean_ch, limit=50)
        for c in credits:
            if not project_id or c.project_id == project_id:
                timeline.append({
                    "timestamp": c.created_at,
                    "type": "credit_ledger",
                    "event_id": c.id,
                    "project_id": c.project_id,
                    "status": c.transaction_type,
                    "title": f"Flow Credit: {c.credits_amount:+.2f} ({c.transaction_type})",
                    "details": f"Balance: {c.balance_after:.2f}, Note: {c.operator_note or 'Manual Entry'}",
                })

        # Sort reverse chronologically
        timeline.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return timeline[:limit]

    # -------------------------------------------------------------------------
    # 7. Safe Report Generation (JSON & CSV)
    # -------------------------------------------------------------------------
    def export_production_report(self, channel_id: str, format_type: str = "json") -> Tuple[str, str]:
        """Generate a complete production intelligence report in JSON or CSV format.

        Guarantees:
        - Strict Path Traversal protection (sanitized filename).
        - Comprehensive data serialization.
        """
        clean_ch = channel_id.strip()
        channel = self.channel_repo.get_by_id(clean_ch)
        channel_name = channel.name if channel else clean_ch

        # Collect data
        storage_data = self.get_channel_storage_breakdown(clean_ch)
        render_data = self.get_render_analytics(clean_ch)
        cost_data = self.calculate_costs(clean_ch)
        flow_data = self.get_flow_credit_summary(clean_ch)
        insights_data = self.generate_deterministic_insights(clean_ch)
        history_data = self.get_unified_production_history(clean_ch, limit=100)

        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_channel_id = re.sub(r"[^a-zA-Z0-9_\-]", "", clean_ch)

        if format_type.lower() == "csv":
            filename = f"production_report_{safe_channel_id}_{now_str}.csv"
            output = io.StringIO()
            writer = csv.writer(output)

            # Metadata header
            writer.writerow(["UNIVERSAL AI VIDEO STUDIO - PRODUCTION INTELLIGENCE REPORT"])
            writer.writerow(["Channel ID", clean_ch])
            writer.writerow(["Channel Name", channel_name])
            writer.writerow(["Generated At UTC", datetime.now(timezone.utc).isoformat()])
            writer.writerow([])

            # Storage Section
            writer.writerow(["--- STORAGE FOOTPRINT ---"])
            writer.writerow(["Total Storage (Bytes)", storage_data["total_bytes"]])
            writer.writerow(["Total Storage (GB)", storage_data["total_gb"]])
            writer.writerow(["Human Readable Size", storage_data["human_size"]])
            for cat, c_info in storage_data["by_category"].items():
                writer.writerow([f"Category: {cat}", c_info["human_size"], f"{c_info['percent']}%"])
            writer.writerow([])

            # Render Analytics Section
            writer.writerow(["--- RENDER & BATCH PERFORMANCE ---"])
            writer.writerow(["Total Renders Dispatched", render_data["total_renders"]])
            writer.writerow(["Completed Tasks", render_data["completed_count"]])
            writer.writerow(["Failed Tasks", render_data["failed_count"]])
            writer.writerow(["Success Rate %", f"{render_data['success_rate_percent']}%"])
            writer.writerow(["Tasks With Retries", render_data["tasks_with_retries"]])
            writer.writerow(["Average Render Duration", render_data["timing"]["avg_formatted"]])
            writer.writerow(["Total Render Time", render_data["timing"]["total_formatted"]])
            writer.writerow([])

            # Cost Section
            writer.writerow(["--- COST INTELLIGENCE ---"])
            writer.writerow(["Currency", cost_data["currency"]])
            writer.writerow(["Actual Known Cost", cost_data["actual_cost_label"]])
            writer.writerow(["Estimated Production Cost", cost_data["estimated_cost_label"]])
            writer.writerow(["Render Cost Portion", f"{cost_data['sub_costs']['render_cost']} {cost_data['currency']}"])
            writer.writerow(["Storage Cost Portion", f"{cost_data['sub_costs']['storage_cost']} {cost_data['currency']}"])
            writer.writerow(["Flow API Cost Portion", f"{cost_data['sub_costs']['flow_api_cost']} {cost_data['currency']}"])
            writer.writerow([])

            # Google Flow Credits
            writer.writerow(["--- GOOGLE FLOW MANUAL CREDITS LEDGER ---"])
            writer.writerow(["Current Balance", flow_data["current_balance"]])
            writer.writerow(["Total Added", flow_data["total_topup"]])
            writer.writerow(["Total Consumed", flow_data["total_consumed"]])
            writer.writerow(["Attribution", flow_data["attribution"]])
            writer.writerow([])

            # Unified History Table
            writer.writerow(["--- RECENT PRODUCTION TIMELINE ---"])
            writer.writerow(["Timestamp", "Type", "Event ID", "Project ID", "Status", "Title", "Details"])
            for h in history_data:
                writer.writerow([
                    h.get("timestamp"),
                    h.get("type"),
                    h.get("event_id"),
                    h.get("project_id"),
                    h.get("status"),
                    h.get("title"),
                    h.get("details"),
                ])

            return filename, output.getvalue()

        elif format_type.lower() in ("md", "markdown"):
            filename = f"production_report_{safe_channel_id}_{now_str}.md"
            md_lines = [
                "# تقرير ذكاء الإنتاج والموارد / Production Intelligence & Resource Report",
                "",
                "## 1. بيانات القناة والتقرير (Metadata)",
                f"- **Channel ID:** `{clean_ch}`",
                f"- **Channel Name:** {channel_name}",
                f"- **Generated At UTC:** `{datetime.now(timezone.utc).isoformat()}`",
                "- **Generator:** Universal AI Video Studio (Task 3.17)",
                "",
                "## 2. البصمة التخزينية (Storage Footprint)",
                f"- **Total Storage (Bytes):** `{storage_data['total_bytes']}`",
                f"- **Total Storage (GB):** `{storage_data['total_gb']} GB`",
                f"- **Human Size:** `{storage_data['human_size']}`",
                "",
                "| الفئة / Category | الحجم / Size | النسبة / Percentage |",
                "| :--- | :--- | :--- |",
            ]
            for cat, c_info in storage_data["by_category"].items():
                md_lines.append(f"| {cat} | {c_info['human_size']} | {c_info['percent']}% |")

            md_lines.extend([
                "",
                "## 3. أداء الرندر والدفعات (Render & Batch Performance)",
                f"- **Total Renders Dispatched:** `{render_data['total_renders']}`",
                f"- **Completed Tasks:** `{render_data['completed_count']}`",
                f"- **Failed Tasks:** `{render_data['failed_count']}`",
                f"- **Success Rate:** `{render_data['success_rate_percent']}%`",
                f"- **Tasks With Retries:** `{render_data['tasks_with_retries']}`",
                f"- **Average Render Duration:** `{render_data['timing']['avg_formatted']}`",
                f"- **Total Render Time:** `{render_data['timing']['total_formatted']}`",
                "",
                "## 4. ذكاء التكاليف التشغيلية (Cost Intelligence)",
                f"- **Currency:** `{cost_data['currency']}`",
                f"- **Actual Known Cost:** `{cost_data['actual_cost_label']}`",
                f"- **Estimated Production Cost:** `{cost_data['estimated_cost_label']}`",
                f"- **Render Compute Cost:** `{cost_data['sub_costs']['render_cost']} {cost_data['currency']}`",
                f"- **Storage Cost:** `{cost_data['sub_costs']['storage_cost']} {cost_data['currency']}`",
                f"- **Flow API Cost:** `{cost_data['sub_costs']['flow_api_cost']} {cost_data['currency']}`",
                "",
                "## 5. دفتر أرصدة Google Flow (Manual Credits Ledger)",
                f"- **Current Balance:** `{flow_data['current_balance']}`",
                f"- **Total Added:** `{flow_data['total_topup']}`",
                f"- **Total Consumed:** `{flow_data['total_consumed']}`",
                f"- **Attribution:** `{flow_data['attribution']}`",
                "",
                "## 6. الخط الزمني للإنتاج (Recent Production Timeline)",
                "| Timestamp | Type | Event ID | Project ID | Status | Title | Details |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ])
            for h in history_data:
                md_lines.append(
                    f"| {h.get('timestamp') or ''} | {h.get('type') or ''} | {h.get('event_id') or ''} | "
                    f"{h.get('project_id') or ''} | {h.get('status') or ''} | {h.get('title') or ''} | {h.get('details') or ''} |"
                )

            return filename, "\n".join(md_lines)

        else:
            filename = f"production_report_{safe_channel_id}_{now_str}.json"
            report_dict = {
                "report_metadata": {
                    "generator": "Universal AI Video Studio - Production Intelligence (Task 3.17)",
                    "channel_id": clean_ch,
                    "channel_name": channel_name,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                "storage": storage_data,
                "render_analytics": render_data,
                "cost_intelligence": cost_data,
                "flow_credits": flow_data,
                "deterministic_insights": insights_data,
                "unified_history": history_data,
            }
            return filename, json.dumps(report_dict, indent=2, ensure_ascii=False)
