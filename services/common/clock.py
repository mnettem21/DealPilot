import importlib.util

from .env import ROOT


def _sim_clock_type():
    spec = importlib.util.spec_from_file_location("dealpilot_sim_clock", ROOT / "sim" / "clock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SimClock


def clock():
    return _sim_clock_type()()


def now() -> str:
    return clock().now().isoformat(timespec="minutes")


def arrived():
    return clock().arrived()
