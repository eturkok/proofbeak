"""Real SDK MCP target backed by private files; invoked only by protocol tests."""

import json
import re
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

root = Path(sys.argv[1])
root.mkdir(mode=0o700, parents=True, exist_ok=True)
server = FastMCP('Proofbeak target fixture', host='127.0.0.1', port=int(sys.argv[2]) if len(sys.argv) > 2 else 8766)


def file(scope):
    if not re.fullmatch('[a-f0-9]{32}', scope):
        raise ValueError('invalid scope')
    return root / (scope + '.json')


@server.tool()
def scope_inspect(scope: str) -> dict:
    path = file(scope)
    return {'exists': path.exists(), **({'owner': json.loads(path.read_text())['owner']} if path.exists() else {})}


@server.tool()
def scope_create(scope: str) -> dict:
    with file(scope).open('x') as stream:
        json.dump({'owner': scope, 'records': {}}, stream)
    return {'accepted': True}


@server.tool()
def scope_destroy(scope: str) -> dict:
    path = file(scope)
    if path.exists():
        if json.loads(path.read_text())['owner'] != scope:
            raise ValueError('not owned')
        path.unlink()
    return {'accepted': True}


@server.tool()
def record_put(scope: str, key: str, value: str) -> dict:
    path = file(scope)
    data = json.loads(path.read_text())
    data['records'][key] = value
    path.write_text(json.dumps(data))
    return {'accepted': True}


@server.tool()
def record_get(scope: str, key: str) -> dict:
    data = json.loads(file(scope).read_text())
    exists = key in data['records']
    return {'exists': exists, **({'value': data['records'][key]} if exists else {})}


if __name__ == '__main__':
    server.run(transport='streamable-http' if len(sys.argv) > 2 else 'stdio')
