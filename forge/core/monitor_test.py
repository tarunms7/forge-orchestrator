from unittest.mock import patch

import pytest

from forge.core.monitor import ResourceMonitor, ResourceSnapshot


def test_snapshot_has_required_fields():
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=100.0)
    assert snap.cpu_percent == 50.0
    assert snap.memory_available_pct == 60.0
    assert snap.disk_free_gb == 100.0


def test_can_dispatch_when_resources_healthy():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=100.0)
    assert monitor.can_dispatch(snap) is True


def test_cannot_dispatch_when_cpu_high():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=90.0, memory_available_pct=60.0, disk_free_gb=100.0)
    assert monitor.can_dispatch(snap) is False


def test_cannot_dispatch_when_memory_low():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=10.0, disk_free_gb=100.0)
    assert monitor.can_dispatch(snap) is False


def test_cannot_dispatch_when_disk_low():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=2.0)
    assert monitor.can_dispatch(snap) is False


@pytest.mark.asyncio
async def test_take_snapshot_returns_real_values():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = await monitor.take_snapshot()
    assert 0.0 <= snap.cpu_percent <= 100.0
    assert 0.0 <= snap.memory_available_pct <= 100.0
    assert snap.disk_free_gb >= 0.0


def test_blocked_reason_reports_all_violations():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=95.0, memory_available_pct=10.0, disk_free_gb=2.0)
    reasons = monitor.blocked_reasons(snap)
    assert len(reasons) == 3
    assert any("cpu" in r.lower() for r in reasons)
    assert any("memory" in r.lower() for r in reasons)
    assert any("disk" in r.lower() for r in reasons)


def test_healthy_snapshot_no_blocked_reasons():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=100.0)
    reasons = monitor.blocked_reasons(snap)
    assert len(reasons) == 0


@pytest.mark.asyncio
async def test_take_snapshot_returns_conservative_defaults_on_oserror():
    """When psutil raises OSError, take_snapshot returns conservative (blocking) defaults."""
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    with patch("forge.core.monitor.psutil.virtual_memory", side_effect=OSError("device not ready")):
        snap = await monitor.take_snapshot()

    assert snap.cpu_percent == 100.0
    assert snap.memory_available_pct == 0.0
    assert snap.disk_free_gb == 0.0
    assert monitor.can_dispatch(snap) is False


@pytest.mark.asyncio
async def test_take_snapshot_returns_conservative_defaults_on_runtime_error():
    """When psutil raises RuntimeError, take_snapshot returns conservative (blocking) defaults."""
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    with patch("forge.core.monitor.psutil.virtual_memory", side_effect=RuntimeError("unexpected")):
        snap = await monitor.take_snapshot()

    assert snap.cpu_percent == 100.0
    assert snap.memory_available_pct == 0.0
    assert snap.disk_free_gb == 0.0
    assert monitor.can_dispatch(snap) is False
