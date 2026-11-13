"""Trusted worker codecs. Secret fields travel only through a private anonymous pipe."""

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.google import GoogleCalendarAdapter
from proofbeak.adapters.http import Transport
from proofbeak.adapters.radicale import RadicaleAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.adapters.webdav import WebDAVAdapter
from proofbeak.adapters.scenario import JSONReadConnector, ScenarioAdapter


def encode_adapter(adapter):
    if type(adapter) is ScenarioAdapter:
        return {"type": "scenario", "options": {"backend": encode_adapter(adapter.backend)}}
    if type(adapter) is JSONReadConnector and type(adapter.transport) is Transport \
            and adapter.evidence_level != "simulation":
        return {"type": "http-json", "options": {"name": adapter.name, "endpoint": adapter.endpoint,
                                                   "routes": adapter.routes}}
    if type(adapter) is FilesystemAdapter:
        return {"type": "filesystem", "options": {"root": str(adapter.root)}}
    if type(adapter) is SimulationAdapter:
        return {"type": "simulation", "options": {"mode": adapter.mode}}
    if type(adapter) is WebDAVAdapter and type(adapter.transport) is Transport \
            and adapter.evidence_level != "simulation":
        return {"type": "webdav", "options": {"name": adapter.name, "endpoint": adapter.endpoint,
                "username": adapter.username, "password": adapter._password}}
    if type(adapter) is RadicaleAdapter and type(adapter.transport) is Transport \
            and adapter.evidence_level != "simulation":
        return {"type": "radicale", "options": {"endpoint": adapter.endpoint,
                "username": adapter.username, "password": adapter._password}}
    if type(adapter) is GoogleCalendarAdapter and type(adapter.transport) is Transport \
            and adapter.evidence_level != "simulation":
        return {"type": "google-calendar", "options": {"access_token": adapter._access_token,
                                                        "test_account": adapter.account}}
    raise ValueError("adapter has no registered worker codec")


def decode_adapter(value):
    if not isinstance(value, dict) or set(value) != {"type", "options"}:
        raise ValueError("invalid worker adapter")
    if value["type"] == "scenario":
        if not isinstance(value["options"], dict) or set(value["options"]) != {"backend"}:
            raise ValueError("invalid scenario worker connector")
        return ScenarioAdapter(decode_adapter(value["options"]["backend"]))
    constructors = {"filesystem": FilesystemAdapter, "simulation": SimulationAdapter,
                    "radicale": RadicaleAdapter, "google-calendar": GoogleCalendarAdapter,
                    "webdav": WebDAVAdapter, "http-json": JSONReadConnector}
    constructor = constructors.get(value["type"])
    if constructor is None or not isinstance(value["options"], dict):
        raise ValueError("unregistered worker adapter")
    return constructor(**value["options"])
