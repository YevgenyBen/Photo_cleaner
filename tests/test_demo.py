"""End-to-end: the demo export must produce exactly the expected findings."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("skimage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_demo  # noqa: E402
import make_demo_takeout  # noqa: E402


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    make_demo_takeout.build(out)
    return out


def test_demo_findings_match_expectations(demo):
    assert check_demo.check(demo)
