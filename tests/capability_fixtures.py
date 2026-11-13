"""Disposable protocol fixtures; model responses are deterministic test data, not a live LLM."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def operation(properties, binding, read_only, required=None):
    return {"input_schema": {"type": "object", "properties": properties,
        "required": list(properties) if required is None else required, "additionalProperties": False},
        "output_schema": {"type": "object"}, "read_only": read_only, "binding": binding}


def rest_profile(origin):
    text = {"type": "string", "minLength": 1, "maxLength": 4096}
    return {"kind": "rest", "connection": {"endpoint": origin}, "operations": {
        "scope.create": operation({}, {"method": "PUT", "path": "/scopes/{scope}", "body": {"owner": {"scope": True}}}, False),
        "scope.inspect": operation({}, {"method": "GET", "path": "/scopes/{scope}", "statuses": [200, 404],
            "project": {"exists": ["exists"], "owner": ["json", "owner"]}}, True),
        "scope.destroy": operation({}, {"method": "DELETE", "path": "/scopes/{scope}", "statuses": [204, 404]}, False),
        "record.put": operation({"key": text, "value": text}, {"method": "PUT", "path": "/scopes/{scope}/items/{key}",
            "body": {"value": {"arg": "value"}}}, False),
        "record.get": operation({"key": text}, {"method": "GET", "path": "/scopes/{scope}/items/{key}", "statuses": [200, 404],
            "project": {"exists": ["exists"], "value": ["json", "value"]}}, True)},
        "lifecycle": {"create": "scope.create", "inspect": "scope.inspect", "destroy": "scope.destroy"}}


def mcp_profile(command, script, root, transport='stdio', url=None):
    text = {"type": "string", "minLength": 1, "maxLength": 4096}
    def binding(tool, arguments):
        return {"tool": tool, "arguments": {"scope": {"scope": True}, **{a: {"arg": a} for a in arguments}}}
    connection = {"transport": "stdio", "command": command, "args": [str(script), str(root)]} if transport == 'stdio' else {
        "transport": "http", "url": url}
    return {"kind": "mcp", "connection": connection, "operations": {
        **{"scope." + phase: operation({}, binding('scope_' + phase, []), phase == 'inspect') for phase in ('create', 'inspect', 'destroy')},
        "record.put": operation({"key": text, "value": text}, binding('record_put', ['key', 'value']), False),
        "record.get": operation({"key": text}, binding('record_get', ['key']), True)},
        "lifecycle": {"create": "scope.create", "inspect": "scope.inspect", "destroy": "scope.destroy"}}


class CapabilityHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self):
        super().__init__(('127.0.0.1', 0), CapabilityHandler)
        self.scopes, self.journal, self.delay, self.pending = {}, [], 0, []


class CapabilityHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, code, value=None):
        body = json.dumps(value).encode() if value is not None else b''
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        parts = url.path.strip('/').split('/')
        if url.path == '/search':
            query = parse_qs(url.query).get('q', [''])[0].lower().split()
            docs = [{'id': 'vector', 'text': 'vector similarity search'}, {'id': 'sql', 'text': 'relational SQL transaction'},
                    {'id': 'stream', 'text': 'stream event delivery'}]
            matches = sorted((d for d in docs if any(word in d['text'].lower() for word in query)),
                             key=lambda d: -sum(word in d['text'].lower() for word in query))
            return self.respond(200, {'matches': matches})
        if len(parts) in {2, 4} and parts[0] == 'scopes':
            scope = self.server.scopes.get(parts[1])
            if scope is None:
                return self.respond(404, {})
            if len(parts) == 2:
                return self.respond(200, {'owner': scope['owner']})
            if parts[3] not in scope['records']:
                return self.respond(404, {})
            return self.respond(200, {'value': scope['records'][parts[3]]})
        self.respond(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/v1/chat/completions':
            prompt = body['messages'][-1]['content']
            return self.respond(200, {'choices': [{'message': {'content': 'fixture echo: ' + prompt}}],
                                      'usage': {'prompt_tokens': len(prompt.split()), 'completion_tokens': len(prompt.split()) + 2}})
        self.respond(404, {})

    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        parts = self.path.strip('/').split('/')
        self.server.journal.append(('PUT', self.path))
        if len(parts) == 2 and parts[0] == 'scopes':
            if parts[1] in self.server.scopes:
                return self.respond(409, {})
            self.server.scopes[parts[1]] = {'owner': body['owner'], 'records': {}}
            return self.respond(201, {'accepted': True})
        if len(parts) == 4 and parts[1] in self.server.scopes:
            def update():
                scope = self.server.scopes.get(parts[1])
                if scope:
                    scope['records'][parts[3]] = body['value']
            if self.server.delay:
                timer = threading.Timer(self.server.delay, update)
                self.server.pending.append(timer)
                timer.start()
            else:
                update()
            return self.respond(200, {'accepted': True})
        self.respond(404, {})

    def do_DELETE(self):
        parts = self.path.strip('/').split('/')
        self.server.journal.append(('DELETE', self.path))
        if len(parts) == 2 and parts[0] == 'scopes':
            self.server.scopes.pop(parts[1], None)
            return self.respond(204)
        self.respond(404, {})
