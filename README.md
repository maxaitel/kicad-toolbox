# kicad-toolbox

Use Backplane's KiCad fork from **Codex or any stdio MCP client**, without
installing or running the Backplane app.

The toolbox installs a checksum-pinned upstream runtime separately from stock
KiCad, starts headless IPC servers for explicit design paths, discovers their
protobuf schemas, and sends JSON commands over local NNG sockets. It adds
schematic and board IPC alongside your existing high-level KiCad tools.

## Status

Version 0.1 is a small, working **low-level bridge**, not a replacement for the
Backplane app or a complete high-level EE assistant.

- Tested locally on Apple Silicon macOS with Backplane KiCad **10.99.0-bp.7**.
- Verified real schematic text edits, PCB footprint movement, commit/rollback,
  explicit save/reopen, rejection of dirty shutdown, and sketch routing of a
  simple two-pad board.
- Verified KiCad 10.0 downgrade exports reopen in stock KiCad 10.0.6.
- The runtime supplies 500 protobuf message schemas. A listed message is not
  a claim that every corresponding command is implemented or tested.
- Linux x86-64 is in the pinned download catalog but has not been tested locally.
  Other platforms require an existing compatible runtime via `--runtime PATH`.
- No integrated schematic/PCB viewer, cross-probing UI, design history UI,
  Backplane agent orchestration, circuit simulation, or supplier service.
- Headless IPC **does not synchronize a separate stock KiCad GUI**. Save and
  reopen/export deliberately. Keep a single writer for each design.
- KiStack's placement scripts can connect using the returned socket URL, but
  their complete optimization workflow is separate from this bridge's tests.
  A read-only `pin_sides.py` query was also verified against the live two-pad
  fixture using the separately installed KiStack skill.

### Native format compatibility

The pinned runtime is a **10.99 development build**. Native saves can upgrade
designs beyond stock KiCad 10.0.6. `SaveDocument` therefore requires both
`allow_write=true` and `allow_format_upgrade=true` (CLI:
`--write --allow-format-upgrade`). Keep a backup or work on a copy.

The fork provides `pcb downgrade` and `sch downgrade` for stable exports:

```sh
# Use the bin/kicad-cli path printed by "doctor".
/path/to/runtime/bin/kicad-cli pcb downgrade --target 10.0 \
  --output /path/to/new-stable-board.kicad_pcb /path/to/edited.kicad_pcb
/path/to/runtime/bin/kicad-cli sch downgrade --target 10.0 \
  --output /path/to/new-stable-schematic-folder /path/to/edited.kicad_sch
```

Review the conversion report. The toolbox never adds `--force` or
`--drop-approximations`. Preserve adjacent `<native-filename>.backplane.json`
companions when copying designs. Do not assume every future feature can be
represented in an older format.

## Setup

Requires Python **3.12+**, [uv](https://docs.astral.sh/uv/), a supported local
machine, and internet for initial dependencies and runtime download. No API keys,
Backplane account, cloud service, or physical hardware are required for local IPC.
Codex needs its own normal account/setup if you use it as the MCP client.

```sh
git clone https://github.com/maxaitel/kicad-toolbox.git
cd kicad-toolbox
uv sync --locked
uv run kicad-toolbox install --download
uv run kicad-toolbox doctor
```

The runtime download is explicitly opt-in: about **432 MB** on macOS and **1.02 GB**
on Linux, plus extraction space. Dependencies and runtimes are not committed.
Installation validates the pinned SHA-256, extracts into a staging directory,
retains upstream licenses, and refuses to replace an existing runtime directory.
On macOS it removes quarantine only from this verified, separately installed
runtime. It never replaces `/Applications/KiCad`.

Default locations:

| Content | Location |
| --- | --- |
| Runtime and local session logs | `~/.local/share/kicad-toolbox/` |
| Download cache | `~/.cache/kicad-toolbox/downloads/` |
| Short-lived private IPC socket directory | OS temporary directory |

`KICAD_TOOLBOX_HOME` changes the data location. `XDG_CACHE_HOME` changes the
download cache base. `KICAD_TOOLBOX_RUNTIME` or global CLI `--runtime PATH`
selects an existing unpacked runtime with its matching descriptor set.

An existing stock macOS installation supplies symbol, footprint and 3D libraries
through `KICAD10_*_DIR` defaults. Elsewhere, install KiCad libraries separately
and set those variables yourself. Simple fixture tests need no external libraries;
placing real library parts and detailed 3D exports do.

## Connect Codex

From this checkout:

```sh
codex mcp add kicad_toolbox -- "$(pwd)/.venv/bin/kicad-toolbox" serve
```

Alternatively, `uv run kicad-toolbox codex-config` prints a TOML fragment for
your user or trusted project's Codex configuration without modifying settings.
Reconnect/restart the MCP connection so Codex discovers the tools. Keep your
existing `kicad` MCP registration for its high-level tools and CLI exports.
Do not mix concurrent writers on the same files.

The bridge exposes five tools:

| Tool | Purpose |
| --- | --- |
| `runtime_status` | Runtime, sessions, socket URLs and actual backend |
| `start_project(path)` | Start an owned headless server on an explicit existing design |
| `api_schema(query, type_name)` | Discover types and inspect exact fields/enums |
| `ipc_call(path, type_name, message, ...)` | Send protobuf JSON to that exact session |
| `close_project(path, allow_close)` | Stop a clean session; refuse unsaved changes |

Sessions are in-memory and owned by the MCP process. Explicitly save milestones:
the runtime's parent-PID watchdog exits when the MCP process disappears, so
unsaved memory is not durable. No session auto-saves, force-closes, or silently
falls back to SWIG.

## IPC workflow

1. Start an absolute `.kicad_pro`, `.kicad_pcb`, or `.kicad_sch` path.
   There is no implicit selection of main versus a worktree.
2. Inspect `kiapi.common.commands.GetOpenDocuments` and call it with
   `{"type":"DOCTYPE_PCB"}` or `{"type":"DOCTYPE_SCHEMATIC"}`.
3. Use the returned document verbatim in `{"header":{"document": ...}}`
   for `GetItems`, `BeginCommit`, `UpdateItems` and related commands.
4. Query the item types you need, preserving returned item IDs and definitions.
   JSON `Any` objects use `@type`. Distances are generally nanometres;
   protobuf JSON encodes 64-bit integers as strings.
5. For edits, set `allow_write=true` on each call. Start a commit, edit, then
   `EndCommit` with `CMA_COMMIT` or `CMA_DROP`. Check both the API-level
   result and per-item statuses. Route items can remain pending until commit.
6. Save deliberately with the format-upgrade opt-in, or discard changes.
   Re-query after edits and after reopen. A board's session-root ID may change
   across reopen; native item IDs and geometry should be checked independently.
7. Close the clean session with `allow_close=true`.

Unknown commands require write opt-in, even if their name starts with `Get`.
The explicit read allowlist lives in `protocol.py`. Schema discovery exposes
request, response, and item types; runtime errors remain visible to the caller.
The raw bridge is intended for a trusted local agent, not as an authorization
boundary for untrusted clients.

CLI discovery and calls:

```sh
uv run kicad-toolbox schema SketchRoute
uv run kicad-toolbox schema --type kiapi.backplane.SketchRoute
uv run kicad-toolbox call --socket ipc:///path/from/start_project/api.sock \
  --type kiapi.common.commands.GetVersion
```

To use a KiStack placement script against an already-open toolbox board:

```sh
KICAD_API_SOCKET=ipc:///path/from/start_project/api.sock \
  /path/to/layout-placement/scripts/run.sh pin_sides.py /absolute/board.kicad_pcb J1
```

That script has its own locked Python environment and requirements. Install
KiStack separately with `npx skills add BackplaneWorks/kistack`. Its viewer and
history instructions refer to Backplane-specific tools; use explicit file paths,
readback, renders and Git in Codex. The toolbox does not emulate `viewer_show`.

## Development and validation

```sh
uv sync --locked --group dev
uv run pytest -q               # transport, schema and opt-in checks; no runtime download
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest --live -q        # explicit local runtime processes; fixture copies only
```

The live suite covers the operations listed under Status, using original tiny
fixtures in `tests/fixtures/`. When stock macOS KiCad is present it independently
checks downgraded output with stock schematic SVG export and PCB DRC.
`KICAD_TOOLBOX_STOCK_CLI` can select another stock binary. These fixtures establish
transport and basic operations, not RF, electrical, manufacturing, routing-quality,
or large-board qualification. Existing personal designs are never used as fixtures.

## Upstream and licensing

This repository's original bridge code and original fixtures are MIT licensed.
It does **not** vendor or relicense KiCad, Backplane, KiStack, or their binaries.
The downloaded KiCad fork remains GPL-3.0-or-later with its upstream notices and
corresponding source available from the pinned release.

- [Backplane KiCad source and releases](https://github.com/BackplaneWorks/Backplane_KiCad)
- [Pinned bp.7 release](https://github.com/BackplaneWorks/Backplane_KiCad/releases/tag/backplane-kicad-v10.99.0-bp.7)
- [Upstream IPC contract](https://github.com/BackplaneWorks/Backplane_KiCad/blob/main/BACKPLANE_IPC.md)
- [KiStack skills](https://github.com/BackplaneWorks/kistack)

Updating the runtime requires reviewing a release's API and file-format changes,
updating `src/kicad_toolbox/releases.json` with asset digests and sizes from the
release API, then rerunning the live suite. Installs are pinned, not auto-updating.
