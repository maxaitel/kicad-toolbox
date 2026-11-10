"""CLI entry point and portable Codex configuration output."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pynng
from google.protobuf.json_format import ParseError

from .protocol import Client, Protocol
from .runtime import install, manifest, runtime_env, runtime_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime", help="Existing runtime root (otherwise pinned managed install)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    installer = commands.add_parser("install", help="Install pinned upstream runtime separately")
    installer.add_argument("--download", action="store_true", help="Allow large runtime download")
    commands.add_parser("doctor", help="Inspect local runtime version, capabilities and schema")
    commands.add_parser("serve", help="Run the MCP server on stdio")
    schema = commands.add_parser("schema", help="Find message types or inspect their fields")
    schema.add_argument("query", nargs="?", default="")
    schema.add_argument("--type", dest="type_name")
    call = commands.add_parser("call", help="Send JSON command to an explicit IPC socket")
    call.add_argument("--socket", required=True)
    call.add_argument("--type", dest="type_name", required=True)
    call.add_argument("--json", default="{}", help="Protobuf JSON, or @path/to/file.json")
    call.add_argument("--write", action="store_true", help="Opt in to this mutating command")
    call.add_argument(
        "--allow-format-upgrade",
        action="store_true",
        help="Allow SaveDocument to write the runtime's newer native file format",
    )
    call.add_argument("--timeout-ms", type=int, default=15000)
    commands.add_parser("codex-config", help="Print a TOML fragment; never edits settings")
    args = parser.parse_args()
    try:
        if args.command == "install":
            result = install(download=args.download)
        elif args.command == "codex-config":
            # JSON-quoted paths are valid TOML basic strings, including paths with spaces.
            executable = str(Path(sys.executable).parent / "kicad-toolbox")
            argv = (["--runtime", args.runtime] if args.runtime else []) + ["serve"]
            print(
                "[mcp_servers.kicad_toolbox]\ncommand = "
                + json.dumps(executable)
                + "\nargs = "
                + json.dumps(argv)
                + "\nstartup_timeout_sec = 60\ntool_timeout_sec = 180"
            )
            return
        elif args.command == "serve":
            from .server import create_server

            create_server(args.runtime).run(transport="stdio")
            return
        else:
            root, cli, descriptor = runtime_paths(args.runtime)
            protocol = Protocol(descriptor)
            if args.command == "doctor":
                version = subprocess.check_output(
                    [str(cli), "--version"], env=runtime_env(), text=True, timeout=20
                ).strip()
                metadata = root / "share/backplane/runtime.json"
                result = {
                    "runtime": str(root),
                    "cli": str(cli),
                    "version": version,
                    "descriptor": str(descriptor),
                    "schema_messages": len(protocol.messages),
                    "metadata": json.loads(metadata.read_text()) if metadata.exists() else None,
                    "pinned_release": manifest()["tag"],
                    "backend": "ipc",
                    "gui_sync": False,
                }
            elif args.command == "schema":
                result = (
                    protocol.describe(args.type_name)
                    if args.type_name
                    else protocol.search(args.query)
                )
            else:
                raw = Path(args.json[1:]).read_text() if args.json.startswith("@") else args.json
                result = Client(protocol, args.socket, args.timeout_ms).call(
                    args.type_name,
                    json.loads(raw),
                    allow_write=args.write,
                    allow_format_upgrade=args.allow_format_upgrade,
                )
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, pynng.NNGException, ParseError) as exc:
        print(f"kicad-toolbox: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
