"""Flat FQN package catalog (date-ordered)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.models import ChannelIndex, PackageEntry
from pymergetic.metal.cdn.services.channel import IndexService
from pymergetic.metal.cdn.storage import LocalObjectStorage


@pytest.mark.asyncio
async def test_list_catalog_flat_fqn_newest_first(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "packs")
    svc = IndexService(storage)

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 6, 1, tzinfo=UTC)
    lead = ChannelIndex(
        schema=1,
        channel="lead",
        generated=t1,
        packages={
            "test_a": PackageEntry(version="0.1.0", artifacts=[], updated_at=t0),
            "test_a.test_d": PackageEntry(version="0.1.0", artifacts=[], updated_at=t1),
            "test_a.test_b.test_c": PackageEntry(
                version="0.1.0",
                artifacts=[],
                updated_at=t0 + timedelta(days=2),
                deps={"test_a.test_d": "0.1.0"},
            ),
            "hello": PackageEntry(
                version="0.1.0", artifacts=[], updated_at=t0 + timedelta(days=1)
            ),
        },
    )
    pin = ChannelIndex(
        schema=1,
        channel="@0.1.0",
        generated=t1,
        packages={
            "test_a": PackageEntry(version="0.1.0", artifacts=[], updated_at=t0),
        },
    )
    await svc.save(lead)
    await svc.save(pin)

    rows = await svc.list_catalog()
    by = {r.name: r for r in rows}
    assert set(by) == {"test_a", "test_a.test_d", "test_a.test_b.test_c", "hello"}
    assert by["test_a"].version_count == 2
    assert by["test_a"].channel == "lead"
    assert by["test_a.test_d"].needed_by == ["test_a.test_b.test_c"]
    assert by["test_a.test_b.test_c"].deps == {"test_a.test_d": "0.1.0"}
    assert by["test_a.test_b.test_c"].deps_ok == {"test_a.test_d": True}
    assert by["test_a.test_b.test_c"].needed_by == []
    assert await svc.list_dependents("test_a.test_d") == ["test_a.test_b.test_c"]
    # Newest first: test_d (Jun) before hello (Jan+1d) / test_c (Jan+2d) / test_a (Jan)
    assert [r.name for r in rows][0] == "test_a.test_d"


@pytest.mark.asyncio
async def test_deps_ok_missing_and_version_mismatch(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "packs")
    svc = IndexService(storage)
    now = datetime(2026, 8, 1, tzinfo=UTC)
    lead = ChannelIndex(
        schema=1,
        channel="lead",
        generated=now,
        packages={
            "app": PackageEntry(
                version="1.0.0",
                artifacts=[],
                updated_at=now,
                deps={"lib": "0.2.0", "ghost": "1.0.0"},
            ),
            "lib": PackageEntry(version="0.1.0", artifacts=[], updated_at=now),
        },
    )
    await svc.save(lead)
    by = {r.name: r for r in await svc.list_catalog()}
    assert by["app"].deps_ok == {"lib": False, "ghost": False}
    assert await svc.deps_fit({"lib": "0.1.0", "ghost": "1.0.0"}) == {
        "lib": True,
        "ghost": False,
    }
