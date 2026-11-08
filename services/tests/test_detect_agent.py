"""Detection agent: warning for the unconfirmed Metroplex pattern, critical only when confirmed."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agent.detect import publish, screen_folder

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT
LOOKALIKE = {"Ridgeway Freight Logistics", "Pecan Grove Catering", "Summit Peak Flooring"}
CLEAN = {
    "Bluebonnet Landscape",
    "Garza Family Pharmacy",
    "Harbor Tea Co",
    "LSFD_docs",
    "Oakline Auto",
    "Okafor Advisory - James",
    "Priya's Bakery",
    "Westfield Print & Sign",
    "tyler loopwise loan stuff",
}


def test_sample_deals_warn_lookalikes_and_leave_good_deals_clear():
    results = {}
    for name in sorted(LOOKALIKE | CLEAN | {"Granite Bay Building Supply"}):
        results[name] = screen_folder(DATA / "shared_folder" / name, DATA, name)

    assert results["Granite Bay Building Supply"]["level"] == "critical"
    assert results["Granite Bay Building Supply"]["hold"] is False
    assert {item["rule"] for item in results["Granite Bay Building Supply"]["findings"]} >= {
        "STMT_TAMPER",
        "REV_INFLATION",
    }

    for name in LOOKALIKE:
        result = results[name]
        assert result["level"] == "warning", result
        assert result["hold"] is False
        assert result["signals"]["vendor"] == "Metroplex Equipment Supply LLC"
        assert result["signals"]["remit_acct_last4"] == "4471"
        assert "DEPENDENCY_BREAK" in {item["rule"] for item in result["findings"]}
        assert all(item["confirmed"] is False for item in result["findings"])
        assert {"H-2608-002", "H-2609-008"} <= set(result["linked_deals"])

    oakline = results["Oakline Auto"]
    assert oakline["level"] == "none"
    assert oakline["hold"] is False
    assert oakline["signals"]["vendor"] == "Metroplex Equipment Supply, Inc."
    assert oakline["signals"]["remit_acct_last4"] == "9902"

    for name in CLEAN:
        assert results[name]["level"] == "none", name
        assert results[name]["hold"] is False


def test_callback_uses_configured_api_token(monkeypatch):
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received["auth"] = self.headers.get("Authorization")
            received["body"] = json.loads(self.rfile.read(length).decode())
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PIPELINE_URL", f"http://127.0.0.1:{server.server_address[1]}/fraud")
    monkeypatch.setenv("API_TOKEN", "sample-token")
    try:
        publish({"folder": "Ridgeway Freight Logistics", "level": "warning", "hold": False})
    finally:
        server.shutdown()
    assert received["auth"] == "Bearer sample-token"
    assert received["body"]["level"] == "warning"
    assert received["body"]["hold"] is False


def test_image_does_not_bake_an_api_token():
    text = (ROOT / "Dockerfile.agent").read_text()
    assert "API_TOKEN" not in text
    assert "ENTRYPOINT" in text
    assert "API_TOKEN" in (ROOT / "agent" / "INSTRUCTIONS.md").read_text()
