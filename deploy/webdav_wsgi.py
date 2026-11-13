"""Disposable authenticated WsgiDAV fixture; credentials arrive on stdin or an operator config file."""

import json
import sys

from cheroot.wsgi import Server
from wsgidav.wsgidav_app import WsgiDAVApp


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
