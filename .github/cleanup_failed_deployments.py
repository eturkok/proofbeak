"""One-off repository-scoped cleanup. Audit first; apply only immutable planned IDs."""

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPOSITORY = 'eturkok/proofbeak'
BRANCH = 'refs/heads/chore/failed-deployment-cleanup'
FAILED_RUNS = {43387893520, 43313214771}


def api(path, method='GET', payload=None, missing=False):
    request = Request('https://api.github.com/repos/' + REPOSITORY + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as error:
        if missing and error.code == 404:
            return None
        raise RuntimeError(f'GitHub returned {error.code} for {method} {path.split("?")[0]}') from None


def pages(path):
    result = []
    for page in range(1, 21):
        rows = api(path + f'?per_page=100&page={page}')
        if not isinstance(rows, list):
            raise ValueError('expected a paginated GitHub array')
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ValueError('pagination bound reached; no deletion performed')


def failed_only(states):
    return bool(states) and 'success' not in states and any(s in {'failure', 'error'} for s in states) and \
        states[0] in {'failure', 'error', 'inactive'}


def save_report(result):
    path = Path('reports/failed-deployment-cleanup.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n')


def confirm_absent(path, timeout=45):
    deadline = time.monotonic() + timeout
    while True:
        if api(path, missing=True) is None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(2)


def audit():
    plan = {'repository': REPOSITORY, 'deployments': [], 'workflow_runs': [], 'preserved_deployments': []}
    for deployment in pages('/deployments'):
        identifier = deployment['id']
        if type(identifier) is not int:
            raise ValueError('invalid deployment ID')
        states = [s['state'] for s in pages(f'/deployments/{identifier}/statuses')]
        row = {'id': identifier, 'environment': deployment['environment'], 'sha': deployment['sha'], 'states': states}
        plan['deployments' if failed_only(states) else 'preserved_deployments'].append(row)
    for identifier in sorted(FAILED_RUNS):
        run = api(f'/actions/runs/{identifier}', missing=True)
        if run and run['name'] == 'Deployment checks' and run['workflow_id'] == 416613562 and \
                run['status'] == 'completed' and run['conclusion'] == 'failure':
            plan['workflow_runs'].append({'id': identifier, 'name': run['name'], 'sha': run['head_sha']})
    return plan


def apply(plan):
    if plan.get('repository') != REPOSITORY or set(plan) != {'repository', 'deployments', 'workflow_runs', 'preserved_deployments'}:
        raise ValueError('unexpected cleanup plan')
    deleted, skipped = {'deployments': [], 'workflow_runs': []}, []
    pending = {'deployments': [], 'workflow_runs': []}
    result = {'deleted': deleted, 'skipped': skipped, 'pending_verification': pending}
    save_report(result)
    for row in plan['deployments']:
        identifier = row['id']
        if type(identifier) is not int or not failed_only(row['states']):
            raise ValueError('plan includes a non-failed deployment')
        deployment = api(f'/deployments/{identifier}', missing=True)
        if deployment is None:
            skipped.append({'deployment': identifier, 'reason': 'already_absent'})
            continue
        states = [s['state'] for s in pages(f'/deployments/{identifier}/statuses')]
        if not failed_only(states) or deployment['sha'] != row['sha']:
            skipped.append({'deployment': identifier, 'reason': 'state_changed'})
            continue
        if states[0] != 'inactive':
            api(f'/deployments/{identifier}/statuses', 'POST', {'state': 'inactive', 'auto_inactive': False,
                                                            'description': 'Remove failed deployment record requested by repository owner'})
        pending['deployments'].append(identifier)
        save_report(result)
        api(f'/deployments/{identifier}', 'DELETE')
        if not confirm_absent(f'/deployments/{identifier}'):
            raise RuntimeError('deployment deletion not confirmed')
        pending['deployments'].remove(identifier)
        deleted['deployments'].append(identifier)
        save_report(result)
    for row in plan['workflow_runs']:
        identifier = row['id']
        if type(identifier) is not int or identifier not in FAILED_RUNS:
            raise ValueError('run is outside the fixed cleanup allowlist')
        run = api(f'/actions/runs/{identifier}', missing=True)
        if run is None:
            skipped.append({'workflow_run': identifier, 'reason': 'already_absent'})
            continue
        if run['name'] != 'Deployment checks' or run['workflow_id'] != 416613562 or run['head_sha'] != row['sha'] or \
                run['status'] != 'completed' or run['conclusion'] != 'failure':
            skipped.append({'workflow_run': identifier, 'reason': 'state_changed'})
            continue
        pending['workflow_runs'].append(identifier)
        save_report(result)
        api(f'/actions/runs/{identifier}', 'DELETE')
        if not confirm_absent(f'/actions/runs/{identifier}'):
            raise RuntimeError('workflow-run deletion not confirmed')
        pending['workflow_runs'].remove(identifier)
        deleted['workflow_runs'].append(identifier)
        save_report(result)
    preserved = []
    for row in plan['preserved_deployments']:
        deployment = api(f'/deployments/{row["id"]}', missing=True)
        if deployment is None:
            raise RuntimeError('a preserved deployment is unexpectedly absent')
        preserved.append(row['id'])
    result['preserved_deployments'] = preserved
    save_report(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--plan', type=Path, default=Path('.github/failed-deployment-cleanup-plan.json'))
    args = parser.parse_args()
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF') != BRANCH:
        raise ValueError('cleanup is restricted to its dedicated repository and branch')
    result = apply(json.loads(args.plan.read_text())) if args.apply else audit()
    save_report(result)
    print(('PROOFBEAK_CLEANUP_RESULT=' if args.apply else 'PROOFBEAK_CLEANUP_PLAN=') + json.dumps(result))


if __name__ == '__main__':
    main()
