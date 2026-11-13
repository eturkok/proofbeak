"""Publish the already-qualified 0.8.0 bytes, with a tag pinned to their source commit."""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPO = 'eturkok/proofbeak'
TAG = 'v0.8.0'
COMMIT = 'bfcd1cfc149aed3de2efd0964ae9c6d5027bd8c6'
SOURCE_RUN = 43513858673
TRIALS_RUN = 43513858661
DEPLOYMENT_RUN = 43513858925
IMAGE_ID = 'sha256:5da9ecafe552fcff97d1b7103859027fea1ba485ee03b4baca05426f20bd0c92'
PACKAGES = {
    'proofbeak-0.8.0-py3-none-any.whl': '9e20e17aa4951c011e7e737e034fe544f71028c91262794916aea127570407dc',
    'proofbeak-0.8.0.tar.gz': '01ab39ec645257fa39d3af5d50675d377c0872488df12bc0a211ad90dfe4345d',
}


def api(path, method='GET', payload=None, missing=False):
    request = Request('https://api.github.com/repos/' + REPO + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if missing and error.code == 404:
            return None
        raise RuntimeError(f'GitHub returned {error.code} for {method} {path.split("?")[0]}') from None


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_tag():
    ref = api('/git/ref/tags/' + TAG, missing=True)
    if ref is None:
        return None
    target = ref['object']
    for _ in range(4):
        if target['type'] == 'commit':
            if target['sha'] != COMMIT:
                raise ValueError('existing tag points at a different source commit; refusing replacement')
            return COMMIT
        if target['type'] != 'tag':
            break
        target = api('/git/tags/' + target['sha'])['object']
    raise ValueError('cannot resolve existing release tag')


def prepare_assets():
    for identifier in (TRIALS_RUN, SOURCE_RUN, DEPLOYMENT_RUN):
        run = api('/actions/runs/' + str(identifier))
        if run['head_sha'] != COMMIT or run['status'] != 'completed' or run['conclusion'] != 'success':
            raise ValueError('source qualification is not successful for the pinned commit')
    manifest = json.loads(Path('tested-python/manifest.json').read_text())
    if manifest['name'] != 'proofbeak' or manifest['version'] != '0.8.0' or set(manifest['artifacts']) != set(PACKAGES):
        raise ValueError('unexpected Python artifact manifest')
    verification = json.loads(Path('tested-verification/pypi-verification.json').read_text())
    if verification.get('version') != '0.8.0' or verification.get('verified_file_hashes') != PACKAGES or \
            any(verification.get(field) != 'pass' for field in ('public_index_install', 'trial', 'public_connectors_install', 'capability_trial', 'suite_trial', 'ci_exports', 'baseline_comparison', 'signed_bundle', 'workspace_assets', 'studio_setup')):
        raise ValueError('public installation verification is incomplete')
    with urlopen('https://pypi.org/pypi/proofbeak/0.8.0/json', timeout=20) as response:
        registry = json.load(response)
    if {file['filename']: file['digests']['sha256'] for file in registry['urls']} != PACKAGES:
        raise ValueError('published Python files differ from the qualified build')
    browser = json.loads(Path('tested-browser/qualification.json').read_text())
    if browser.get('suite') != 'proofbeak.workbench.browser.v1' or browser.get('passed') is not True or \
            len(browser.get('cases', [])) != 9 or any(case.get('passed') is not True for case in browser['cases']):
        raise ValueError('browser qualification is incomplete')
    assets = Path('release-assets')
    assets.mkdir(exist_ok=True)
    for filename, expected in PACKAGES.items():
        source = Path('tested-python/dist') / filename
        if source.is_symlink() or digest(source) != expected or manifest['artifacts'][filename]['sha256'] != expected:
            raise ValueError('Python file hash mismatch')
        shutil.copyfile(source, assets / filename)
    image = Path('tested-image/proofbeak-0.8.0.tar.gz')
    checksum = Path('tested-image/SHA256SUMS').read_text().split()
    if len(checksum) != 2 or checksum[1] != image.name or digest(image) != checksum[0]:
        raise ValueError('tested image archive checksum mismatch')
    smoke = json.loads(Path('tested-image/container-smoke.json').read_text())
    if len(smoke) != 2 or any(row['version'] != '0.8.0' or row['verdict'] != 'pass' or
            row.get('capability_sqlite') != 'pass' for row in smoke):
        raise ValueError('image qualification is incomplete')
    subprocess.run(['docker', 'load', '--input', str(image)], check=True, stdout=subprocess.DEVNULL)
    loaded = json.loads(subprocess.check_output(['docker', 'image', 'inspect', 'proofbeak:distribution'], text=True))[0]
    if loaded['Id'] != IMAGE_ID or loaded['Architecture'] != 'amd64' or loaded['Os'] != 'linux':
        raise ValueError('archive is not the qualified Linux amd64 image')
    shutil.copyfile(image, assets / 'proofbeak-0.8.0-linux-amd64.tar.gz')
    shutil.copyfile('tested-verification/pypi-verification.json', assets / 'pypi-verification.json')
    shutil.copyfile('tested-browser/qualification.json', assets / 'workbench-qualification.json')
    inventory = {path.name: {'sha256': digest(path), 'bytes': path.stat().st_size} for path in sorted(assets.iterdir())}
    release_manifest = {'repository': REPO, 'version': '0.8.0', 'tag': TAG, 'commit': COMMIT,
                        'source_run': SOURCE_RUN, 'container_config_digest': IMAGE_ID, 'files': inventory,
                        'qualification_runs': {name: {'id': run, 'url': f'https://github.com/{REPO}/actions/runs/{run}'}
                            for name, run in [('trials', TRIALS_RUN), ('distribution', SOURCE_RUN), ('deployment', DEPLOYMENT_RUN)]}}
    (assets / 'release-manifest.json').write_text(json.dumps(release_manifest, indent=2) + '\n')
    (assets / 'SHA256SUMS').write_text(''.join(digest(path) + '  ' + path.name + '\n'
        for path in sorted(assets.iterdir()) if path.name != 'SHA256SUMS'))
    return assets


def asset_matches(asset, path):
    return asset.get('state') == 'uploaded' and asset.get('size') == path.stat().st_size and \
        asset.get('digest') == 'sha256:' + digest(path)


def ensure_asset(release, path):
    existing = [asset for asset in api(f'/releases/{release["id"]}/assets?per_page=100') if asset['name'] == path.name]
    if existing:
        if len(existing) != 1 or not asset_matches(existing[0], path):
            raise ValueError('an existing release asset has different bytes; refusing replacement')
        return existing[0]
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('asset exceeds this release upload budget')
    request = Request(f'https://uploads.github.com/repos/{REPO}/releases/{release["id"]}/assets?name=' + quote(path.name, safe=''),
        data=path.read_bytes(), method='POST', headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
            'Accept': 'application/vnd.github+json', 'Content-Type': 'application/octet-stream', 'X-GitHub-Api-Version': '2022-11-28'})
    with urlopen(request, timeout=180) as response:
        asset = json.load(response)
    for _ in range(15):
        if asset_matches(asset, path):
            return asset
        if asset.get('digest') and asset['digest'] != 'sha256:' + digest(path):
            raise ValueError('uploaded asset hash mismatch')
        time.sleep(2)
        asset = api('/releases/assets/' + str(asset['id']))
    raise RuntimeError('uploaded asset verification did not complete')


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != 'refs/heads/chore/github-release-v0.8.0':
        raise ValueError('release helper is restricted to its dedicated repository and branch')
    checked_tag()
    assets = prepare_assets()
    notes = Path('.github/release-v0.8.0.md').read_text()
    matches = [release for release in api('/releases?per_page=100') if release['tag_name'] == TAG]
    if len(matches) > 1:
        raise ValueError('ambiguous release tag')
    release = matches[0] if matches else api('/releases', 'POST', {'tag_name': TAG, 'target_commitish': COMMIT,
        'name': 'Proofbeak v0.8.0', 'body': notes, 'draft': True, 'prerelease': False})
    if release['target_commitish'] != COMMIT or release['prerelease']:
        raise ValueError('release metadata does not match the qualified source')
    uploaded = [ensure_asset(release, path) for path in sorted(assets.iterdir())]
    if release['draft']:
        if checked_tag() is None:
            api('/git/refs', 'POST', {'ref': 'refs/tags/' + TAG, 'sha': COMMIT})
        release = api('/releases/' + str(release['id']), 'PATCH', {'draft': False, 'prerelease': False,
            'make_latest': 'true', 'body': notes, 'name': 'Proofbeak v0.8.0'})
    checked_tag()
    published = api('/releases/' + str(release['id']))
    if published['draft'] or published['prerelease'] or published['tag_name'] != TAG:
        raise ValueError('release publication not confirmed')
    result = {'id': published['id'], 'url': published['html_url'], 'tag': TAG, 'commit': COMMIT,
        'assets': [{'name': a['name'], 'size': a['size'], 'digest': a['digest']} for a in uploaded]}
    Path('github-release-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PROOFBEAK_GITHUB_RELEASE=' + json.dumps(result))


if __name__ == '__main__':
    main()
