import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.live


def test_stdio_discovery_and_project_lifecycle(tmp_path):
    path = tmp_path / "note.kicad_sch"
    shutil.copy2(Path(__file__).parent / "fixtures/note.kicad_sch", path)

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "kicad_toolbox.cli", "serve"]
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "runtime_status",
                "start_project",
                "api_schema",
                "ipc_call",
                "close_project",
            }
            status = await session.call_tool("runtime_status")
            assert not status.isError
            started = await session.call_tool("start_project", {"path": str(path)})
            assert not started.isError
            version = await session.call_tool(
                "ipc_call",
                {
                    "path": str(path),
                    "type_name": "kiapi.common.commands.GetVersion",
                    "message": {},
                },
            )
            assert not version.isError
            assert "10.99" in json.dumps(version.model_dump())
            rejected = await session.call_tool(
                "ipc_call",
                {
                    "path": str(path),
                    "type_name": "kiapi.common.commands.SaveDocument",
                    "message": {},
                },
            )
            assert rejected.isError
            closed = await session.call_tool(
                "close_project",
                {
                    "path": str(path),
                    "allow_close": True,
                },
            )
            assert not closed.isError

    asyncio.run(run())
