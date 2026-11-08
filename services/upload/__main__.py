import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from services.common.clock import now
from services.common.env import ROOT
from services.common.pocketbase import PocketBase, escape_filter
from services.parser.extract import classify
from services.stages import load, upload_port


def parse_multipart(content_type: str, body: bytes):
    if "boundary=" not in content_type:
        raise ValueError("Expected multipart form data")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
    fields = {}
    uploaded = None
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        header, _, content = part.partition(b"\r\n\r\n")
        content = content.removesuffix(b"\r\n")
        text = header.decode(errors="replace")
        name = _token(text, "name")
        filename = _token(text, "filename")
        if not name:
            continue
        if filename is None:
            fields[name] = content.decode(errors="replace").strip()
        else:
            uploaded = (Path(filename).name, content)
    return fields, uploaded


def _token(header: str, key: str):
    marker = f'{key}="'
    if marker not in header:
        return None
    return header.split(marker, 1)[1].split('"', 1)[0]


def build_handler(stage):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DealPilotUpload/0.1"

        def log_message(self, fmt, *args):
            print(f"[upload {stage.name}] {self.address_string()} - {fmt % args}")

        def send_json(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/files":
                self.send_json({"error": "Not found"}, 404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 25 * 1024 * 1024:
                self.send_json({"error": "File is too large"}, 400)
                return
            try:
                fields, uploaded = parse_multipart(
                    self.headers.get("Content-Type", ""),
                    self.rfile.read(length),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            folder = fields.get("folder") or ""
            if uploaded is None or not uploaded[0] or not folder:
                self.send_json({"error": "Send a folder field and a file"}, 400)
                return
            if "/" in folder or "\\" in folder or folder in {".", ".."}:
                self.send_json({"error": "Folder is not valid"}, 400)
                return
            pb = PocketBase()
            rows = pb.list_records(stage.name, f"folder='{escape_filter(folder)}'")
            if not rows:
                self.send_json({"error": f"{folder!r} is not in {stage.name}"}, 404)
                return
            filename, content = uploaded
            destination = ROOT / "data" / "uploads" / folder
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / filename
            path.write_bytes(content)
            relative = path.relative_to(ROOT).as_posix()
            doc_type = classify(content[:2000].decode(errors="ignore"), filename)
            record = pb.create_record(
                "files",
                {
                    "folder": folder,
                    "path": relative,
                    "filename": filename,
                    "arrived_at": now(),
                    "doc_type": doc_type,
                    "source": "upload",
                },
            )
            self.send_json({"id": record.get("id"), "path": relative, "doc_type": doc_type})

    return Handler


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Upload a file onto a deal in one stage")
    parser.add_argument("--stage", required=True)
    args = parser.parse_args()
    stage = load(args.stage)
    port = upload_port(stage.name)
    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler(stage))
    print(f"upload {stage.name} listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
