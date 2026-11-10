"""One explicit design per owned headless server, with no SWIG fallback."""

import hashlib
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pynng

from .protocol import Client, Protocol
from .runtime import data_dir, runtime_env, runtime_paths


class Sessions:
    def __init__(self, runtime=None):
        self.root, self.cli, descriptor = runtime_paths(runtime)
        self.protocol = Protocol(descriptor)
        self.sessions = {}

    def start(self, path):
        target = Path(path).expanduser().resolve(strict=True)
        if target.suffix not in {".kicad_pro", ".kicad_pcb", ".kicad_sch"}:
            raise ValueError("Choose an existing .kicad_pro, .kicad_pcb or .kicad_sch file.")
        key = str(target)
        if key in self.sessions:
            return self.status(key)
        if any(Path(existing).parent == target.parent for existing in self.sessions):
            raise ValueError(
                "A session already owns this design folder. Open additional documents "
                "through that session, rather than launching another writer."
            )
        locks = list(target.parent.glob("~*.lck"))
        if locks:
            raise ValueError(
                "KiCad lock files are present; close the other editor first: "
                + ", ".join(str(p) for p in locks)
            )
        if len(self.sessions) >= 4:
            raise ValueError("Four sessions are open; close one explicitly first.")
        # Fresh per-process directories avoid stale-socket readiness and cross-process collisions.
        socket_dir = Path(tempfile.mkdtemp(prefix="ktb-"))
        socket_dir.chmod(0o700)
        socket = socket_dir / "api.sock"
        log_dir = data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(key.encode()).hexdigest()[:16]
        log = log_dir / f"{name}-{os.getpid()}.log"
        args = [
            str(self.cli),
            "api-server",
            key,
            "--socket",
            str(socket),
            "--parent-pid",
            str(os.getpid()),
        ]
        with log.open("ab") as stream:
            proc = subprocess.Popen(args, stdout=stream, stderr=stream, env=runtime_env())
        client = Client(self.protocol, str(socket), timeout_ms=1000)
        deadline = time.monotonic() + 35
        last_error = None
        while time.monotonic() < deadline and proc.poll() is None:
            candidates = [socket, *socket_dir.glob("api-*.sock")]
            for candidate in candidates:
                if not candidate.exists():
                    continue
                client.socket = "ipc://" + str(candidate)
                try:
                    version = client.ping()
                    client.timeout_ms = 15000
                    self.sessions[key] = {
                        "process": proc,
                        "client": client,
                        "log": log,
                        "directory": socket_dir,
                        "version": version,
                    }
                    return self.status(key)
                except (pynng.NNGException, RuntimeError) as exc:
                    last_error = str(exc)
            time.sleep(0.1)
        # Only our new process, with no successful commands, can be terminated on startup failure.
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        raise RuntimeError(f"Runtime failed to start: {last_error}; inspect {log}")

    def get(self, path):
        key = str(Path(path).expanduser().resolve())
        if key not in self.sessions:
            raise ValueError("No session for that exact path; call start_project first.")
        session = self.sessions[key]
        if session["process"].poll() is not None:
            raise RuntimeError(f"Runtime exited; inspect {session['log']}")
        return session

    def status(self, path=None):
        if path is None:
            return {
                "runtime": str(self.root),
                "backend": "ipc",
                "gui_sync": False,
                "sessions": [self.status(p) for p in self.sessions],
            }
        session = self.get(path)
        return {
            "path": str(Path(path).expanduser().resolve()),
            "pid": session["process"].pid,
            "socket": session["client"].socket,
            "version": session["version"],
            "log": str(session["log"]),
            "backend": "ipc",
            "gui_sync": False,
        }

    def close(self, path):
        key = str(Path(path).expanduser().resolve())
        session = self.get(key)
        # Runtime refuses shutdown with unsaved edits. Never force or silently save.
        session["client"].call("kiapi.backplane.Shutdown", allow_write=True)
        session["process"].wait(timeout=10)
        del self.sessions[key]
        for p in session["directory"].iterdir():
            p.unlink()
        session["directory"].rmdir()
        return {"closed": key}
