"""Live framing tests; run with native CE and ce_mcp_bridge.lua already loaded.

Uses only ping and unknown-method requests; does not attach or modify a process.
Run while other MCP clients are disconnected (the bridge serves one at a time).
"""

import json
import os
import socket
import struct
import time
import unittest


class NativeTCPTests(unittest.TestCase):
    def connect(self):
        sock = socket.create_connection(
            (os.getenv("CE_MCP_HOST", "127.0.0.1"), int(os.getenv("CE_MCP_PORT", "9876"))),
            timeout=5,
        )
        self.addCleanup(sock.close)
        return sock

    @staticmethod
    def frame(method="ping", request_id=1, **params):
        body = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id},
            ensure_ascii=False,
        ).encode("utf-8")
        return struct.pack("<I", len(body)) + body

    def receive(self, sock):
        def exact(size):
            chunks = bytearray()
            while len(chunks) < size:
                part = sock.recv(size - len(chunks))
                self.assertTrue(part, "Connection closed before the complete response")
                chunks.extend(part)
            return chunks

        size = struct.unpack("<I", exact(4))[0]
        self.assertLess(size, 32 * 1024 * 1024)
        return json.loads(exact(size))

    def ping(self, sock, request_id=1):
        sock.sendall(self.frame(request_id=request_id))
        result = self.receive(sock)
        self.assertEqual(result["id"], request_id)
        self.assertTrue(result["result"]["success"])
        return result

    def test_fragmented_header_and_body(self):
        sock = self.connect()
        frame = self.frame()
        for byte in frame:
            sock.sendall(bytes([byte]))
            time.sleep(0.001)
        self.assertTrue(self.receive(sock)["result"]["success"])

    def test_pipelined_requests(self):
        sock = self.connect()
        sock.sendall(self.frame(request_id=1) + self.frame(request_id=2))
        for request_id in (1, 2):
            response = self.receive(sock)
            self.assertEqual(response["id"], request_id)
            self.assertTrue(response["result"]["success"])

    def test_large_utf8_request(self):
        sock = self.connect()
        sock.sendall(self.frame(ignored="é🔎" * 60000))
        self.assertTrue(self.receive(sock)["result"]["success"])

    def test_reconnect(self):
        for request_id in range(5):
            with self.connect() as sock:
                self.ping(sock, request_id)

    def test_disconnect_during_header_or_body(self):
        frame = self.frame()
        for size in (1, 3, 4, 10):
            with self.connect() as sock:
                sock.sendall(frame[:size])
            with self.connect() as sock:
                self.ping(sock)

    def test_invalid_lengths_disconnect(self):
        for size in (0, 32 * 1024 * 1024, 0xFFFFFFFF):
            with self.connect() as sock:
                sock.sendall(struct.pack("<I", size))
                self.assertEqual(sock.recv(1), b"")
            with self.connect() as sock:
                self.ping(sock)

    def test_unknown_method_keeps_connection(self):
        sock = self.connect()
        sock.sendall(self.frame(method="not_a_bridge_method"))
        self.assertEqual(self.receive(sock)["error"]["code"], -32601)
        self.ping(sock, 2)

    def test_invalid_json_keeps_connection(self):
        sock = self.connect()
        sock.sendall(struct.pack("<I", 1) + b"{")
        self.assertEqual(self.receive(sock)["error"]["code"], -32700)
        self.ping(sock, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
