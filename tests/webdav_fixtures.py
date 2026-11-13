"""Shared real-server fixtures. Journals contain only HTTP method, path and status."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from proofbeak.adapters.base import ProviderError
from proofbeak.adapters.webdav import WebDAVAdapter

AVAILABLE = shutil.which("docker") is not None
PASSWORD = "proofbeak-local-only"  # Public disposable fixture credential, never a real account.
IMAGE = os.environ.get("PROOFBEAK_WEBDAV_APACHE_IMAGE", "proofbeak-webdav-apache:test")
WSGI_IMAGE = os.environ.get("PROOFBEAK_WEBDAV_WSGI_IMAGE", "proofbeak-webdav-wsgi:test")
MUTATING = {"MKCOL", "PUT", "DELETE", "MOVE", "COPY", "POST", "LOCK", "UNLOCK"}


def wait_for(endpoint):
    adapter = WebDAVAdapter("webdav:ready", endpoint, "proofbeak", PASSWORD)
    deadline = time.monotonic() + 20
    while True:
        try:
            adapter.transport.calls = 0
            adapter.transport.request("OPTIONS", endpoint + "/", headers=adapter.headers, allowed=(200, 204))
            return
        except ProviderError:
            if time.monotonic() > deadline:
                raise RuntimeError("actual WebDAV fixture did not become ready") from None
            time.sleep(.1)


class WebDAVFixtures:
    fixture_report_environment = "PROOFBEAK_WEBDAV_REPORT_DIR"

    @classmethod
    def setUpClass(cls):
        checked = subprocess.run(["docker", "image", "inspect", IMAGE, WSGI_IMAGE],
                                 capture_output=True, text=True)
        if checked.returncode:
            raise unittest.SkipTest("build both WebDAV fixture images before integration")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        endpoints, fixtures, cls.containers = {}, {}, {}
        for name, image, data_path in (("wsgidav", WSGI_IMAGE, "/tmp/objects"),
                                      ("apache", IMAGE, "/usr/local/apache2/htdocs")):
            container = subprocess.check_output(["docker", "run", "-d", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:uid=10001,gid=10001,mode=0700,size=64m",
                "--tmpfs", data_path + ":uid=10001,gid=10001,mode=0700,size=32m",
                "-p", "127.0.0.1::8080", image], text=True).strip()
            cls.containers[name] = container
            cls.addClassCleanup(lambda cid=container: subprocess.run(
                ["docker", "rm", "-f", cid], check=True, stdout=subprocess.DEVNULL))
            metadata = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
            assert metadata["Config"]["User"] == "10001:10001"
            assert metadata["HostConfig"]["ReadonlyRootfs"]
            port = metadata["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
            endpoints[name] = f"http://127.0.0.1:{port}"
            wait_for(endpoints[name])
            fixtures[name] = {"image_id": metadata["Image"], "build_tag": image,
                              "endpoint": endpoints[name], "user": metadata["Config"]["User"],
                              "read_only_rootfs": metadata["HostConfig"]["ReadonlyRootfs"]}
        cls.wsgi_url, cls.apache_url = endpoints["wsgidav"], endpoints["apache"]
        if os.environ.get(cls.fixture_report_environment):
            output = Path(os.environ[cls.fixture_report_environment])
            output.mkdir(parents=True, exist_ok=True)
            (output / "fixture-environment.json").write_text(json.dumps(fixtures, indent=2) + "\n")

    def adapters(self):
        return [WebDAVAdapter("webdav:wsgidav", self.wsgi_url, "proofbeak", PASSWORD),
                WebDAVAdapter("webdav:apache", self.apache_url, "proofbeak", PASSWORD)]

    def mutations(self):
        result = {}
        for name, container in self.containers.items():
            path = "/tmp/requests.jsonl" if name == "wsgidav" else "/tmp/requests.log"
            lines = subprocess.check_output(["docker", "exec", container, "cat", path], text=True).splitlines()
            if name == "wsgidav":
                rows = [json.loads(line) for line in lines]
            else:
                rows = []
                for line in lines:
                    method, path, status = line.split()
                    rows.append({"method": method, "path": path, "status": int(status)})
            result[name] = [row for row in rows if row["method"] in MUTATING]
        return result
