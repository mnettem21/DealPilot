import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.common.pocketbase import PocketBase, escape_filter
from services.common.records import log_action
from services.stages import human_port, load


def build_handler(stage):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DealPilotHuman/0.1"

        def log_message(self, fmt, *args):
            print(f"[human {stage.name}] {self.address_string()} - {fmt % args}")

        def send_json(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not length:
                return {}
            return json.loads(self.rfile.read(length))

        def do_GET(self):
            if self.path.split("?", 1)[0] != "/deals":
                self.send_json({"error": "Not found"}, 404)
                return
            pb = PocketBase()
            deals = []
            for row in pb.list_records(stage.name):
                deals.append(
                    {
                        "folder": row["folder"],
                        "status": row.get("status"),
                        "assignee": row.get("assignee"),
                        "waiting_on": row.get("waiting_on"),
                        "memo": row.get("memo") or "",
                    }
                )
            self.send_json({"stage": stage.name, "deals": deals})

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/answer":
                self.send_json({"error": "Not found"}, 404)
                return
            if not stage.answers:
                self.send_json({"error": f"{stage.name} has no human actions"}, 400)
                return
            try:
                payload = self.read_json()
            except json.JSONDecodeError:
                self.send_json({"error": "Expected JSON"}, 400)
                return
            folder = payload.get("folder") or ""
            decision = payload.get("decision") or ""
            actor = payload.get("actor") or "operator"
            if decision not in stage.answers:
                self.send_json(
                    {"error": f"{decision!r} is not valid. Use: {', '.join(sorted(stage.answers))}"},
                    400,
                )
                return
            pb = PocketBase()
            rows = pb.list_records(stage.name, f"folder='{escape_filter(folder)}'")
            if not rows:
                self.send_json({"error": f"{folder!r} is not in {stage.name}"}, 404)
                return
            row = rows[0]
            if row.get("status") in {"declined", "funded"}:
                self.send_json({"error": f"{folder} is already {row['status']}"}, 400)
                return
            log_action(
                pb,
                folder,
                stage.name,
                actor,
                "decision",
                decision,
                f"{stage.decision_type} {decision}",
            )
            fields = {"status": "approved", "waiting_on": ""}
            if decision == "decline":
                fields = {"status": "declined", "waiting_on": ""}
            elif decision == "request_info":
                fields = {"status": "open", "waiting_on": "Additional information requested"}
            updated = pb.update_record(stage.name, row["id"], fields)
            self.send_json(
                {
                    "folder": updated["folder"],
                    "status": updated["status"],
                    "waiting_on": updated.get("waiting_on") or "",
                    "decision": decision,
                }
            )

    return Handler


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Human actions for one stage")
    parser.add_argument("--stage", required=True)
    args = parser.parse_args()
    stage = load(args.stage)
    port = human_port(stage.name)
    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler(stage))
    print(f"human {stage.name} listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
