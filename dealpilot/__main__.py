import argparse

from .server import run


parser = argparse.ArgumentParser(description="Run the DealPilot web application")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()

run(args.host, args.port)

