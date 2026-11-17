"""Every image Cloud Run runs carries a `keep-<consumer>` tag in Artifact Registry, so a repository's cleanup policy
never deletes a deployed one.

The host's cleanup policy keeps a version tagged `keep-` and deletes everything else past its age. Which versions are
deployed is Cloud Run's to say, never a list kept by hand: `consumers` reads every service of the region (each
revision serving traffic or holding a traffic tag) and every job (its template), and `keep` gives each image digest
that lives in the project's own Artifact Registry the tag `keep-<service or job>`. The current `keep-` tags are read
first (Artifact Registry's own name filter, one call a package), so only a tag that moved is written, and the tag of
a consumer that no longer exists is deleted.

A host runs `keep(project, region)` after each deploy, as an account that holds Artifact Registry and Cloud Run
admin. Reads and writes go through `google_rest`, whose guard refuses a real write where none is meant. The standard
library alone.
"""
import re
import urllib.parse

RUN_API = 'https://run.googleapis.com/v2'
AR_API = 'https://artifactregistry.googleapis.com/v1'
PREFIX = 'keep-'
# An image a project's Artifact Registry holds, by digest: <region>-docker.pkg.dev/<project>/<repo>/<package>@sha256:..
IMAGE = re.compile(r'^(?P<loc>[a-z0-9-]+)-docker\.pkg\.dev/(?P<project>[^/]+)/(?P<repo>[^/]+)/(?P<package>[^@:]+)@(?P<digest>sha256:[0-9a-f]{64})$')


def _rest():
    from . import google_rest
    return google_rest


def tag_name(consumer: str) -> str:
    """The keep tag of a service or job: `keep-<name>`, in Artifact Registry's tag characters."""
    return PREFIX + re.sub(r'[^A-Za-z0-9_.-]', '-', consumer)[:120]


RETRY_SECONDS = (1, 3)   # the waits before a revision read refused with a 5xx is asked again


def _get_revision(rest, url, token, kw, sleep=None, retry_seconds=None):
    """One revision read, asked again after each of `retry_seconds` (RETRY_SECONDS) when Cloud Run answers 5xx. Why:
    one deploy reads every serving revision, and one 503 among them would leave every tag of that deploy unchecked
    until the next one."""
    import time
    sleep = sleep or time.sleep
    for wait in (*(RETRY_SECONDS if retry_seconds is None else retry_seconds), None):
        try:
            return rest.call('GET', url, token, **kw)
        except Exception as e:   # noqa: BLE001 - only a 5xx is asked again
            if wait is None or not 500 <= (getattr(e, 'status', 0) or 0) < 600:
                raise
            sleep(wait)


def consumers(project: str, region: str, token=None, opener=None, rest=None, sleep=None, retry_seconds=None) -> dict:
    """{consumer: image} for every image Cloud Run runs in the region: a service's revisions with traffic or a traffic
    tag (`<service>` for the one serving, `<service>-<tag>` for a tagged one, `<service>-<revision>` for any other
    serving revision), and each job's template (`<job>`). A revision read Cloud Run refuses with a 5xx is asked again
    (`retry_seconds`, RETRY_SECONDS by default). `rest` is the test seam for google_rest, `sleep` for the retry's
    wait."""
    rest = rest or _rest()
    kw = {'opener': opener} if opener else {}
    base = f'{RUN_API}/projects/{project}/locations/{region}'
    out = {}
    for svc in rest.list_all(f'{base}/services', 'services', token, **kw):
        name = svc['name'].rsplit('/', 1)[-1]
        for t in svc.get('trafficStatuses') or []:
            rev = t.get('revision')
            if not rev or not (t.get('percent') or t.get('tag')):
                continue
            got = _get_revision(rest, f'{base}/services/{name}/revisions/{rev}', token, kw, sleep, retry_seconds)
            image = ((got.get('containers') or [{}])[0]).get('image') or ''
            if t.get('tag'):
                out[f"{name}-{t['tag']}"] = image
            if t.get('percent'):
                out[name if name not in out else f'{name}-{rev}'] = image
    for job in rest.list_all(f'{base}/jobs', 'jobs', token, **kw):
        containers = ((job.get('template') or {}).get('template') or {}).get('containers') or [{}]
        out[job['name'].rsplit('/', 1)[-1]] = containers[0].get('image') or ''
    return out


def _package_url(m) -> str:
    return (f"{AR_API}/projects/{m['project']}/locations/{m['loc']}/repositories/{m['repo']}/packages/"
            f"{urllib.parse.quote(m['package'], safe='')}")


def keep(project: str, region: str, token=None, opener=None, rest=None, found=None, retry_seconds=None) -> list:
    """Each deployed image's keep tag made right: the lines said (each a change or a skip, a refusal a WARNING; never
    raises). `found` is {consumer: image} when the caller read it already (consumers otherwise, with
    `retry_seconds`)."""
    rest = rest or _rest()
    kw = {'opener': opener} if opener else {}
    try:
        found = consumers(project, region, token, opener, rest, retry_seconds=retry_seconds) if found is None else found
    except Exception as e:   # noqa: BLE001 - the next deploy tries again
        return [f'WARNING: image keep: Cloud Run could not be read ({type(e).__name__}: {str(e)[:160]}); no tag changed']
    want, lines = {}, []   # {package url: {tag: version name}}
    for consumer, image in sorted(found.items()):
        m = IMAGE.match(image or '')
        if not m or m['project'] != project:
            lines.append(f'image keep: {consumer} runs {image or "no image"}, not this project\'s Artifact Registry; left alone')
            continue
        url = _package_url(m)
        version = (f"projects/{m['project']}/locations/{m['loc']}/repositories/{m['repo']}/packages/{m['package']}"
                   f"/versions/{m['digest']}")
        want.setdefault(url, {})[tag_name(consumer)] = version
    # Every package of each repository a consumer runs from, so a package nothing runs any more has its keep tags
    # read and deleted too (otherwise its versions stay kept past the cleanup's age for good).
    for repo in sorted({u.rsplit('/packages/', 1)[0] for u in want}):
        try:
            for p in rest.list_all(f'{repo}/packages', 'packages', token, **kw):
                want.setdefault(f"{repo}/packages/{urllib.parse.quote(p['name'].rsplit('/', 1)[-1], safe='')}", {})
        except Exception as e:   # noqa: BLE001 - the packages a consumer runs from are still made right
            lines.append(f'WARNING: image keep: the packages of {repo.rsplit("/", 1)[-1]} could not be listed '
                         f'({type(e).__name__}: {str(e)[:160]}); only the deployed ones looked at')
    packages = {}
    for url in want:
        try:
            parent = url.split('/v1/', 1)[1]
            flt = f'name="{parent}/tags/{PREFIX}*"'
            have = rest.list_all(f'{url}/tags?{urllib.parse.urlencode({"filter": flt})}', 'tags', token, **kw)
            packages[url] = {t['name'].rsplit('/', 1)[-1]: t.get('version') for t in have}
        except Exception as e:   # noqa: BLE001 - this package waits for the next deploy
            lines.append(f'WARNING: image keep: the keep tags of {url.rsplit("/", 1)[-1]} could not be read '
                         f'({type(e).__name__}: {str(e)[:160]}); its tags left as they are')
    wrote = kept = 0
    for url, tags in want.items():
        if url not in packages:
            continue
        have = packages[url]
        pkg = url.rsplit('/', 1)[-1]
        for tag, version in sorted(tags.items()):
            if have.get(tag) == version:
                kept += 1
                continue
            parent = url.split('/v1/', 1)[1]
            body = {'name': f'{parent}/tags/{tag}', 'version': version}
            try:
                if tag in have:
                    rest.call('PATCH', f'{url}/tags/{tag}?updateMask=version', token, body, **kw)
                else:
                    try:
                        rest.call('POST', f'{url}/tags?tagId={tag}', token, body, **kw)
                    except Exception as e:   # noqa: BLE001
                        # one release's runs may go side by side: another run made the tag first (409), so this
                        # one sets it to what it read
                        if getattr(e, 'status', None) != 409:
                            raise
                        rest.call('PATCH', f'{url}/tags/{tag}?updateMask=version', token, body, **kw)
                wrote += 1
                lines.append(f"image keep: {pkg}:{tag} -> {version.rsplit('/', 1)[-1][:19]}")
            except Exception as e:   # noqa: BLE001 - one refused tag never stops the others
                lines.append(f'WARNING: image keep: {pkg}:{tag} not written ({type(e).__name__}: {str(e)[:160]})')
        for tag in sorted(set(have) - set(tags)):   # its consumer is gone, or runs from another package now
            try:
                rest.call('DELETE', f'{url}/tags/{tag}', token, **kw)
                lines.append(f'image keep: {pkg}:{tag} deleted: nothing runs it here any more')
            except Exception as e:   # noqa: BLE001
                if getattr(e, 'status', None) == 404:   # a run beside this one deleted it first
                    continue
                lines.append(f'WARNING: image keep: {pkg}:{tag} not deleted ({type(e).__name__}: {str(e)[:160]})')
    lines.append(f'image keep: {len(found)} image(s) Cloud Run runs; {wrote} tag(s) written, {kept} already right')
    return lines
