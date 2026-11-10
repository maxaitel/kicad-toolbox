"""Opt-in tests use only disposable copies of original, public test fixtures."""

import copy
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kicad_toolbox.session import Sessions

FIXTURES = Path(__file__).parent / "fixtures"
COMMON = "kiapi.common.commands."
pytestmark = pytest.mark.live


def get_items(client, doc, kind):
    response = client.call(
        COMMON + "GetItems",
        {
            "header": {"document": doc},
            "types": [kind],
        },
    )
    assert response["status"] == "IRS_OK"
    return response.get("items", [])


def stable_items(items):
    # The headless board's root object ID is session-local. Keep every saved item ID.
    result = copy.deepcopy(items)
    for item in result:
        item.pop("parent", None)
    return sorted(result, key=lambda item: item["id"]["value"])


@pytest.mark.parametrize(
    "filename,doctype,kind",
    [
        ("note.kicad_sch", "DOCTYPE_SCHEMATIC", "KOT_SCH_TEXT"),
        ("two-pads.kicad_pcb", "DOCTYPE_PCB", "KOT_PCB_FOOTPRINT"),
    ],
)
def test_edit_rollback_save_and_reopen(tmp_path, filename, doctype, kind):
    path = tmp_path / filename
    shutil.copy2(FIXTURES / filename, path)
    original = path.read_bytes()
    sessions = Sessions()
    sessions.start(path)
    client = sessions.get(path)["client"]
    doc = client.call(COMMON + "GetOpenDocuments", {"type": doctype})["documents"][0]
    header = {"document": doc}
    before = get_items(client, doc, kind)
    changed = copy.deepcopy(before[0])
    if doctype == "DOCTYPE_SCHEMATIC":
        changed["text"]["text"] = "Changed through standalone IPC"
    else:
        changed["position"]["x_nm"] = str(int(changed["position"]["x_nm"]) + 1000000)

    for action in ["CMA_DROP", "CMA_COMMIT"]:
        commit = client.call(COMMON + "BeginCommit", {"header": header}, allow_write=True)
        result = client.call(
            COMMON + "UpdateItems",
            {
                "header": header,
                "items": [changed],
            },
            allow_write=True,
        )
        assert "ISC_OK" in str(result), result
        client.call(
            COMMON + "EndCommit",
            {
                "header": header,
                "id": commit["id"],
                "action": action,
                "message": "IPC test",
            },
            allow_write=True,
        )
        actual = get_items(client, doc, kind)
        if action == "CMA_DROP":
            assert actual == before
        else:
            assert actual != before
    assert path.read_bytes() == original, "Edits must not silently save"
    # Dirty shutdown must be rejected rather than discarding a committed edit.
    with pytest.raises(RuntimeError):
        sessions.close(path)
    client.call(
        COMMON + "SaveDocument", {"document": doc}, allow_write=True, allow_format_upgrade=True
    )
    assert path.read_bytes() != original
    expected = get_items(client, doc, kind)
    sessions.close(path)
    sessions.start(path)
    client = sessions.get(path)["client"]
    doc = client.call(COMMON + "GetOpenDocuments", {"type": doctype})["documents"][0]
    assert stable_items(get_items(client, doc, kind)) == stable_items(expected)
    sessions.close(path)


def test_sketch_route_and_rollback(tmp_path):
    path = tmp_path / "two-pads.kicad_pcb"
    shutil.copy2(FIXTURES / path.name, path)
    sessions = Sessions()
    sessions.start(path)
    client = sessions.get(path)["client"]
    doc = client.call(COMMON + "GetOpenDocuments", {"type": "DOCTYPE_PCB"})["documents"][0]
    parts = get_items(client, doc, "KOT_PCB_FOOTPRINT")
    header = {"document": doc}
    commit = client.call(COMMON + "BeginCommit", {"header": header}, allow_write=True)
    response = client.call(
        "kiapi.backplane.SketchRoute",
        {
            "board": doc,
            "items": [p["id"] for p in parts],
            "preferred_layer": "BL_F_Cu",
            "time_limit_ms": 3000,
        },
        allow_write=True,
    )
    assert response.get("routed", response.get("connections_routed", 0)) > 0, response
    client.call(
        COMMON + "EndCommit",
        {
            "header": header,
            "id": commit["id"],
            "action": "CMA_DROP",
        },
        allow_write=True,
    )
    assert not get_items(client, doc, "KOT_PCB_TRACE")
    commit = client.call(COMMON + "BeginCommit", {"header": header}, allow_write=True)
    client.call(
        "kiapi.backplane.SketchRoute",
        {
            "board": doc,
            "items": [p["id"] for p in parts],
            "preferred_layer": "BL_F_Cu",
            "time_limit_ms": 3000,
        },
        allow_write=True,
    )
    client.call(
        COMMON + "EndCommit",
        {
            "header": header,
            "id": commit["id"],
            "action": "CMA_COMMIT",
        },
        allow_write=True,
    )
    assert get_items(client, doc, "KOT_PCB_TRACE")
    client.call(
        COMMON + "SaveDocument", {"document": doc}, allow_write=True, allow_format_upgrade=True
    )
    assert "(segment" in path.read_text()
    sessions.close(path)


def test_v10_downgrade_exports(tmp_path):
    sessions = Sessions()
    for name, kind in [("note.kicad_sch", "sch"), ("two-pads.kicad_pcb", "pcb")]:
        path = tmp_path / name
        shutil.copy2(FIXTURES / name, path)
        sessions.start(path)
        client = sessions.get(path)["client"]
        doctype = "DOCTYPE_PCB" if kind == "pcb" else "DOCTYPE_SCHEMATIC"
        doc = client.call(COMMON + "GetOpenDocuments", {"type": doctype})["documents"][0]
        client.call(
            COMMON + "SaveDocument", {"document": doc}, allow_write=True, allow_format_upgrade=True
        )
        sessions.close(path)
        output = tmp_path / (f"v10-{name}" if kind == "pcb" else "v10-schematic")
        subprocess.run(
            [
                str(sessions.cli),
                kind,
                "downgrade",
                "--target",
                "10.0",
                "--output",
                str(output),
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        exported = output if kind == "pcb" else output / name
        assert exported.exists()
        stock = Path(
            os.environ.get(
                "KICAD_TOOLBOX_STOCK_CLI", "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
            )
        )
        if stock.exists():
            args = (
                ["pcb", "drc", "--output", str(tmp_path / "stock-drc.rpt"), str(exported)]
                if kind == "pcb"
                else [
                    "sch",
                    "export",
                    "svg",
                    "--output",
                    str(tmp_path / "stock-svg"),
                    str(exported),
                ]
            )
            subprocess.run([str(stock), *args], check=True, capture_output=True, timeout=30)
