"""Bounded Redis Streams operations in a run-owned namespace; RESP2, optionally TLS."""

import json
import socket
import ssl

from proofbeak.adapters.base import ProviderError
from proofbeak.connectors import Connector
from proofbeak.models import canonical


def stream_operations():
    result = {}
    for action in ("publish", "read"):
        properties = {"channel": {"enum": ["events", "results"]}}
        if action == "publish":
            properties["payload"] = {"type": "object"}
        result["stream." + action] = {"input_schema": {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}, "output_schema": {"type": "object"},
            "read_only": action == "read", "binding": {"action": action}}
    return result


class RedisConnector(Connector):
    def __init__(self, name, settings, secrets=None):
        settings = {**settings, "operations": settings.get("operations", stream_operations())}
        super().__init__(name, settings, secrets)
        connection = settings["connection"]
        if set(settings) - {"kind", "connection", "operations", "provider_version"} or \
                set(connection) - {"host", "port", "tls", "secret_env"}:
            raise ValueError("unknown Redis connector setting")
        self.host, self.port = connection.get("host", "127.0.0.1"), connection.get("port", 6379)
        self.tls = connection.get("tls", False)
        if not isinstance(self.host, str) or type(self.port) is not int or not 1 <= self.port <= 65535 or \
                type(self.tls) is not bool or (not self.tls and self.host not in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("Redis requires TLS except on loopback")
        for definition in self.catalog.operations.values():
            action = definition["binding"].get("action")
            if action not in {"publish", "read"} or set(definition["binding"]) != {"action"} or \
                    definition["read_only"] != (action == "read"):
                raise ValueError("invalid stream operation binding")

    def command(self, *arguments):
        sock = None
        try:
            sock = socket.create_connection((self.host, self.port), timeout=3)
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            stream = sock.makefile("rb")
            def send(parts):
                encoded = [str(p).encode() for p in parts]
                sock.sendall(b"*%d\r\n" % len(encoded) + b"".join(b"$%d\r\n" % len(p) + p + b"\r\n" for p in encoded))
            budget = [0]
            def read(depth=0):
                if depth > 8:
                    raise ProviderError("invalid_stream_response")
                line = stream.readline(8193)
                budget[0] += len(line)
                if not line.endswith(b"\r\n") or budget[0] > 65536:
                    raise ProviderError("invalid_stream_response")
                tag, value = line[:1], line[1:-2]
                if tag == b"-":
                    raise ProviderError("stream_command_failed")
                if tag == b"+":
                    return value.decode()
                if tag == b":":
                    return int(value)
                if tag == b"$":
                    size = int(value)
                    if size == -1:
                        return None
                    if not 0 <= size <= 65536 - budget[0]:
                        raise ProviderError("observation_budget_exceeded")
                    body = stream.read(size + 2)
                    budget[0] += len(body)
                    if len(body) != size + 2 or not body.endswith(b"\r\n"):
                        raise ProviderError("invalid_stream_response")
                    return body[:-2].decode()
                if tag == b"*":
                    count = int(value)
                    if not 0 <= count <= 1024:
                        raise ProviderError("invalid_stream_response")
                    return [read(depth + 1) for _ in range(count)]
                raise ProviderError("invalid_stream_response")
            if "password" in self.secrets:
                send(["AUTH", *([self.secrets["username"]] if "username" in self.secrets else []), self.secrets["password"]])
                read()
            send(arguments)
            return read()
        except (OSError, ValueError, UnicodeError):
            raise ProviderError("write_outcome_unknown" if arguments[0] in {"SET", "XADD", "DEL", "EVAL"} else "observation_unavailable") from None
        finally:
            if sock:
                sock.close()

    def _prefix(self, resource):
        return "proofbeak:" + self.scope(resource)

    def _owned(self, resource):
        if self.command("GET", self._prefix(resource) + ":owner") != self.scope(resource):
            raise ProviderError("ownership_unverified")

    def provision(self, resource, trial):
        if self.command("SET", self._prefix(resource) + ":owner", self.scope(resource), "NX") != "OK":
            raise ProviderError("resource_conflict")

    def cleanup(self, resource):
        prefix = self._prefix(resource)
        keys = [prefix + ":" + suffix for suffix in ("events", "results", "owner")]
        if not self.command("EXISTS", *keys):
            return
        removed = self.command("EVAL", "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',unpack(KEYS)) else return -1 end",
                               len(keys), keys[-1], *keys[:-1], self.scope(resource))
        if removed == -1:
            raise ProviderError("ownership_unverified")
        if self.command("EXISTS", *keys):
            raise ProviderError("cleanup_unconfirmed")

    def invoke(self, resource, operation, arguments):
        definition = self.catalog.validate(operation, arguments)
        self._owned(resource)
        if arguments.get("channel") not in {"events", "results"}:
            raise ProviderError("scenario_arguments_invalid")
        key = self._prefix(resource) + ":" + arguments["channel"]
        if definition["binding"]["action"] == "publish":
            message = self.command("XADD", key, "MAXLEN", "256", "*", "payload", canonical(arguments["payload"]))
            return {"accepted": True, "message_id": message}
        rows = self.command("XRANGE", key, "-", "+", "COUNT", "65")
        if len(rows) > 64:
            raise ProviderError("observation_budget_exceeded")
        messages = []
        for identifier, pairs in rows:
            fields = dict(zip(pairs[::2], pairs[1::2]))
            messages.append({"id": identifier, "payload": json.loads(fields["payload"])})
        return {"messages": messages, "count": len(messages)}
