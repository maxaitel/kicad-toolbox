"""Stdio MCP bridge. Raw API access stays explicit and schema-driven."""

from mcp.server.fastmcp import FastMCP

from .protocol import READS
from .session import Sessions


def create_server(runtime=None):
    sessions = Sessions(runtime)
    server = FastMCP(
        "kicad-toolbox",
        instructions=(
            "Standalone Backplane KiCad headless IPC. Start an explicit design path, inspect "
            "schemas, then send protobuf-JSON commands. Mutations require allow_write=true. "
            "Use BeginCommit/EndCommit with rollback for multi-step edits and SaveDocument "
            "only when asked to persist. Close clean sessions. Headless IPC does not synchronize "
            "a separate stock KiCad GUI. Native files and .backplane.json companions must stay "
            "together. Existing KiCad MCP remains available for high-level tools and exports. "
            "Do not open the same design for writing in multiple sessions or the GUI."
        ),
    )

    @server.tool()
    def runtime_status() -> dict:
        """Report the actual runtime, owned sessions and IPC socket URLs."""
        return sessions.status()

    @server.tool()
    def start_project(path: str) -> dict:
        """Open an explicitly named existing KiCad project/board/schematic in headless IPC.

        This starts a local runtime process. Does not save design changes or choose main
        versus a worktree implicitly. Use absolute paths. Return includes KICAD_API_SOCKET
        for KiStack placement scripts run from another terminal.
        """
        return sessions.start(path)

    @server.tool()
    def api_schema(query: str = "", type_name: str = "") -> dict:
        """Discover exact protobuf type names, or inspect a message's fields and enums.

        Names come from the installed runtime's descriptor set. Listing a message does
        not establish that the runtime implements it. Use the returned field schemas
        instead of guessing JSON keys. Unknown commands require allow_write=true.
        """
        if type_name:
            return sessions.protocol.describe(type_name)
        return {"types": sessions.protocol.search(query)}

    @server.tool()
    def ipc_call(
        path: str,
        type_name: str,
        message: dict,
        allow_write: bool = False,
        allow_format_upgrade: bool = False,
    ) -> dict:
        """Send one command to an already-started exact design path.

        JSON uses protobuf field names; enum names are accepted and KiCad distances
        are usually integer nanometres. ALL mutations (including commit, rollback,
        routing, save and document lifecycle) require allow_write=true. Unknown/new
        commands require it too. A rejected command is surfaced, with no file fallback.
        SaveDocument also requires allow_format_upgrade=true: the pinned runtime is
        10.99 and can upgrade files beyond stock KiCad 10.0. Use copies or downgrade.
        """
        if type_name not in READS and not allow_write:
            raise PermissionError("Set allow_write=true explicitly for this command.")
        return sessions.get(path)["client"].call(
            type_name,
            message,
            allow_write=allow_write,
            allow_format_upgrade=allow_format_upgrade,
        )

    @server.tool()
    def close_project(path: str, allow_close: bool = False) -> dict:
        """Close this toolbox's server only; refuses unsaved changes, never forces a close."""
        if not allow_close:
            raise PermissionError("Set allow_close=true to stop this session.")
        return sessions.close(path)

    return server
