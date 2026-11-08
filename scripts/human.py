#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.stages import human_port, load


def request(port: int, method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    call = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(detail) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Human service is not reachable on port {port}. "
            f"Start it with: python3 -m services.human --stage <stage>"
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Talk to one stage's human service")
    parser.add_argument("--stage", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    answer = commands.add_parser("answer")
    answer.add_argument("folder")
    answer.add_argument("decision")
    answer.add_argument("--actor", default="operator")
    args = parser.parse_args()
    stage = load(args.stage)
    port = human_port(stage.name)
    if args.command == "list":
        print(json.dumps(request(port, "GET", "/deals"), indent=2))
        return
    print(
        json.dumps(
            request(
                port,
                "POST",
                "/answer",
                {"folder": args.folder, "decision": args.decision, "actor": args.actor},
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
