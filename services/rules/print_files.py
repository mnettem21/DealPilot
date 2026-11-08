import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from services.common.env import ROOT
from services.common.pocketbase import escape_filter


IMAGE = "dealpilot-files"


def run_command(args: list[str]) -> subprocess.CompletedProcess:
    """Start a process without fork.

    On this Mac, fork crashes after the PocketBase client has opened a TLS
    connection. posix_spawn does not.
    """
    program = args[0] if os.path.isabs(args[0]) else shutil.which(args[0])
    if not program:
        raise FileNotFoundError(args[0])
    stdout_path = tempfile.mktemp()
    stderr_path = tempfile.mktemp()
    stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    actions = [
        (os.POSIX_SPAWN_DUP2, stdout_fd, 1),
        (os.POSIX_SPAWN_DUP2, stderr_fd, 2),
        (os.POSIX_SPAWN_CLOSE, stdout_fd),
        (os.POSIX_SPAWN_CLOSE, stderr_fd),
    ]
    pid = os.posix_spawn(program, [program, *args[1:]], os.environ, file_actions=actions)
    os.close(stdout_fd)
    os.close(stderr_fd)
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    stdout = Path(stdout_path).read_text(errors="replace")
    stderr = Path(stderr_path).read_text(errors="replace")
    os.remove(stdout_path)
    os.remove(stderr_path)
    if code != 0:
        raise subprocess.CalledProcessError(code, args, stdout, stderr)
    return subprocess.CompletedProcess(args, code, stdout, stderr)


def ensure_image() -> None:
    try:
        run_command(["docker", "image", "inspect", IMAGE])
        return
    except subprocess.CalledProcessError:
        run_command(["docker", "build", "-f", str(ROOT / "Dockerfile.files"), "-t", IMAGE, str(ROOT)])


def print_deal_files(pb, folder: str) -> None:
    rows = pb.list_records("files", f"folder='{escape_filter(folder)}'")
    with tempfile.TemporaryDirectory() as tmp:
        used = set()
        copied = 0
        for row in rows:
            source = ROOT / row["path"]
            if not source.is_file():
                continue
            name = source.name
            while name in used:
                name = f"{copied}_{name}"
            used.add(name)
            shutil.copy(source, Path(tmp) / name)
            copied += 1
        ensure_image()
        result = run_command(["docker", "run", "--rm", "-v", f"{tmp}:/data", IMAGE])
    print(f"credit rules: files for {folder}")
    print(result.stdout)
