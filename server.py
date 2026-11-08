#!/usr/bin/env python3
"""Convenience launcher for DealPilot Web UI & PocketBase Table Explorer."""
import sys
from services.web import run

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    run(port)

