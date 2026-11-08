"""python -m dealpilot.cli run   -> play the whole sim week with auto-answering humans, write submission.json"""
import argparse
import json
import shutil
from pathlib import Path

from .pipeline import Engine
from .store import FileStore

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "check"])
    ap.add_argument("--expect", help="check: answer key json (see tests/expected_sample.json); omit when the key is hidden")
    ap.add_argument("--data", default=str(next(p for p in (ROOT / "data", ROOT.parent) if (p / "shared_folder").exists())))
    ap.add_argument("--arrivals")
    ap.add_argument("--state", default=str(ROOT / "state" / "cli_state.json"))
    ap.add_argument("--out", default=str(ROOT / "submission.json"))
    ap.add_argument("--until", default="2026-11-06T18:00")
    ap.add_argument("--answer-delay-h", type=float, default=1.0, help="humans answer after this many hours")
    ap.add_argument("--silent", default="", help="comma list of people who never answer")
    ap.add_argument("--set", default="sample")
    a = ap.parse_args()
    Path(a.state).unlink(missing_ok=True)
    shutil.rmtree(ROOT / "state" / "cli_store", ignore_errors=True)   # every headless run starts from a clean store
    eng = Engine(a.data, a.state, a.arrivals, FileStore(ROOT / "state" / "cli_store"))
    end = eng.clockmod.parse_t(a.until)
    silent = [p for p in a.silent.split(",") if p]
    while eng.now() < end:
        eng.advance("1h")
        eng.auto_answer(skip_people=silent, only_older_than_h=a.answer_delay_h)
    sub = eng.export(set_name=a.set)
    json.dump(sub, open(a.out, "w"), indent=1)
    print("wrote", a.out)
    if a.cmd == "check":
        from .verify import verify
        problems = verify(sub, Path(a.data), json.load(open(a.expect)) if a.expect else None)
        print("\n".join(["CHECK FAILED:"] + problems) if problems else "CHECK OK")
        raise SystemExit(1 if problems else 0)
    for n, d in eng.state["deals"].items():
        print(f"{n:32s} {d['status']:9s} {d['stage']:10s} fraud={d['fraud'].get('flagged')} issues={d['issues']} alerts={len(d['alerts'])}")


if __name__ == "__main__":
    main()
