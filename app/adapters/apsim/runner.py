"""APSIM runtime adapter. Order: APSIM_COMMAND (e.g. `dotnet Models.dll`) -> docker image -> unavailable.

Isolation: each run gets a fresh temp workdir containing ONLY the server-generated .apsimx and its MET file;
a timeout is enforced and the process is killed on cancel. Docker runs use --network none. The direct command
has no sandbox beyond the temp dir + timeout (run the worker as an unprivileged user / in a container).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


class RuntimeUnavailable(Exception):
    pass


class RunFailed(Exception):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


class Cancelled(Exception):
    pass


@dataclass
class RunResult:
    db_path: Path
    log: str
    seconds: float
    runtime: dict = field(default_factory=dict)


_version_cache: dict[str, str | None] = {}


def resolve(apsim_command: str | None, docker_image: str | None) -> tuple[str, list[str]]:
    if apsim_command:
        argv = shlex.split(apsim_command)
        if not argv or shutil.which(argv[0]) is None and not Path(argv[0]).exists():
            raise RuntimeUnavailable(f"APSIM runtime unavailable: APSIM_COMMAND executable '{argv[0] if argv else ''}' "
                                     "not found")
        return "command", argv
    if docker_image and shutil.which("docker"):
        return "docker", ["docker", "run", "--rm", "--network", "none", "-v", "{workdir}:/w", docker_image]
    raise RuntimeUnavailable("APSIM runtime unavailable: set APSIM_COMMAND (e.g. 'dotnet /opt/apsim/Models.dll') or "
                             "APSIM_DOCKER_IMAGE with docker installed on the worker")


def version(mode: str, argv: list[str]) -> str | None:
    key = " ".join(argv)
    if key in _version_cache:
        return _version_cache[key]
    v = None
    if mode == "command":
        try:
            p = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=60)
            v = (p.stdout or p.stderr).strip().splitlines()[-1] if (p.stdout or p.stderr).strip() else None
        except (OSError, subprocess.TimeoutExpired):
            v = None
    _version_cache[key] = v
    return v


def run(apsimx_text: str, met_bytes: bytes, workdir: Path, *, apsim_command: str | None, docker_image: str | None,
        timeout: int, met_filename: str = "weather.met", should_cancel=None, log_cb=None) -> RunResult:
    mode, argv = resolve(apsim_command, docker_image)
    workdir.mkdir(parents=True, exist_ok=True)
    sim = workdir / "simulation.apsimx"
    sim.write_text(apsimx_text, encoding="utf-8")
    (workdir / met_filename).write_bytes(met_bytes)
    if mode == "docker":
        cmd = [a.replace("{workdir}", str(workdir)) for a in argv] + ["/w/simulation.apsimx"]
    else:
        cmd = [*argv, str(sim)]
    ver = version(mode, argv)
    runtime = {"mode": mode, "command": [Path(cmd[0]).name, *[Path(a).name if "/" in a else a for a in cmd[1:]]],
               "apsim_version": ver, "timeout_seconds": timeout}
    if log_cb:
        log_cb(f"starting APSIM ({mode}, version {ver}) in isolated workdir")
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1")
    t0 = time.time()
    out_path = workdir / "apsim.log"
    with open(out_path, "w") as out:
        p = subprocess.Popen(cmd, cwd=workdir, stdout=out, stderr=subprocess.STDOUT, env=env)
        while p.poll() is None:
            if time.time() - t0 > timeout:
                p.kill()
                p.wait()
                raise RunFailed(f"APSIM timed out after {timeout} s", out_path.read_text(errors="replace"))
            if should_cancel and should_cancel():
                p.kill()
                p.wait()
                raise Cancelled("cancelled by user")
            time.sleep(0.25)
    secs = round(time.time() - t0, 2)
    log = out_path.read_text(errors="replace")
    db = workdir / "simulation.db"
    if p.returncode != 0:
        raise RunFailed(f"APSIM exited with code {p.returncode}", log)
    if not db.exists():
        raise RunFailed("APSIM finished but produced no output database", log)
    return RunResult(db_path=db, log=log, seconds=secs, runtime=runtime)
