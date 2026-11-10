"""Dynamic protobuf IPC, using the schema shipped with the exact runtime."""

from threading import Lock

import pynng
from google.protobuf import descriptor_pb2, descriptor_pool, empty_pb2, json_format, message_factory

# Exact namespaces: a new/unknown Get* command never becomes implicitly read-only.
READS = {
    "kiapi.common.commands.Ping",
    "kiapi.common.commands.GetVersion",
    "kiapi.common.commands.GetKiCadBinaryPath",
    "kiapi.common.commands.GetOpenDocuments",
    "kiapi.common.commands.GetItems",
    "kiapi.common.commands.GetTextExtents",
    "kiapi.common.commands.GetTextAsShapes",
    "kiapi.common.commands.GetLibraryStatus",
    "kiapi.common.commands.GetLibraryStatuses",
    "kiapi.common.commands.GetBoundingBox",
    "kiapi.common.commands.GetItemsById",
    "kiapi.common.commands.GetDocumentModifiedState",
    "kiapi.board.commands.GetItems",
    "kiapi.board.commands.GetBoardEnabledLayers",
    "kiapi.board.commands.GetBoardStackup",
    "kiapi.board.commands.GetNets",
    "kiapi.board.commands.GetSelection",
    "kiapi.board.commands.GetPadShapesAsPolygons",
    "kiapi.board.commands.GetBoardOrigin",
    "kiapi.board.commands.GetBoardDesignRules",
    "kiapi.board.commands.GetBoardCustomRules",
    "kiapi.board.commands.GetBoardPlotSettings",
    "kiapi.board.commands.GetBoardConnectivity",
    "kiapi.board.commands.GetBoardNetlist",
    "kiapi.sch.commands.GetItems",
    "kiapi.sch.commands.GetHierarchy",
    "kiapi.sch.commands.GetNetlist",
    "kiapi.schematic.commands.GetItems",
    "kiapi.schematic.commands.GetHierarchy",
    "kiapi.schematic.commands.GetNetlist",
}


class Protocol:
    def __init__(self, descriptor_path):
        self.pool = descriptor_pool.DescriptorPool()
        # Empty responses are used on the wire but not necessarily imported by api.desc.
        self.pool.AddSerializedFile(empty_pb2.DESCRIPTOR.serialized_pb)
        descriptor_set = descriptor_pb2.FileDescriptorSet()
        descriptor_set.ParseFromString(descriptor_path.read_bytes())
        pending = list(descriptor_set.file)
        while pending:
            remaining = []
            for file in pending:
                try:
                    self.pool.Add(file)
                except TypeError:
                    remaining.append(file)
            if len(remaining) == len(pending):
                raise ValueError("Descriptor set has unresolved imports.")
            pending = remaining
        self.messages = {}

        def collect(messages):
            for message in messages:
                self.messages[message.full_name] = message
                collect(message.nested_types)

        for file in descriptor_set.file:
            collect(self.pool.FindFileByName(file.name).message_types_by_name.values())

    def new(self, name, data=None):
        result = message_factory.GetMessageClass(self.pool.FindMessageTypeByName(name))()
        if data is not None:
            json_format.ParseDict(data, result, descriptor_pool=self.pool)
        return result

    def json(self, message):
        return json_format.MessageToDict(
            message, preserving_proto_field_name=True, descriptor_pool=self.pool
        )

    def describe(self, name):
        desc = self.pool.FindMessageTypeByName(name)
        fields = []
        for f in desc.fields:
            item = {"name": f.name, "repeated": f.is_repeated}
            if f.message_type:
                item["message"] = f.message_type.full_name
            elif f.enum_type:
                item["enum"] = {v.name: v.number for v in f.enum_type.values}
            else:
                item["scalar_type"] = f.type
            fields.append(item)
        return {"type": name, "read_only": name in READS, "fields": fields}

    def search(self, query=""):
        return [name for name in sorted(self.messages) if query.lower() in name.lower()]


class Client:
    def __init__(self, protocol, socket, timeout_ms=15000):
        self.protocol = protocol
        self.socket = socket if socket.startswith("ipc://") else "ipc://" + socket
        self.timeout_ms = timeout_ms
        self.token = ""
        self.lock = Lock()

    def call(self, name, data=None, *, allow_write=False, allow_format_upgrade=False):
        if name not in READS and not allow_write:
            raise PermissionError(f"{name} requires allow_write=true (CLI: --write).")
        if name == "kiapi.common.commands.SaveDocument" and not allow_format_upgrade:
            raise PermissionError(
                "Saving with the pinned 10.99 runtime can upgrade native file formats. "
                "Use allow_format_upgrade=true (CLI: --allow-format-upgrade) explicitly, "
                "or work on a copy and export with kicad-cli pcb/sch downgrade."
            )
        command = self.protocol.new(name, data or {})
        with self.lock:
            envelope = self.protocol.new("kiapi.common.ApiRequest")
            envelope.header.client_name = "tools.kicad-toolbox"
            envelope.header.kicad_token = self.token
            envelope.message.Pack(command)
            # Nonblocking dial plus bounded send/receive avoids hanging on dead sockets.
            with pynng.Req0(
                send_timeout=self.timeout_ms,
                recv_timeout=self.timeout_ms,
                recv_max_size=64 * 1024 * 1024,
            ) as connection:
                connection.dial(self.socket, block=False)
                connection.send(envelope.SerializeToString())
                reply = self.protocol.new("kiapi.common.ApiResponse")
                reply.ParseFromString(connection.recv())
            status = reply.status.status
            status_name = (
                reply.status.DESCRIPTOR.fields_by_name["status"]
                .enum_type.values_by_number[status]
                .name
            )
            if status_name != "AS_OK":
                raise RuntimeError(f"{status_name}: {reply.status.error_message}")
            self.token = reply.header.kicad_token
            if not reply.message.type_url:
                return {}
            response = self.protocol.new(reply.message.TypeName())
            if not reply.message.Unpack(response):
                raise RuntimeError("Unable to decode IPC response.")
            return self.protocol.json(response)

    def ping(self):
        return self.call("kiapi.common.commands.GetVersion")
