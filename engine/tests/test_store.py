"""Store backends. PocketBase is tested against a tiny in-process fake speaking the REST calls we use."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from dealpilot.store import FileStore, PocketBaseStore, schema


class FakePB(BaseHTTPRequestHandler):
    db = {}

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        c = u.path.split("/")[3]
        q = parse_qs(u.query)
        rows = list(self.db.setdefault(c, {}).values())
        if "filter" in q:
            want = q["filter"][0].split('"')[1]
            rows = [r for r in rows if r["key"] == want]
        per, page = int(q.get("perPage", [30])[0]), int(q.get("page", [1])[0])
        self._send({"items": rows[(page - 1) * per: page * per], "totalPages": max(1, -(-len(rows) // per))})

    def do_POST(self):
        parts = urlparse(self.path).path.split("/")
        if parts[-1] == "auth-with-password":
            return self._send({"token": "tok123"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body["id"] = f"id{len(self.db.setdefault(parts[3], {}))}"
        self.db[parts[3]][body["id"]] = body
        self._send(body)

    def do_PATCH(self):
        parts = urlparse(self.path).path.split("/")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.db[parts[3]][parts[5]].update(body)
        self._send(self.db[parts[3]][parts[5]])


@pytest.fixture()
def pb():
    FakePB.db = {}
    srv = HTTPServer(("127.0.0.1", 0), FakePB)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.parametrize("make", ["file", "pocketbase"])
def test_upsert_replaces_and_list_returns(make, tmp_path, pb):
    s = FileStore(tmp_path) if make == "file" else PocketBaseStore(pb, email="a@b.c", password="x")
    s.upsert("rules", "r1", {"status": "proposed", "n": 1})
    s.upsert("rules", "r2", {"status": "active"})
    s.upsert("rules", "r1", {"status": "active", "n": 2})
    got = sorted(s.list("rules"), key=lambda r: r.get("n", 0))
    assert len(got) == 2 and {"status": "active", "n": 2} in got
    assert s.list("labels") == []


def test_pocketbase_pages_through_many_records(pb):
    s = PocketBaseStore(pb, token="t")
    for i in range(620):
        s.upsert("deals", f"d{i}", {"status": "closed", "i": i})
    assert len(s.list("deals")) == 620


def test_schema_covers_all_collections():
    assert [c["name"] for c in schema()] == ["deals", "labels", "rules"]
