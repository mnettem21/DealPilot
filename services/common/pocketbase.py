import json
import urllib.error
import urllib.parse
import urllib.request

from .env import load_env
import os


class PocketBaseError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"PocketBase {status}: {detail}")
        self.status = status
        self.detail = detail


class PocketBase:
    def __init__(self):
        load_env()
        self.base = os.environ["POCKETBASE_URL"].rstrip("/")
        self.token = ""
        self.auth()

    def auth(self) -> None:
        body = {
            "identity": os.environ["POCKETBASE_EMAIL"],
            "password": os.environ["POCKETBASE_PASSWORD"],
        }
        last = None
        for path in (
            "/api/collections/_superusers/auth-with-password",
            "/api/admins/auth-with-password",
        ):
            try:
                data = self._request("POST", path, body, auth=False)
            except PocketBaseError as exc:
                last = exc
                if exc.status in {400, 404}:
                    continue
                raise
            self.token = data["token"]
            return
        raise PocketBaseError(last.status if last else 401, "Could not authenticate")

    def _request(self, method: str, path: str, body=None, query=None, auth=True, retried=False):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if exc.code == 401 and auth and not retried:
                self.auth()
                return self._request(method, path, body, query, auth=True, retried=True)
            raise PocketBaseError(exc.code, detail) from None

    def collections(self):
        data = self._request("GET", "/api/collections", query={"perPage": 200})
        if isinstance(data, list):
            return data
        return data.get("items", [])

    def create_collection(self, body: dict) -> dict:
        return self._request("POST", "/api/collections", body)

    def delete_collection(self, collection_id: str) -> None:
        self._request("DELETE", f"/api/collections/{collection_id}")

    def list_records(self, collection: str, filter_expr: str = "", sort: str = ""):
        page = 1
        rows = []
        while True:
            query = {"page": page, "perPage": 200}
            if filter_expr:
                query["filter"] = filter_expr
            if sort:
                query["sort"] = sort
            data = self._request("GET", f"/api/collections/{collection}/records", query=query)
            items = data.get("items", [])
            rows.extend(items)
            if page >= data.get("totalPages", 1):
                return rows
            page += 1

    def create_record(self, collection: str, fields: dict) -> dict:
        return self._request("POST", f"/api/collections/{collection}/records", fields)

    def update_record(self, collection: str, record_id: str, fields: dict) -> dict:
        return self._request("PATCH", f"/api/collections/{collection}/records/{record_id}", fields)

    def delete_record(self, collection: str, record_id: str) -> None:
        self._request("DELETE", f"/api/collections/{collection}/records/{record_id}")


def escape_filter(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")
