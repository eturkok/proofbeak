"""Disposable authenticated WsgiDAV fixture; credentials arrive on stdin or an operator config file."""

import json
import os
import sys

from cheroot.wsgi import Server
from wsgidav.wsgidav_app import WsgiDAVApp


class RequestJournal:
    """Fixture-only method/path/status log, without headers, bodies or authentication."""

    def __init__(self, app, path):
        self.app, self.path = app, path

    def __call__(self, environ, start_response):
        def recorded(status, headers, exc_info=None):
            row = {"method": environ["REQUEST_METHOD"], "path": environ["PATH_INFO"],
                   "status": int(status.split()[0])}
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(descriptor, (json.dumps(row) + "\n").encode())
            finally:
                os.close(descriptor)
            return start_response(status, headers, exc_info)
        return self.app(environ, recorded)


def main():
    if len(sys.argv) == 2:
        with open(sys.argv[1]) as stream:
            settings = json.load(stream)
    else:
        settings = json.load(sys.stdin)
    config = {"host": settings["host"], "port": settings["port"],
              "provider_mapping": {"/": settings["root"]}, "verbose": 0,
              "logging": {"enable": False},
              "http_authenticator": {"accept_basic": True, "accept_digest": False,
                                     "default_to_digest": False},
              "simple_dc": {"user_mapping": {"*": {"proofbeak": {"password": settings["password"]}}}}}
    app = WsgiDAVApp(config)
    if settings.get("request_journal"):
        app = RequestJournal(app, settings["request_journal"])
    server = Server((settings["host"], settings["port"]), app)
    try:
        server.start()
    finally:
        server.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not expose configuration/credentials through library exception messages.
        print("WebDAV fixture startup failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
