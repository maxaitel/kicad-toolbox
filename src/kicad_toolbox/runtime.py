"""Pinned, separately installed upstream runtimes; never replace stock KiCad."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from importlib.resources import files
from pathlib import Path


def manifest():
    return json.loads(files("kicad_toolbox").joinpath("releases.json").read_text())


def data_dir():
    return Path(os.environ.get("KICAD_TOOLBOX_HOME", "~/.local/share/kicad-toolbox")).expanduser()


def platform_key():
    machine = {"aarch64": "arm64", "AMD64": "x86_64"}.get(platform.machine(), platform.machine())
    return f"{platform.system().lower()}-{machine}"


def installed_root():
    return data_dir() / "runtimes" / manifest()["version"] / platform_key()


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def locate_cli(root):
    candidates = [root / "bin/kicad-cli", root / "KiCad.app/Contents/MacOS/kicad-cli"]
    for path in candidates:
        if path.is_file():
            return path
    found = sorted(root.rglob("kicad-cli"))
    found = [p for p in found if p.is_file() and "bin" in p.parts]
    if len(found) == 1:
        return found[0]
    raise FileNotFoundError(f"No unique kicad-cli under {root}")


def runtime_paths(runtime=None):
    root = Path(runtime or os.environ.get("KICAD_TOOLBOX_RUNTIME") or installed_root()).resolve()
    if not root.is_dir():
        raise FileNotFoundError("Runtime missing. Run: kicad-toolbox install --download")
    cli = locate_cli(root)
    descriptors = sorted(root.rglob("*.desc")) + sorted(root.rglob("*.protoset"))
    if len(descriptors) != 1:
        raise FileNotFoundError(f"Expected one protobuf descriptor set under {root}: {descriptors}")
    return root, cli, descriptors[0]


def install(download=False):
    if not download:
        raise ValueError("Runtime downloads are large; pass --download to opt in.")
    release = manifest()
    key = platform_key()
    if key not in release["assets"]:
        raise ValueError(
            f"No pinned runtime for {key}; supply --runtime PATH to an existing runtime."
        )
    asset = release["assets"][key]
    dest = installed_root()
    if dest.exists():
        runtime_paths(dest)
        return {"installed": str(dest), "already_present": True}
    cache = (
        Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "kicad-toolbox/downloads"
    )
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset["name"]
    if not archive.exists() or sha256(archive) != asset["sha256"]:
        partial = archive.with_suffix(archive.suffix + ".partial")
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "kicad-toolbox"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as target:
            shutil.copyfileobj(response, target)
        if sha256(partial) != asset["sha256"]:
            partial.unlink()
            raise ValueError("Upstream runtime SHA-256 mismatch; refusing installation.")
        partial.replace(archive)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=dest.parent) as directory:
        stage = Path(directory)
        with tarfile.open(archive) as bundle:
            bundle.extractall(stage, filter="data")
        # Keep the archive's complete layout and all upstream license files.
        root = next(stage.iterdir()) if len(list(stage.iterdir())) == 1 else stage
        runtime_paths(root)
        if platform.system() == "Darwin":
            # Only this checksum-verified runtime, never /Applications or unrelated files.
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(root)], check=True)
        root.rename(dest)
    return {"installed": str(dest), "sha256": asset["sha256"], "version": release["version"]}


def runtime_env():
    env = os.environ.copy()
    # Reuse an existing stock library installation; no extra multi-GB download.
    stock = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport")
    if stock.is_dir():
        for key, folder in {
            "KICAD10_SYMBOL_DIR": "symbols",
            "KICAD10_FOOTPRINT_DIR": "footprints",
            "KICAD10_3DMODEL_DIR": "3dmodels",
            "KICAD10_TEMPLATE_DIR": "template",
        }.items():
            env.setdefault(key, str(stock / folder))
    return env
