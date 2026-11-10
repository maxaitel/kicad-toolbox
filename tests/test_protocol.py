import threading
import time
from tempfile import TemporaryDirectory

import pynng
import pytest
from google.protobuf import any_pb2, descriptor_pb2

from kicad_toolbox.protocol import Client, Protocol
from kicad_toolbox.runtime import install


@pytest.fixture
def protocol(tmp_path):
    """An independent tiny peer schema, not a copied upstream descriptor."""
    bundle = descriptor_pb2.FileDescriptorSet()
    bundle.file.add().ParseFromString(any_pb2.DESCRIPTOR.serialized_pb)
    file = bundle.file.add(name="test.proto", package="kiapi.common", syntax="proto3")
    file.dependency.append("google/protobuf/any.proto")
    status = file.enum_type.add(name="ApiStatusCode")
    status.value.add(name="AS_OK", number=0)
    status.value.add(name="AS_BAD_REQUEST", number=1)

    def message(name, fields):
        result = file.message_type.add(name=name)
        for number, (field_name, kind, target) in enumerate(fields, 1):
            f = result.field.add(name=field_name, number=number, type=kind)
            if target:
                f.type_name = target

    message("Header", [("client_name", 9, ""), ("kicad_token", 9, "")])
    message("Status", [("status", 14, ".kiapi.common.ApiStatusCode"), ("error_message", 9, "")])
    message(
        "ApiRequest",
        [("header", 11, ".kiapi.common.Header"), ("message", 11, ".google.protobuf.Any")],
    )
    message(
        "ApiResponse",
        [
            ("header", 11, ".kiapi.common.Header"),
            ("message", 11, ".google.protobuf.Any"),
            ("status", 11, ".kiapi.common.Status"),
        ],
    )
    commands = bundle.file.add(
        name="commands.proto", package="kiapi.common.commands", syntax="proto3"
    )
    commands.message_type.add(name="GetVersion")
    response = commands.message_type.add(name="GetVersionResponse")
    response.field.add(name="version", number=1, type=9)
    descriptor = tmp_path / "api.desc"
    descriptor.write_bytes(bundle.SerializeToString())
    return Protocol(descriptor)


@pytest.mark.parametrize(
    "name",
    [
        "kiapi.common.commands.SaveDocument",
        "kiapi.common.commands.UpdateItems",
        "kiapi.backplane.SketchRoute",
        "untrusted.namespace.GetSomething",
    ],
)
def test_mutation_and_unknown_names_are_blocked_before_transport(protocol, name):
    with pytest.raises(PermissionError, match="allow_write"):
        Client(protocol, "/does/not/exist", 50).call(name, {})


def test_save_needs_separate_format_opt_in(protocol):
    with pytest.raises(PermissionError, match="format"):
        Client(protocol, "/does/not/exist", 50).call(
            "kiapi.common.commands.SaveDocument", {}, allow_write=True
        )


def test_download_requires_opt_in():
    with pytest.raises(ValueError, match="--download"):
        install()


@pytest.mark.parametrize("error", [False, True])
def test_real_nng_envelopes_tokens_and_api_errors(protocol, tmp_path, error):
    directory = TemporaryDirectory(prefix="ktb-peer-")
    socket = "ipc://" + directory.name + "/peer.sock"
    failures = []

    def peer():
        try:
            with pynng.Rep0(listen=socket, recv_timeout=3000, send_timeout=3000) as server:
                for _ in range(2 if not error else 1):
                    request = protocol.new("kiapi.common.ApiRequest")
                    request.ParseFromString(server.recv())
                    assert request.header.client_name == "tools.kicad-toolbox"
                    reply = protocol.new("kiapi.common.ApiResponse")
                    reply.header.kicad_token = "test-token"
                    if error:
                        reply.status.status = 1
                        reply.status.error_message = "refused by test peer"
                    else:
                        reply.message.Pack(
                            protocol.new(
                                "kiapi.common.commands.GetVersionResponse", {"version": "test"}
                            )
                        )
                    server.send(reply.SerializeToString())
        except (AssertionError, pynng.NNGException, ValueError) as exc:
            failures.append(exc)

    thread = threading.Thread(target=peer)
    thread.start()
    client = Client(protocol, socket, 3000)
    if error:
        with pytest.raises(RuntimeError, match="refused by test peer"):
            client.ping()
    else:
        assert client.ping() == {"version": "test"}
        assert client.token == "test-token"
        assert client.ping() == {"version": "test"}
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not failures
    directory.cleanup()


def test_dead_socket_is_bounded(protocol, tmp_path):
    start = time.monotonic()
    with pytest.raises(pynng.NNGException):
        Client(protocol, str(tmp_path / "absent.sock"), 100).ping()
    assert time.monotonic() - start < 2


def test_schema_validates_fields(protocol):
    assert protocol.describe("kiapi.common.commands.GetVersion")["read_only"]
    with pytest.raises(Exception, match="no field named"):
        protocol.new("kiapi.common.commands.GetVersion", {"invented": 1})
