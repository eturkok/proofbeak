"""Publish a main version increase, or an explicitly matching manual release."""
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path


def release_targets(event, ref, version, previous=None, requested="", pypi=False, image=False):
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("release needs a plain semantic version")
    automatic = event == "push" and ref == "refs/heads/main" and previous is not None and previous != version
    if automatic:
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", previous) or tuple(map(int, version.split("."))) <= tuple(map(int, previous.split("."))):
            raise ValueError("an automatic release must increase the version")
    manual = event == "workflow_dispatch" and (pypi or image)
    if manual and (ref != "refs/heads/main" or requested != version):
        raise ValueError("manual publication requires main and the exact package version")
    return {"version": version, "publish_pypi": automatic or (manual and pypi), "publish_image": automatic or (manual and image)}


def main():
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    event, ref = os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"]
    previous = None
    if event == "push" and ref == "refs/heads/main":
        before = os.environ.get("BEFORE_SHA", "")
        if not re.fullmatch(r"[a-f0-9]{40}", before) or before == "0" * 40:
            raise ValueError("cannot establish the previous main version")
        source = subprocess.check_output(["git", "show", before + ":pyproject.toml"], text=True)
        previous = tomllib.loads(source)["project"]["version"]
    targets = release_targets(event, ref, version, previous, os.environ.get("REQUESTED_VERSION", ""),
                              os.environ.get("PUBLISH_PYPI") == "true", os.environ.get("PUBLISH_IMAGE") == "true")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in targets.items():
            output.write(key + "=" + (str(value).lower() if isinstance(value, bool) else value) + "\n")
    print(json.dumps(targets))


if __name__ == "__main__":
    main()
