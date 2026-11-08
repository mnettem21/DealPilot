"""Where history, feedback labels and learned rules persist. Two interchangeable backends behind the same tiny interface:

    store.list(collection)            -> [record dict, ...]
    store.upsert(collection, key, rec) -> None   (insert or replace the record with this key)

FileStore       one JSON file per collection (dev, tests, offline demo).
PocketBaseStore PocketBase over REST; every collection is {key (unique text), status (text), data (json)} so
                new fields need no schema change.

Pick with environment variables: POCKETBASE_URL (+ POCKETBASE_TOKEN, or POCKETBASE_EMAIL and POCKETBASE_PASSWORD
for a superuser). Without POCKETBASE_URL the FileStore is used.

Collections:  deals  (finished deals the pipeline wrote, source="pipeline")
              labels (Fraud Ops outcomes: confirmed / cleared)
              rules  (learned patterns: proposed / active / rejected)
"""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

COLLECTIONS = ("deals", "labels", "rules")


class FileStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, c):
        return self.folder / f"{c}.json"

    def _load(self, c):
        p = self._path(c)
        return json.load(open(p)) if p.exists() else {}

    def list(self, c):
        return list(self._load(c).values())

    def upsert(self, c, key, rec):
        data = self._load(c)
        data[key] = rec
        json.dump(data, open(self._path(c), "w"), indent=1, default=list)

    def clear(self):
        for c in COLLECTIONS:
            self._path(c).unlink(missing_ok=True)


class PocketBaseStore:
    def __init__(self, url, token=None, email=None, password=None):
        self.url = url.rstrip("/")
        self.token = token
        self.email, self.password = email, password

    def _req(self, method, path, body=None):
        if self.token is None and self.email:
            self.token = self._login()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = self.token
        data = json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(urllib.request.Request(self.url + path, data, headers, method=method), timeout=15) as r:
            return json.load(r)

    def _login(self):
        body = json.dumps({"identity": self.email, "password": self.password}).encode()
        req = urllib.request.Request(self.url + "/api/collections/_superusers/auth-with-password", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)["token"]

    def _find(self, c, key):
        q = urllib.parse.urlencode({"filter": f'key="{key}"', "perPage": 1})
        items = self._req("GET", f"/api/collections/{c}/records?{q}")["items"]
        return items[0] if items else None

    def list(self, c):
        out, page = [], 1
        while True:
            res = self._req("GET", f"/api/collections/{c}/records?perPage=500&page={page}")
            out += [i["data"] for i in res["items"]]
            if page >= res.get("totalPages", 1):
                return out
            page += 1

    def upsert(self, c, key, rec):
        body = {"key": key, "status": str(rec.get("status") or rec.get("label") or ""), "data": rec}
        old = self._find(c, key)
        if old:
            self._req("PATCH", f"/api/collections/{c}/records/{old['id']}", body)
        else:
            self._req("POST", f"/api/collections/{c}/records", body)


def make_store(folder):
    url = os.environ.get("POCKETBASE_URL")
    if url:
        return PocketBaseStore(url, os.environ.get("POCKETBASE_TOKEN"), os.environ.get("POCKETBASE_EMAIL"), os.environ.get("POCKETBASE_PASSWORD"))
    return FileStore(folder)


def schema():
    """Collections to import into PocketBase (v0.23+ format). Not yet tried against a live instance."""
    return [{"name": c, "type": "base",
             "fields": [{"name": "key", "type": "text", "required": True}, {"name": "status", "type": "text"},
                        {"name": "data", "type": "json", "maxSize": 2000000}],
             "indexes": [f"CREATE UNIQUE INDEX idx_{c}_key ON {c} (key)"]} for c in COLLECTIONS]


if __name__ == "__main__":
    print(json.dumps(schema(), indent=2))
