---
name: kicad-toolbox
description: Use the standalone Backplane KiCad headless IPC bridge from Codex for board and schematic queries, transactions, edits and sketch routing without the Backplane app.
---

# KiCad toolbox

Use the connected `kicad_toolbox` MCP tools. This is a low-level IPC bridge,
not a schematic/PCB viewer or the Backplane app. Existing `kicad` MCP tools
remain useful for high-level operations, rule checks and exports.

1. Call `runtime_status`, then `start_project` with the exact absolute
   design path the user intends. Never guess main versus a worktree or launch
   two writers on the same design. Keep stock GUI editing separate.
2. Use `api_schema` to inspect command fields and nested types. Request names
   and enums come from the runtime's descriptor; never invent them.
3. Obtain the correct document with `kiapi.common.commands.GetOpenDocuments`.
   Use it in the item header for queries and mutations. Treat nanometres and
   protobuf JSON integer strings carefully.
4. Preserve item IDs, full definitions and unrelated fields when editing.
   Use `BeginCommit`, make edits, then `EndCommit` with `CMA_COMMIT` or
   `CMA_DROP`. Set `allow_write=true` on each mutating call. Inspect per-item
   statuses as well as API success. Query and verify the resulting geometry.
5. The pinned runtime is KiCad 10.99. `SaveDocument` requires the explicit
   `allow_format_upgrade=true` opt-in as well as `allow_write=true`.
   Prefer disposable copies when testing. For stock 10.0 compatibility, export
   with the runtime's `pcb downgrade` / `sch downgrade`, inspect its report
   and reopen with stock KiCad. Preserve adjacent .backplane.json companions.
6. Save milestones deliberately; memory is not durable when the MCP parent
   exits. Close clean sessions using `close_project(allow_close=true)`.
   Never force-close dirty work. Headless IPC does not synchronize stock GUI.

For schema/transport commands outside MCP, run `kicad-toolbox --help`.
The `start_project` result includes an explicit `KICAD_API_SOCKET` URL for
KiStack placement scripts. Backplane's `viewer_show` and history UI are absent;
use file readback, renders and Git instead.
