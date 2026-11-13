"""Link the main commit to the actual distribution/publication run."""

import json
import os
import sys
from urllib.request import Request, urlopen


def main():
    state, description = "pending", "Building and checking distribution artifacts"
    if sys.argv[1] == "complete":
        jobs = json.loads(os.environ["NEEDS_JSON"])
        mandatory = all(jobs[name]["result"] == "success" for name in ("build", "installed", "image"))
        published = all(jobs[name]["result"] in {"success", "skipped"} for name in ("publish-pypi", "verify-pypi", "publish-image"))
        state = "success" if mandatory and published else "failure"
        description = "Distribution checks and selected publications completed" if state == "success" else "Distribution checks or publication failed"
    target = os.environ["GITHUB_SERVER_URL"] + "/" + os.environ["GITHUB_REPOSITORY"] + "/actions/runs/" + os.environ["GITHUB_RUN_ID"]
    payload = {"state": state, "context": "proofbeak/distribution", "description": description, "target_url": target}
    origin = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = Request(origin + "/repos/" + os.environ["GITHUB_REPOSITORY"] + "/statuses/" + os.environ["GITHUB_SHA"],
        data=json.dumps(payload).encode(), method="POST", headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        assert response.status == 201
    print(json.dumps({"state": state, "target_url": target}))


if __name__ == "__main__":
    main()
