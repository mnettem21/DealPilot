import importlib

ORDER = ("intake", "credit", "committee", "product", "committed", "closed")


def load(name: str):
    key = name.strip().lower()
    if key not in ORDER:
        raise SystemExit(f"Unknown stage {name!r}. Choose one of: {', '.join(ORDER)}")
    module = importlib.import_module(f"services.stages.{key}")
    return module.STAGE


def human_port(name: str) -> int:
    return 8201 + ORDER.index(name.strip().lower())


def upload_port(name: str) -> int:
    return 8101 + ORDER.index(name.strip().lower())
