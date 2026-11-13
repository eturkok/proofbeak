"""Exercise the rendered chart's policy with actual traffic on a policy-enforcing CNI."""

import json
import subprocess
import time
from pathlib import Path

NAMESPACE = "proofbeak-smoke"
CHART = "charts/proofbeak"
LABELS = "app.kubernetes.io/instance=proofbeak,app.kubernetes.io/component=server"
PROBE = '''
import json, sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
try:
    with build_opener(ProxyHandler({})).open(sys.argv[1], timeout=2) as response:
        result = {"reachable":True,"status":response.status}
except HTTPError as error:
    result = {"reachable":True,"status":error.code}
    error.close()
except (URLError, TimeoutError, OSError) as error:
    result = {"reachable":False,"network_error":type(error).__name__}
print(json.dumps(result))
'''


def command(*args, timeout=60):
    return subprocess.check_output(list(args), text=True, timeout=timeout)


def kubectl(*args):
    return command("kubectl", "-n", NAMESPACE, *args)


def pod(name, *, labels=None, server=False):
    command_args = ["python", "-m", "http.server", "8766", "--bind", "0.0.0.0", "--directory", "/tmp"] \
        if server else ["python", "-c", "import time; time.sleep(600)"]
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": NAMESPACE,
            "labels": labels or {}}, "spec": {"automountServiceAccountToken": False,
            "restartPolicy": "Never", "securityContext": {"runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": "probe", "image": "proofbeak:test", "imagePullPolicy": "Never",
                "command": command_args, "securityContext": {"allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "25m", "memory": "32Mi"},
                              "limits": {"cpu": "250m", "memory": "128Mi"}}}]}}


def reachable(source, url):
    return json.loads(kubectl("exec", source, "--", "python", "-c", PROBE, url))


def expect(source, url, allowed):
    deadline = time.monotonic() + 30
    while True:
        result = reachable(source, url)
        if result["reachable"] is allowed:
            if allowed:
                assert result["status"] == 200, result
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(f"policy check expected reachable={allowed}: {result}")
        time.sleep(.2)


def change_egress(rules):
    return command("helm", "upgrade", "proofbeak", CHART, "--namespace", NAMESPACE,
                   "--reuse-values", "--set-json", "networkPolicy.egressRules=" + json.dumps(rules),
                   "--wait", "--timeout", "90s", timeout=120)


if __name__ == "__main__":
    names = ["policy-allowed", "policy-denied", "policy-sink"]
    fixtures = [pod(names[0], labels={"proofbeak-client": "true"}), pod(names[1]),
                pod(names[2], labels={"proofbeak-ci-sink": "true"}, server=True)]
    try:
        subprocess.run(["kubectl", "create", "-f", "-"], input=json.dumps(
            {"apiVersion": "v1", "kind": "List", "items": fixtures}), text=True, check=True, timeout=30)
        subprocess.run(["kubectl", "-n", NAMESPACE, "wait", "--for=condition=Ready",
                        *("pod/" + name for name in names), "--timeout=90s"], check=True, timeout=100)
        servers = json.loads(kubectl("get", "pods", "-l", LABELS, "-o", "json"))["items"]
        assert len(servers) == 1
        server_name = servers[0]["metadata"]["name"]
        api_url = "http://" + servers[0]["status"]["podIP"] + ":8765/health"
        sink = json.loads(kubectl("get", "pod", names[2], "-o", "json"))
        sink_url = "http://" + sink["status"]["podIP"] + ":8766"
        # Positive controls establish a live listener and working pod routing before any deny claim.
        control = expect(names[0], sink_url, True)
        ingress_allowed = expect(names[0], api_url, True)
        ingress_denied = expect(names[1], api_url, False)
        egress_denied = expect(server_name, sink_url, False)
        try:
            change_egress([{"to": [{"podSelector": {"matchLabels": {"proofbeak-ci-sink": "true"}}}],
                           "ports": [{"protocol": "TCP", "port": 8766}]}])
            egress_allowed = expect(server_name, sink_url, True)
        finally:
            change_egress([])
        egress_restored = expect(server_name, sink_url, False)
        expect(names[0], sink_url, True)
        report = {"cni": "calico-v3.33.0", "scope": "IPv4 pod TCP traffic on ephemeral kind",
                  "positive_control": control, "ingress_allowed": ingress_allowed,
                  "ingress_denied": ingress_denied, "default_egress_denied": egress_denied,
                  "explicit_egress_allowed": egress_allowed, "default_deny_restored": egress_restored,
                  "network_policy_enforcement": "passed",
                  "limits": ["Not an OpenShift installation or a different production CNI.",
                             "No IPv6, DNS-policy, public-provider connectivity or per-tenant isolation claim."]}
        Path("reports").mkdir(exist_ok=True)
        Path("reports/network-policy-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        subprocess.run(["kubectl", "-n", NAMESPACE, "delete", "pod", *names,
                        "--ignore-not-found", "--wait=false"], check=False, timeout=30)
