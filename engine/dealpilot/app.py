"""Tiny web server: JSON API + the one-page UI. No framework."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .pipeline import Engine, hours

ROOT = Path(__file__).resolve().parent.parent
LOCK = threading.Lock()


def summary(eng):
    t = eng.now()
    deals = []
    for n, d in eng.state["deals"].items():
        f = d["facts"]
        live = d["status"] == "active" and d["stage"] != "Closed"
        deals.append({
            "name": n, "legal_name": f.get("legal_name"), "amount": f.get("amount_requested"), "product": f.get("product"),
            "purpose": f.get("purpose_category"), "stage": d["stage"], "status": d["status"],
            "rm": eng.name(d["rm"]), "waiting_on": d.get("waiting_on"), "issues": d["issues"], "needed_by": f.get("needed_by"),
            "sla": eng._sla(d) if f.get("legal_name") and live else None,
            "hours": round(hours(eng.clockmod.parse_t(d["stage_entered"]), t), 1) if live else None,
            "fraud": d["fraud"], "alerts": len(d["alerts"]), "risk": eng.risk(d), "baseline": eng.baseline_note(d),
            "style": f.get("style"),
        })
    pend = [{"deal": n, **{k: r[k] for k in ("id", "type", "to", "at", "message", "options")}, "to_name": eng.name(r["to"]) if r["to"] != "borrower" else "Borrower"}
            for n, r in eng.pending()]
    alerts = sorted([{"deal": n, **a, "to_name": eng.name(a["to"])} for n, d in eng.state["deals"].items() for a in d["alerts"]], key=lambda a: a["at"], reverse=True)
    rings = {}
    for x in deals:
        if x["fraud"].get("flagged"):
            key = ",".join(sorted([x["name"]] + x["fraud"].get("linked_deals", [])))
            rings.setdefault(key, []).append(x)
    rules = sorted(eng.learned.values(), key=lambda r: (r["status"] != "proposed", r["created_at"]))
    return {"now": eng.state["now"], "deals": deals, "pending": pend, "rules": [{k: r.get(k) for k in ("id", "status", "tokens", "support", "evidence")} for r in rules], "alerts": alerts[:60],
            "fraud": [{"members": [m["name"] for m in v], "history": sorted({h for m in v for h in m["fraud"].get("linked_deals", []) if h.startswith("H-")}),
                       "reason": v[0]["fraud"].get("reason"), "held": [m["name"] for m in v if m["fraud"].get("held")]} for v in rings.values()]}


def make_handler(eng):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200, ctype="application/json"):
            b = obj if isinstance(obj, bytes) else json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self._send((ROOT / "ui" / "index.html").read_bytes(), ctype="text/html; charset=utf-8")
            with LOCK:
                if u.path == "/api/state":
                    return self._send(summary(eng))
                if u.path == "/api/deal":
                    name = self.path.split("name=", 1)[-1]
                    from urllib.parse import unquote
                    d = eng.state["deals"].get(unquote(name))
                    return self._send({k: v for k, v in d.items() if k != "file_times"} if d else {"error": "no such deal"}, 200 if d else 404)
                if u.path == "/api/submission":
                    return self._send(eng.export())
            self._send({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            with LOCK:
                try:
                    if self.path == "/api/advance":
                        eng.advance(body.get("by", "1h"))
                    elif self.path == "/api/answer":
                        eng.answer(body["deal"], body["id"], body["answer"], by="ui")
                    elif self.path == "/api/rule":
                        eng.decide_rule(body["id"], body["decision"])
                    elif self.path == "/api/auto":
                        eng.auto_answer(skip_people=body.get("skip", []))
                    elif self.path == "/api/reset":
                        eng.reset(clear_store=True)
                    elif self.path == "/api/export":
                        out = ROOT / "submission.json"
                        json.dump(eng.export(set_name=body.get("set", "sample")), open(out, "w"), indent=1)
                        return self._send({"wrote": str(out)})
                    else:
                        return self._send({"error": "not found"}, 404)
                    eng.save()
                    return self._send(summary(eng))
                except Exception as ex:  # surface to the UI instead of a silent 500
                    return self._send({"error": str(ex)}, 400)

    return H


def main(port=8765, data=None, arrivals=None):
    eng = Engine(data or next(p for p in (ROOT / "data", ROOT.parent) if (p / "shared_folder").exists()), ROOT / "state" / "state.json", arrivals)
    print(f"DealPilot on http://localhost:{port}")
    ThreadingHTTPServer(("", port), make_handler(eng)).serve_forever()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data")
    ap.add_argument("--arrivals")
    a = ap.parse_args()
    main(a.port, a.data, a.arrivals)
