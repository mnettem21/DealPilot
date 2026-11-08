import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import auth, consumer, gemini_advisor, service
from .config import ROOT


WEB_ROOT = ROOT / "web"


class DealPilotHandler(BaseHTTPRequestHandler):
    server_version = "DealPilot/0.1"

    def log_message(self, fmt, *args):
        print(f"[dealpilot] {self.address_string()} - {fmt % args}")

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'",
        )

    def send_json(self, data, status=HTTPStatus.OK, cookie=None):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 14 * 1024 * 1024:
            raise ValueError("Request is too large")
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def user(self):
        return auth.current_user(self.headers.get("Cookie", ""))

    def require_user(self, roles=None):
        user = self.user()
        if not user:
            raise PermissionError("Sign in to continue")
        if roles and user["role"] not in roles:
            raise PermissionError("Your account is not authorized for this action")
        return user

    def require_bank(self):
        return self.require_user(set(auth.BANK_DEMO_USERS))

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/health":
                return self.send_json({"status": "ok", "now": service.sim_now()})
            if path == "/api/ai/status":
                return self.send_json({
                    "available": True,
                    "key_configured": gemini_advisor.is_live_gemini_available(),
                    "model": "gemini-1.5-flash",
                })
            if path == "/api/auth/me":
                return self.send_json({"user": self.user()})
            if path == "/api/consumer/applications":
                user = self.require_user({"consumer"})
                return self.send_json(consumer.list_applications(user["id"]))
            if path.startswith("/api/consumer/applications/") and path.endswith("/manifest"):
                user = self.require_user({"consumer"})
                application_id = unquote(path.split("/")[4])
                return self.send_json(consumer.manifest(application_id, user["id"]))
            if path.startswith("/api/consumer/applications/"):
                user = self.require_user({"consumer"})
                application_id = unquote(path.split("/")[4])
                return self.send_json(consumer.get_application(application_id, user["id"]))
            if path == "/api/summary":
                self.require_bank()
                return self.send_json(service.summary())
            if path == "/api/deals":
                self.require_bank()
                return self.send_json(service.get_deals())
            if path.startswith("/api/deals/"):
                self.require_bank()
                identifier = unquote(path.removeprefix("/api/deals/"))
                deal = service.get_deal(identifier)
                return self.send_json(deal or {"error": "Deal not found"}, HTTPStatus.OK if deal else HTTPStatus.NOT_FOUND)
            if path == "/api/human-requests":
                self.require_bank()
                return self.send_json(service.pending_requests())
            if path == "/api/export":
                self.require_bank()
                payload, _path = service.export_submission()
                return self.send_json(payload)
            if path == "/api/fraud-patterns":
                self.require_bank()
                return self.send_json(service.learned_patterns())
            return self.serve_static(path)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            data = self.read_json()
            if path == "/api/auth/signup":
                user, token = auth.signup(data.get("email"), data.get("password"), data.get("display_name"), data.get("organization"))
                return self.send_json({"user": user}, HTTPStatus.CREATED, auth.cookie_for(token))
            if path == "/api/auth/login":
                user, token = auth.login(data.get("email"), data.get("password"))
                return self.send_json({"user": user}, cookie=auth.cookie_for(token))
            if path == "/api/auth/bank-login":
                user, token = auth.login(data.get("email"), data.get("password"), set(auth.BANK_DEMO_USERS))
                return self.send_json({"user": user}, cookie=auth.cookie_for(token))
            if path == "/api/auth/logout":
                auth.logout(self.headers.get("Cookie", ""))
                return self.send_json({"ok": True}, cookie=auth.clear_cookie())
            if path == "/api/consumer/applications":
                user = self.require_user({"consumer"})
                return self.send_json(consumer.create_application(user), HTTPStatus.CREATED)
            if path.startswith("/api/consumer/applications/"):
                user = self.require_user({"consumer"})
                parts = path.split("/")
                application_id = unquote(parts[4])
                action = parts[5] if len(parts) > 5 else "save"
                if action == "save":
                    return self.send_json(consumer.save_application(application_id, user["id"], data.get("form", {}), data.get("current_step")))
                if action == "upload":
                    return self.send_json(consumer.upload_document(application_id, user["id"], data.get("filename"), data.get("content_base64")), HTTPStatus.CREATED)
                if action == "submit":
                    return self.send_json(consumer.submit_application(application_id, user))
                return self.send_json({"error": "Unknown application action"}, HTTPStatus.NOT_FOUND)
            if path == "/api/sim/reset":
                self.require_bank()
                result = service.reset()
                return self.send_json(result)
            if path == "/api/sim/advance":
                self.require_bank()
                return self.send_json(service.advance_clock(data.get("by", "4h")))
            if path == "/api/sim/process":
                self.require_bank()
                processed = service.ingest_arrived()
                service.evaluate_all()
                return self.send_json({"now": service.sim_now(), "processed": processed})
            if path.startswith("/api/human-requests/") and path.endswith("/answer"):
                user = self.require_bank()
                request_id = int(path.split("/")[3])
                result = service.answer_request(request_id, data["answer"], user["id"], user["role"])
                return self.send_json(result)
            if path.startswith("/api/deals/") and path.endswith("/fraud-release"):
                user = self.require_user({"fraud_ops", "admin"})
                identifier = unquote(path.split("/")[3])
                return self.send_json(service.request_fraud_release(identifier, user["id"]))
            if path == "/api/export":
                self.require_bank()
                payload, output_path = service.export_submission(data.get("team", "DealPilot"), data.get("set", "sample"))
                return self.send_json({"path": str(output_path), "submission": payload})
            if path == "/api/ai/config":
                gemini_advisor.set_api_key(data.get("api_key"))
                return self.send_json({"ok": True, "key_configured": gemini_advisor.is_live_gemini_available()})
            if path == "/api/ai/investigate-ring":
                self.require_bank()
                deals = service.get_deals()
                flagged = [d for d in deals if d.get("fraud", {}).get("flagged")]
                result = gemini_advisor.investigate_fraud_ring(flagged, deals)
                return self.send_json(result)
            if path == "/api/ai/investigate-deal":
                self.require_bank()
                identifier = data.get("deal_id")
                question = data.get("question")
                deal = service.get_deal(identifier) if identifier else None
                if not deal:
                    return self.send_json({"error": "Deal not found"}, HTTPStatus.NOT_FOUND)
                result = gemini_advisor.investigate_deal_with_gemini(deal, question)
                return self.send_json(result)
            return self.send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_static(self, path):
        relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            return self.send_json({"error": "Invalid path"}, HTTPStatus.BAD_REQUEST)
        if not candidate.exists() or not candidate.is_file():
            candidate = WEB_ROOT / "index.html"
        body = candidate.read_bytes()
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)


def run(host="127.0.0.1", port=8765):
    service.initialize()
    auth.seed_bank_users()
    server = ThreadingHTTPServer((host, port), DealPilotHandler)
    print(f"DealPilot running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

