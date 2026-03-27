"""Resource monitor. Tracks CPU, memory, disk and gates agent dispatch."""

import asyncio
import logging
from dataclasses import dataclass

import psutil

logger = logging.getLogger("forge.monitor")


@dataclass(frozen=True)
class ResourceSnapshot:
    """Point-in-time system resource reading."""

    cpu_percent: float
    memory_available_pct: float
    disk_free_gb: float


class ResourceMonitor:
    """Monitors system resources and decides if new agents can be dispatched."""

    def __init__(
        self,
        cpu_threshold: float,
        memory_threshold_pct: float,
        disk_threshold_gb: float,
    ) -> None:
        self._cpu_threshold = cpu_threshold
        self._memory_threshold_pct = memory_threshold_pct
        self._disk_threshold_gb = disk_threshold_gb

    def _take_snapshot_sync(self) -> ResourceSnapshot:
        try:
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            return ResourceSnapshot(
                cpu_percent=psutil.cpu_percent(interval=0),
                memory_available_pct=mem.available / mem.total * 100,
                disk_free_gb=disk.free / (1024**3),
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("Resource snapshot failed: %s — returning safe defaults", exc)
            return ResourceSnapshot(
                cpu_percent=0.0,
                memory_available_pct=100.0,
                disk_free_gb=100.0,
            )

    async def take_snapshot(self) -> ResourceSnapshot:
        try:
            snapshot = await asyncio.to_thread(self._take_snapshot_sync)
            return snapshot
        except (OSError, RuntimeError) as exc:
            logger.warning("Resource snapshot failed: %s -- returning safe defaults", exc)
            return ResourceSnapshot(cpu_percent=0.0, memory_available_pct=100.0, disk_free_gb=100.0)

    def can_dispatch(self, snapshot: ResourceSnapshot) -> bool:
        return len(self.blocked_reasons(snapshot)) == 0

    def blocked_reasons(self, snapshot: ResourceSnapshot) -> list[str]:
        reasons: list[str] = []
        if snapshot.cpu_percent > self._cpu_threshold:
            reasons.append(
                f"CPU at {snapshot.cpu_percent:.1f}% (threshold: {self._cpu_threshold:.1f}%)"
            )
        if snapshot.memory_available_pct < self._memory_threshold_pct:
            reasons.append(
                f"Memory available {snapshot.memory_available_pct:.1f}% "
                f"(threshold: {self._memory_threshold_pct:.1f}%)"
            )
        if snapshot.disk_free_gb < self._disk_threshold_gb:
            reasons.append(
                f"Disk free {snapshot.disk_free_gb:.1f}GB "
                f"(threshold: {self._disk_threshold_gb:.1f}GB)"
            )
        return reasons
