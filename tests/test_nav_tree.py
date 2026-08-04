"""Dotted package names → module tree nav."""

from pymergetic.metal.cdn.models import PackageNavNode, PackageVersionOption
from pymergetic.metal.cdn.services.channel import IndexService


def _ver(channel: str = "lead", version: str = "0.1.0") -> list[PackageVersionOption]:
    return [
        PackageVersionOption(
            channel=channel,
            version=version,
            label=f"lead ({version})" if channel == "lead" else f"{channel} ({version})",
            artifact_count=1,
        )
    ]


def test_nav_insert_dotted_hybrid() -> None:
    roots: list[PackageNavNode] = []
    IndexService._nav_insert(roots, ["test_a"], "test_a", _ver())
    IndexService._nav_insert(roots, ["test_a", "test_d"], "test_a.test_d", _ver())
    IndexService._nav_insert(
        roots, ["test_a", "test_b", "test_c"], "test_a.test_b.test_c", _ver()
    )

    assert len(roots) == 1
    a = roots[0]
    assert a.name == "test_a"
    assert a.full_name == "test_a"
    assert a.is_package and a.is_folder
    assert {c.name for c in a.children} == {"test_b", "test_d"}

    d = next(c for c in a.children if c.name == "test_d")
    assert d.full_name == "test_a.test_d"
    assert d.is_package and not d.is_folder

    b = next(c for c in a.children if c.name == "test_b")
    assert b.full_name is None
    assert b.is_folder and not b.is_package
    assert len(b.children) == 1
    c = b.children[0]
    assert c.full_name == "test_a.test_b.test_c"
    assert c.is_package and not c.is_folder
