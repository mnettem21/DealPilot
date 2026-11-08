# Start the demo

From the repo root:

```bash
./demo/up.sh
```

That brings up the web app and one process per stage for parser, rules, human, upload, and SLA, plus history and the sim clock. Running it again skips anything that is already up.

You need a repo-root `.env` with `POCKETBASE_URL`, `POCKETBASE_EMAIL`, and `POCKETBASE_PASSWORD`. The services read it themselves.

## What comes up

| Piece | Address |
|---|---|
| Web app | http://127.0.0.1:8000/ |
| Human actions | http://127.0.0.1:8201/ through :8206/ |
| File upload | http://127.0.0.1:8101/ through :8106/ |
| Sim clock | http://127.0.0.1:8765/now |

Stage order for the ports is intake, credit, committee, product, committed, closed. Parser, rules, SLA, and history do not listen on a port. They poll every 5 seconds.

Logs are appended under `output/logs/`. One file per process, for example `output/logs/parser-intake.log` and `output/logs/web.log`.

## Check

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
curl -s http://127.0.0.1:8765/now
```

A web response of `200` and a clock JSON `now` value means those two are up. Human and upload answer on their ports even when the path is empty.
