"""Validate chart syntax plus the deployment invariants that prevent duplicate writers."""

import json
import subprocess
from pathlib import Path

import yaml

CHART = "charts/proofbeak"
VALUES = CHART + "/ci-values.yaml"


def rendered(extra=None):
    text = subprocess.check_output(["helm", "template", "smoke", CHART, "-f", VALUES, *(extra or [])], text=True)
    return [document for document in yaml.safe_load_all(text) if document]


if __name__ == "__main__":
    subprocess.run(["helm", "lint", CHART, "-f", VALUES, "--strict"], check=True)
    documents = rendered()
    deployment = next(d for d in documents if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert pod["automountServiceAccountToken"] is False
    security = pod["containers"][0]["securityContext"]
    assert security["runAsNonRoot"] and security["readOnlyRootFilesystem"]
    assert security["allowPrivilegeEscalation"] is False
    assert "ALL" in security["capabilities"]["drop"]
    policy = next(d for d in documents if d["kind"] == "NetworkPolicy")
    assert policy["spec"]["egress"] == []
    assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert next(d for d in documents if d["kind"] == "Service")["spec"]["type"] == "ClusterIP"
    invalid = subprocess.run(["helm", "template", "smoke", CHART, "-f", VALUES,
                              "--set", "replicaCount=2"], capture_output=True)
    assert invalid.returncode != 0, "a shared SQLite deployment must reject multiple replicas"
    dns = next(d for d in rendered(["--set", "networkPolicy.allowDNS=true"]) if d["kind"] == "NetworkPolicy")
    assert dns["spec"]["egress"][0]["ports"][0]["port"] == 53
    result = {"chart_rendering": "passed", "single_writer": True,
              "rootless": True, "default_egress": "deny", "public_service": False}
    Path("reports").mkdir(exist_ok=True)
    Path("reports/chart-check.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
