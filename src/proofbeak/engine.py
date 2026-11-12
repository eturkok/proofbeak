"""One synchronous worker, shared by the CLI and future agent interfaces."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from proofbeak.adapters.base import Adapter, ProviderError, Resource
from proofbeak.models import Trial, fingerprint
from proofbeak.observer import OBSERVER_VERSION, assess
from proofbeak.store import Store


def run_trial(spec: dict, adapters: list[Adapter], store: Store, key: str) -> dict:
    trial = Trial.parse(spec)
    # Validate the entire fixture before reserving or making any external call.
    trial.fixture("validation")
    if not 1 <= len(adapters) <= 4 or len({a.name for a in adapters}) != len(adapters):
        raise ValueError("provide one to four distinct candidates")
    context = [{"name": a.name, "version": a.version, "profile": a.profile,
                "evidence_level": a.evidence_level} for a in adapters]
    request_hash = fingerprint({"test": spec, "candidates": context,
                                "observer_version": OBSERVER_VERSION})
    run_id, existing = store.reserve(key, request_hash)
    if existing is not None:
        return existing
    results = []
    for adapter in adapters:
        resource = adapter.resource(run_id)
        store.track(run_id, adapter.name, fingerprint(adapter.profile), asdict(resource))
        before = after = None
        outcome = {"verdict": "inconclusive", "reason_code": "execution_interrupted", "checks": []}
        cleanup_status = "pending"
        provisioned = False
        try:
            adapter.provision(resource, trial)
            provisioned = True
            adapter.seed(resource, trial)
            before = adapter.read(resource, trial)
            if assess(trial, before, before)["reason_code"] == "invalid_baseline":
                raise ProviderError("invalid_baseline")
            adapter.move(resource, trial)
            after = adapter.read(resource, trial)
            outcome = assess(trial, before, after)
        except ProviderError as exc:
            outcome["reason_code"] = exc.reason
            if exc.reason == "write_outcome_unknown" and before is not None:
                # Reconcile once without retrying the mutation. Ambiguous writes stay inconclusive.
                try:
                    after = adapter.read(resource, trial)
                except ProviderError:
                    pass
        finally:
            if not provisioned and outcome["reason_code"] == "resource_conflict":
                cleanup_status = "ownership_unverified"
            else:
                try:
                    adapter.cleanup(resource)
                    cleanup_status = "cleaned"
                except ProviderError:
                    cleanup_status = "quarantined"
            store.cleaned(run_id, adapter.name, cleanup_status)
        now = datetime.now(UTC)
        evidence = {
            "before": before.to_dict() if before else None,
            "after": after.to_dict() if after else None,
        }
        result = {
            "provider": adapter.name, "adapter_version": adapter.version,
            "observer_version": OBSERVER_VERSION, "profile": adapter.profile,
            "evidence_level": adapter.evidence_level,
            "eligible_for_provider_comparison": adapter.evidence_level != "simulation",
            "test_hash": fingerprint(spec), "fixture_hash": fingerprint(spec["fixture"]),
            "observed_at": now.isoformat(), "expires_at": (now + timedelta(hours=24)).isoformat(),
            **outcome, "evidence": evidence, "evidence_hash": fingerprint(evidence),
            "cleanup_status": cleanup_status,
        }
        results.append(result)
    report = {"schema_version": "0.1", "run_id": run_id, "state": "completed",
              "test": spec, "request_hash": request_hash, "candidates": results}
    store.finish(run_id, report)
    return report


def recover_cleanup(store: Store, adapters: list[Adapter]) -> list[dict]:
    """Explicit cleanup only; interrupted writes are never replayed."""
    available = {a.name: a for a in adapters}
    outcomes = []
    for item in store.pending():
        if item["cleanup_status"] == "ownership_unverified":
            outcomes.append({"run_id": item["run_id"], "adapter": item["adapter"],
                             "status": "ownership_unverified"})
            continue
        adapter = available.get(item["adapter"])
        status = "profile_unavailable"
        if adapter and fingerprint(adapter.profile) == item["profile_hash"]:
            resource = Resource(**item["resource"])
            if resource != adapter.resource(item["run_id"]):
                status = "resource_scope_violation"
            else:
                try:
                    adapter.cleanup(resource)
                    status = "cleaned"
                except ProviderError:
                    status = "quarantined"
                store.cleaned(item["run_id"], adapter.name, status)
        outcomes.append({"run_id": item["run_id"], "adapter": item["adapter"], "status": status})
    return outcomes
