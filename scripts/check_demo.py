"""Scan the demo export and compare findings with make_demo_takeout.EXPECTED."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_demo_takeout import EXPECTED  # noqa: E402
from photo_cleaner.detect import Settings  # noqa: E402
from photo_cleaner.scan import run_scan  # noqa: E402
from photo_cleaner.state import State  # noqa: E402


def check(demo: Path) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        state = State(Path(tmp))
        result = run_scan(sorted(demo.glob("*.zip")), Path(tmp), state, Settings(), log=lambda m: None)
        state.close()
    got: dict[str, set[str]] = {}
    for f in result.findings:
        for i in f.flagged:
            got.setdefault(f.category, set()).add(i.title)
            print(f"{f.category:16} {i.title:45} {f.reasons.get(i.id)}  keep={[k.title for k in f.keep]}")
    ok = True
    for cat in sorted(set(EXPECTED) | set(got)):
        missing, extra = EXPECTED.get(cat, set()) - got.get(cat, set()), got.get(cat, set()) - EXPECTED.get(cat, set())
        if missing or extra:
            ok = False
            print(f"MISMATCH {cat}: missing={sorted(missing)} extra={sorted(extra)}")
    print("ALL EXPECTED FINDINGS MATCH" if ok else "MISMATCHES FOUND")
    return ok


if __name__ == "__main__":
    sys.exit(0 if check(Path(sys.argv[1] if len(sys.argv) > 1 else "demo")) else 1)
