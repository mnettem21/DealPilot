#!/usr/bin/env python3
"""DealPilot pretend clock.

The sim clock lets you fast-forward time to test SLA alerts and see files "land" in the shared folder
the way they would during a real week.

  python3 sim/clock.py reset                 # set clock to the start of the sample week (2026-11-02 08:00 CT)
  python3 sim/clock.py now                   # print current sim time
  python3 sim/clock.py advance 6h            # move forward (units: m, h, d)  e.g. 90m, 2d
  python3 sim/clock.py set 2026-11-04T09:00  # jump to a time (never backwards unless --force)
  python3 sim/clock.py inbox                 # list files that have arrived by now
  python3 sim/clock.py inbox --new           # only files that arrived since the last 'inbox --new'
  python3 sim/clock.py sync live_inbox/      # copy arrived files into a folder (your app can watch it)
  python3 sim/clock.py serve 8765            # tiny HTTP API: GET /now, POST /advance?by=6h, GET /inbox?since=ISO

Library use:
  from clock import SimClock
  c = SimClock(); c.now(); c.advance("2h"); c.arrived()

State lives in sim/.clock_state.json. Point --arrivals / --folder at the weekend drop when it is released.
"""
import argparse, datetime as dt, json, re, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=-6))  # America/Chicago, CST (no DST in the sim window)
DEFAULT_START = "2026-11-02T08:00"


def parse_t(s):
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=TZ)


def parse_delta(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([mhd])", s.strip())
    if not m:
        raise ValueError("use e.g. 30m, 6h, 2d")
    n, u = float(m.group(1)), m.group(2)
    return dt.timedelta(minutes=n) if u == "m" else dt.timedelta(hours=n) if u == "h" else dt.timedelta(days=n)


class SimClock:
    def __init__(self, state=HERE / ".clock_state.json", arrivals=HERE / "arrivals.jsonl", folder=HERE.parent / "shared_folder"):
        self.state_path, self.arrivals_path, self.folder = Path(state), Path(arrivals), Path(folder)
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"now": DEFAULT_START, "seen_until": None}

    def _save(self):
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def now(self):
        return parse_t(self.state["now"])

    def set(self, t, force=False):
        t = parse_t(t) if isinstance(t, str) else t
        if t < self.now() and not force:
            raise ValueError("refusing to move the clock backwards (use force)")
        self.state["now"] = t.isoformat(timespec="minutes")
        self._save()
        return t

    def advance(self, delta):
        return self.set(self.now() + (parse_delta(delta) if isinstance(delta, str) else delta))

    def reset(self, start=DEFAULT_START):
        self.state = {"now": start, "seen_until": None}
        self._save()

    def schedule(self):
        rows = [json.loads(l) for l in self.arrivals_path.read_text().splitlines() if l.strip()]
        return sorted(rows, key=lambda r: r["arrive_at"])

    def arrived(self, since=None):
        now = self.now()
        since = parse_t(since) if since else None
        return [r for r in self.schedule() if parse_t(r["arrive_at"]) <= now and (since is None or parse_t(r["arrive_at"]) > since)]

    def new_since_last_check(self):
        rows = self.arrived(self.state.get("seen_until"))
        self.state["seen_until"] = self.state["now"]
        self._save()
        return rows

    def sync(self, dest):
        dest = Path(dest)
        copied = []
        for r in self.arrived():
            src, dst = self.folder / r["path"], dest / r["path"]
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(r["path"])
        return copied


def serve(clock, port):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            b = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/now":
                return self._send({"now": clock.now().isoformat()})
            if u.path == "/inbox":
                return self._send(clock.arrived(q.get("since", [None])[0]))
            if u.path == "/schedule":
                return self._send({"count": len(clock.schedule())})
            self._send({"error": "unknown path"}, 404)

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/advance":
                    return self._send({"now": clock.advance(q["by"][0]).isoformat()})
                if u.path == "/set":
                    return self._send({"now": clock.set(q["t"][0]).isoformat()})
                if u.path == "/reset":
                    clock.reset(q.get("t", [DEFAULT_START])[0])
                    return self._send({"now": clock.now().isoformat()})
            except Exception as ex:
                return self._send({"error": str(ex)}, 400)
            self._send({"error": "unknown path"}, 404)

    print(f"sim clock on http://localhost:{port}  (GET /now /inbox  POST /advance?by=6h /set?t= /reset)")
    HTTPServer(("", port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["now", "advance", "set", "reset", "inbox", "sync", "serve"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--arrivals", default=str(HERE / "arrivals.jsonl"))
    ap.add_argument("--folder", default=str(HERE.parent / "shared_folder"))
    a = ap.parse_args()
    c = SimClock(arrivals=a.arrivals, folder=a.folder)
    if a.cmd == "now":
        print(c.now().isoformat())
    elif a.cmd == "advance":
        print(c.advance(a.arg or "1h").isoformat())
    elif a.cmd == "set":
        print(c.set(a.arg, a.force).isoformat())
    elif a.cmd == "reset":
        c.reset(a.arg or DEFAULT_START)
        print(c.now().isoformat())
    elif a.cmd == "inbox":
        for r in (c.new_since_last_check() if a.new else c.arrived()):
            print(r["arrive_at"], r["path"])
    elif a.cmd == "sync":
        for p in c.sync(a.arg or "live_inbox"):
            print("copied", p)
    elif a.cmd == "serve":
        serve(c, int(a.arg or 8765))
