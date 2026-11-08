#!/usr/bin/env python3
"""Build once, deploy the same bytes from either path (twelve-factor V; Gene, 10/10/26 8:07 AM PT:
"follow 1 build pipeline and 12 factors in their own pipelines").

Every image and every Cloud Function source is named by a CONTENT KEY: a hash of exactly the
files that go into it (and the Dockerfile and its build arguments). Whichever path runs first,
GitHub's runner or Cloud Build, builds it and pushes it under that key; the other path, a rerun,
a failover or a later push that changes nothing in it finds it and deploys it as it is. A deploy
always names the artifact by digest (an image) or by its key's object (a function's zip), never
by a moving tag, so what was tested is what runs.

    python -m chuang_sdlc.cicd.build_once stage --dockerfile F --context C [--ignore I] [--arg K=V]... --out DIR
        Copies the files the Dockerfile's COPY/ADD lines take from C (less the ignore file's and
        the always-skipped: __pycache__, *.pyc, *.dist-info) into DIR/ctx, the Dockerfile to
        DIR/Dockerfile, and prints the key. Both paths build from DIR, so the image holds the
        same files whatever the upload or the runner's checkout carried.
    python -m chuang_sdlc.cicd.build_once build --image IMAGE ...stage's arguments [--force] [--verbose]
        The desktop's build (Gene, 10/11/26: Docker on the desktop, so an image is built here for
        free and CI deploys it): stages as above, stops when IMAGE:bo-KEY is in the registry, else
        docker-builds the pinned Dockerfile, pushes IMAGE:bo-KEY and :latest, prints the ref.
        The host's desktop build script runs it for every image before a push.
    python -m chuang_sdlc.cicd.build_once find --image IMAGE --key KEY
        Prints IMAGE@sha256:... when IMAGE:bo-KEY is in Artifact Registry, nothing otherwise.
    python -m chuang_sdlc.cicd.build_once resolve --image IMAGE --key KEY [--tag T]...
        The same, but fails ("ERROR:") when it is missing; adds the tags (the commit) to it.
    python -m chuang_sdlc.cicd.build_once zip --dir D --function NAME --bucket B [--exclude NAME]...
        A Cloud Function's source: the key of D's files, uploaded once as
        gs://B/function-sources/NAME/KEY.zip; prints that URL.
    python -m chuang_sdlc.cicd.build_once key ...stage's arguments, no --out
        The key alone (tests, and a look before a build).

The desktop's build mark (`from-mark`) is read from the bucket BUILD_MARK_BUCKET names; without one the mark answers
nothing and the registry is asked as before. In SMADPickleBot, the first host (#235, 11/8/26), pipeline/shared/build-once.py
is the shim that sets it and runs main(), so every caller's command line is unchanged.

stdlib only: it runs on GitHub's runner and in Cloud Build's cloud-sdk:slim image.
"""
import argparse
import glob
import hashlib
import json
import os
from datetime import datetime, timezone
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

VERSION = "bo1"           # bump to rebuild everything (a change to what the key covers)
TAG_PREFIX = "bo-"        # an image's content tag: IMAGE:bo-<key>
KEY_LEN = 20
ALWAYS_SKIP_DIRS = ("__pycache__", ".git")
# pip --target writes a .dist-info beside an installed package, and its direct_url.json names the
# checkout's own path, which differs between the two paths: nothing reads it at run time.
ALWAYS_SKIP_DIR_SUFFIXES = (".dist-info",)
ALWAYS_SKIP_FILE_SUFFIXES = (".pyc",)


# --------------------------------------------------------------------------- ignore files
def _pattern_regex(pat):
    """A gitignore pattern's body (no '!' and no trailing '/') as a regex over a relative path."""
    anchored = "/" in pat
    pat = pat.lstrip("/")
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("/**", i) and i + 3 == len(pat):
            out.append("/.*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                cls = pat[i + 1:j].replace("\\", "\\\\")
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append(f"[{cls}]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    body = "".join(out)
    return re.compile(("^" if anchored else "^(?:.*/)?") + body + "$")


class Ignore:
    """gitignore semantics, as gcloudignore has them: the last matching line wins, '!' re-includes,
    a trailing '/' matches directories only, a pattern with no inner '/' matches at any depth, and
    nothing under an ignored directory can be re-included."""

    def __init__(self, text=""):
        self.rules = []
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("#"):
                continue
            neg = line.startswith("!")
            if neg:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            if not line or line in (".", ".."):
                continue
            self.rules.append((neg, dir_only, _pattern_regex(line)))

    @classmethod
    def load(cls, path):
        if not path:
            return cls()
        with open(path, encoding="utf-8") as f:
            return cls(f.read())

    def _match(self, rel, is_dir):
        hit = False
        for neg, dir_only, rx in self.rules:
            if dir_only and not is_dir:
                continue
            if rx.match(rel):
                hit = not neg
        return hit

    def ignored(self, rel, is_dir=False):
        parts = rel.split("/")
        for n in range(1, len(parts)):
            if self._match("/".join(parts[:n]), True):
                return True
        return self._match(rel, is_dir)


def _always_skipped(rel):
    parts = rel.split("/")
    if any(p in ALWAYS_SKIP_DIRS or p.endswith(ALWAYS_SKIP_DIR_SUFFIXES) for p in parts[:-1]):
        return True
    last = parts[-1]
    return last in ALWAYS_SKIP_DIRS or last.endswith(ALWAYS_SKIP_FILE_SUFFIXES)


# --------------------------------------------------------------------------- what an image takes
def copy_sources(dockerfile_text):
    """The context paths a Dockerfile's COPY and ADD lines read (not --from= stages), in order."""
    joined = re.sub(r"\\\r?\n", " ", dockerfile_text)
    sources = []
    for line in joined.splitlines():
        s = line.strip()
        m = re.match(r"(?i)^(COPY|ADD)\s+(.*)$", s)
        if not m:
            continue
        rest = m.group(2).strip()
        if rest.startswith("["):
            import json
            toks = json.loads(rest)
            flags = []
        else:
            toks = shlex.split(rest)
            flags = [t for t in toks if t.startswith("--")]
            toks = [t for t in toks if not t.startswith("--")]
        if any(f.startswith("--from") for f in flags) or len(toks) < 2:
            continue
        sources += toks[:-1]
    return sources


def context_files(context, sources, ignore):
    """Every file the sources name under context, relative to it, sorted; ignored ones left out."""
    found = set()
    for src in sources:
        src = src.strip()
        pattern = os.path.normpath(os.path.join(context, src))
        hits = glob.glob(pattern) if any(ch in src for ch in "*?[") else [pattern]
        if not hits or not all(os.path.exists(h) for h in hits):
            raise SystemExit(f"ERROR: the Dockerfile copies {src!r}, which is not in {context}")
        for hit in hits:
            if os.path.isdir(hit):
                for base, dirs, files in os.walk(hit):
                    rel_base = os.path.relpath(base, context).replace("\\", "/")
                    rel_base = "" if rel_base == "." else rel_base + "/"
                    dirs[:] = sorted(d for d in dirs
                                     if not _always_skipped(rel_base + d + "/x")
                                     and not ignore.ignored(rel_base + d, True))
                    for f in files:
                        rel = rel_base + f
                        if not _always_skipped(rel) and not ignore.ignored(rel):
                            found.add(rel)
            else:
                rel = os.path.relpath(hit, context).replace("\\", "/")
                if not _always_skipped(rel) and not ignore.ignored(rel):
                    found.add(rel)
    return sorted(found)


def _file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_key(root, files, extra=b""):
    """The key: VERSION, the extra bytes (a Dockerfile and its build arguments), then each file's
    relative path and its sha256, in order."""
    h = hashlib.sha256(VERSION.encode() + b"\0" + extra + b"\0")
    for rel in files:
        h.update(rel.encode() + b"\0" + _file_digest(os.path.join(root, rel)).encode() + b"\n")
    return h.hexdigest()[:KEY_LEN]


# --------------------------------------------------------------------------- base images
# Twelve-factor II (Gene, 10/10/26: "yes do ... dependencies fix for 12F"): every base image is
# built from by digest, never by a tag that can move under the same Dockerfile (cloud-sdk:slim
# moves every week). The digests live in one file of the host's, named by BASE_IMAGES_LOCK (SMADPickleBot:
# pipeline/shared/base-images.json, refreshed by its scripts/pin-base-images.py, set by its shim), else a
# base-images.json beside this module; the Dockerfiles keep their readable tags, and the staged Dockerfile both
# paths build from names `tag@sha256:...`. A new digest is a new content key, so a base refresh is a rebuild.
LOCK_PATH = os.environ.get("BASE_IMAGES_LOCK") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "base-images.json")
_FROM = re.compile(r"(?i)^(\s*FROM\s+(?:--platform=\S+\s+)?)(\S+)(.*)$")
_AS = re.compile(r"(?i)\s+AS\s+(\S+)")
_ARG = re.compile(r"(?i)^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(\S*))?\s*$")


def load_lock(path=None):
    import json
    path = path or LOCK_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("images", {})
    except FileNotFoundError:
        return {}


def _expand(value, env):
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                  lambda m: env.get(m.group(1) or m.group(2), ""), value)


def base_images(dockerfile_text, args=()):
    """The external base images a Dockerfile builds FROM, its global ARGs and the build arguments
    applied (`python:${PYTHON_VERSION}-slim` -> `python:3.14-slim`); its own stages, `scratch`
    and refs already by digest left out."""
    return [img for _i, img in _from_lines(dockerfile_text, args)[1]]


def _from_lines(text, args):
    env, aliases, seen_from, out = {}, set(), False, []
    for k, _sep, v in (a.partition("=") for a in args):
        env[k] = v
    lines = text.split("\n")
    for i, line in enumerate(lines):
        a = _ARG.match(line)
        if a and not seen_from and a.group(1) not in env:
            env[a.group(1)] = a.group(2) or ""
        m = _FROM.match(line)
        if not m:
            continue
        seen_from = True
        image = _expand(m.group(2), env)
        alias = _AS.search(m.group(3))
        if image not in aliases and image != "scratch" and "@sha256:" not in image:
            out.append((i, image))
        if alias:
            aliases.add(alias.group(1))
    return lines, out


def pin_dockerfile(text, args, lock):
    """The Dockerfile with every external FROM naming `tag@digest` from the lock; ERROR when a
    base image is not in it."""
    lines, refs = _from_lines(text, args)
    for i, image in refs:
        digest = lock.get(image)
        if not digest:
            raise SystemExit(f"ERROR: no pinned digest for the base image {image} in the base-images lock ({LOCK_PATH}): "
                             f"refresh the lock and commit it")
        m = _FROM.match(lines[i])
        lines[i] = f"{m.group(1)}{image}@{digest}{m.group(3)}"
    return "\n".join(lines)


def image_plan(dockerfile, context, ignore_path, args, lock=None):
    """(files, key, pinned Dockerfile text) for an image: the files its COPY lines take, the content
    key over them, the pinned Dockerfile and the build arguments."""
    with open(dockerfile, encoding="utf-8", newline="") as f:
        text = f.read()
    pinned = pin_dockerfile(text, args, load_lock() if lock is None else lock)
    files = context_files(context, copy_sources(pinned), Ignore.load(ignore_path))
    extra = pinned.encode() + b"\0" + "\n".join(sorted(args)).encode()
    return files, content_key(context, files, extra), pinned


# --------------------------------------------------------------------------- the registry
def _gcloud(*argv):
    # BUILD_ONCE_GCLOUD: the command to run instead of gcloud (tests/test-build-once.py's fake).
    cmd = shlex.split(os.environ.get("BUILD_ONCE_GCLOUD", "gcloud"))
    # Windows installs gcloud as gcloud.cmd, which Python finds only through PATHEXT (the desktop's builds, 10/11/26).
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run([*cmd, *argv], capture_output=True, text=True, encoding="utf-8", errors="replace")


def image_marked(image, key, mark):
    """True when IMAGE:<mark>-<key> exists: the tag a desktop build adds (build --mark desktop), which tells
    a deploy that the desktop made this image and will smoke-test it (Gene, 10/11/26: the smoke test runs
    on the desktop when the box is up, on the runner when it is not)."""
    r = _gcloud("artifacts", "docker", "images", "describe", f"{image}:{mark}-{key}", "--format=value(image_summary.digest)")
    return r.returncode == 0 and r.stdout.strip().startswith("sha256:")


def zip_marked(url, mark):
    """True when the marker object beside a function's zip exists (zip --mark): gs://.../<key>.<mark>."""
    return _gcloud("storage", "objects", "describe", f"{url[:-4]}.{mark}", "--format=value(name)").returncode == 0


def find_image(image, key):
    r = _gcloud("artifacts", "docker", "images", "describe", f"{image}:{TAG_PREFIX}{key}",
                "--format=value(image_summary.digest)")
    digest = r.stdout.strip() if r.returncode == 0 else ""
    return f"{image}@{digest}" if digest.startswith("sha256:") else ""


# --------------------------------------------------------------------------- commands
def cmd_key(a):
    _files, key, _pinned = image_plan(a.dockerfile, a.context, a.ignore, a.arg)
    print(key)


def cmd_stage(a):
    files, key, pinned = image_plan(a.dockerfile, a.context, a.ignore, a.arg)
    ctx = os.path.join(a.out, "ctx")
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    os.makedirs(ctx)
    for rel in files:
        dest = os.path.join(ctx, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(a.context, rel), dest)
    # The pinned Dockerfile (every base image by digest), not the checkout's.
    with open(os.path.join(a.out, "Dockerfile"), "w", encoding="utf-8", newline="") as f:
        f.write(pinned)
    print(f"staged {len(files)} file(s) in {ctx}", file=sys.stderr)
    print(key)


def cmd_find(a):
    # Nothing at all when it is not there: an empty line made a one-byte .bo-ref that
    # build-once-gcb.sh read as "built already" (10/10/26, the first Cloud Build run).
    if a.mark:
        print("true" if image_marked(a.image, a.key, a.mark) else "false")
        return 0
    ref = find_image(a.image, a.key)
    if ref:
        print(ref)


MARK_URL = "https://storage.googleapis.com/{bucket}/builds/desktop-{sha}.json"   # the host's public reports bucket


def mark_bucket(env=None) -> str:
    """The bucket the desktop's build marks are read from: BUILD_MARK_BUCKET, '' when the host set none."""
    return (os.environ if env is None else env).get("BUILD_MARK_BUCKET", "")


def from_mark(image, key, sha, fetch=None, bucket=None):
    """IMAGE@sha256:... from the desktop's build mark for `sha` when it lists this image under this very key, else ''
    (10/20/26: one public fetch instead of the registry's find, mark check, tag and size calls, about 7 s a deploy job;
    the host's build script writes the mark, SMADPickleBot's pipeline/desktop/build-images.py write_build_mark). Any
    doubt, a missing mark, no bucket named, another key or a ref without a digest, is '' and the job asks the registry
    as before."""
    import json
    import urllib.request
    bucket = bucket or mark_bucket()
    if not bucket:
        return ""
    if fetch is None:
        def fetch(url):
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}), timeout=10) as r:
                return r.read().decode("utf-8")
    try:
        got = (json.loads(fetch(MARK_URL.format(bucket=bucket, sha=sha))).get("refs") or {}).get(image) or {}
    except Exception:   # noqa: BLE001 -- any failure is "not in the mark"
        return ""
    ref = str(got.get("ref") or "")
    return ref if got.get("key") == key and ref.startswith(f"{image}@sha256:") else ""


def cmd_from_mark(a):
    ref = from_mark(a.image, a.key, a.sha)
    if ref:
        print(ref)


def size_line(image, ref):
    """'size: daily 399 MB compressed (last 396 MB)': this image's compressed size in the registry and the
    version before it, for the deploy log (Gene, 10/11/26: the size of every build, this one and the last, in every
    release report; Mr Sandman: a cloud session reads only the log). '' when the registry cannot be read."""
    r = _gcloud("artifacts", "docker", "images", "list", image, "--format=json", "--limit=50", "--sort-by=~createTime")
    if r.returncode != 0 or not r.stdout.strip():
        return ""
    try:
        rows = [row for row in json.loads(r.stdout) if (row.get("metadata") or {}).get("imageSizeBytes")]
    except (ValueError, TypeError):
        return ""
    digest = ref.split("@", 1)[-1]
    this = next((row for row in rows if row.get("version") == digest), None)
    if not this:
        return ""
    older = [row for row in rows if row.get("version") != digest and (row.get("createTime") or "") < (this.get("createTime") or "")]
    mb = lambda row: f"{int(row['metadata']['imageSizeBytes']) / 1048576:.0f} MB"   # noqa: E731
    return f"size: {image.rsplit('/', 1)[-1]} {mb(this)} compressed" + (f" (last {mb(older[0])})" if older else " (no earlier version)")


def cmd_resolve(a):
    ref = find_image(a.image, a.key)
    if not ref:
        print(f"ERROR: {a.image}:{TAG_PREFIX}{a.key} is not in Artifact Registry after the build")
        return 1
    for t in a.tag or []:
        r = _gcloud("artifacts", "docker", "tags", "add", ref, f"{a.image}:{t}", "--quiet")
        if r.returncode != 0:
            print(f"(could not tag {t}: {r.stderr.strip()[:200]})", file=sys.stderr)
    line = size_line(a.image, ref)
    if line:
        print(line, file=sys.stderr)
    print(ref)
    return 0


def _docker(*argv, **kw):
    # BUILD_ONCE_DOCKER: the command to run instead of docker (the tests' fake).
    cmd = shlex.split(os.environ.get("BUILD_ONCE_DOCKER", "docker"))
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    # UTF-8 with replacement: docker's output carries bytes Windows' default code page cannot decode
    # (the first desktop build, 10/11/26, built and then died reading its own output).
    return subprocess.run([*cmd, *argv], text=True, encoding="utf-8", errors="replace", **kw)


def image_size(image, ref):
    """The registry's compressed size of `ref` (IMAGE@sha256:...) in bytes, or None when it cannot be read."""
    digest = ref.split("@", 1)[-1]
    r = _gcloud("artifacts", "docker", "images", "list", image, "--format=json", "--limit=50", "--sort-by=~createTime")
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        for row in json.loads(r.stdout):
            if row.get("version") == digest:
                size = (row.get("metadata") or {}).get("imageSizeBytes")
                return int(size) if size else None
    except (ValueError, TypeError):
        pass
    return None


def cmd_build(a):
    """Build and push an image from this machine (Gene, 10/11/26: the desktop builds, CI deploys what it
    pushed): stage exactly as CI does, find the content key in the registry and stop when it is there,
    else `docker build` from the pinned Dockerfile, tag IMAGE:bo-<key> and IMAGE:latest and push both.
    Every deploy path then finds the key and deploys that image by digest without building. Prints the
    stage times in seconds and the registry's compressed size; the ref on stdout, like `resolve`."""
    import time
    files, key, pinned = image_plan(a.dockerfile, a.context, a.ignore, a.arg)
    ref = find_image(a.image, key)
    if ref and not a.force:
        if a.mark and not image_marked(a.image, key, a.mark):
            # Built elsewhere (CI, before the desktop had it): the mark alone, so the deploy still knows
            # the desktop will smoke-test it.
            _gcloud("artifacts", "docker", "tags", "add", ref, f"{a.image}:{a.mark}-{key}", "--quiet")
        print(f"already built for this content ({key}): {ref}", file=sys.stderr)
        print(ref)
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        ctx = os.path.join(tmp, "ctx")
        os.makedirs(ctx)
        for rel in files:
            dest = os.path.join(ctx, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(os.path.join(a.context, rel), dest)
        with open(os.path.join(tmp, "Dockerfile"), "w", encoding="utf-8", newline="") as f:
            f.write(pinned)
        tag, latest, cache = f"{a.image}:{TAG_PREFIX}{key}", f"{a.image}:latest", f"{a.image}:cache"
        tags = [tag, latest] + ([f"{a.image}:{a.mark}-{key}"] if a.mark else [])
        build_args = [x for arg in a.arg for x in ("--build-arg", arg)]
        t0 = time.monotonic()
        # One plain manifest (no provenance or SBOM attestation: Docker Desktop otherwise pushes an image index
        # whose tag the registry lists with no size, 10/11/26; the GitHub action sets provenance: false too).
        # #651 (option A of docs/container-build-plan.md): every stage's cache in the registry at IMAGE:cache
        # (`--cache-to ... mode=max`), read back by `--cache-from`. The inline cache before it kept only the final
        # stage's layers in the image, so a cold box, GitHub's runner or Cloud Build rebuilt every pip install (ten
        # function images, 361 s, 10/21/26). No `docker pull :latest` first: BuildKit fetches only the cache layers it
        # needs. The `:latest` layers stay a second source for an image built before the cache ref existed. The
        # desktop's docker driver exports a registry cache with the containerd image store, which it has; the Cloud
        # Build twin (build-once-gcb.sh) and the GitHub action read the same ref and export what their builders can.
        r = _docker("buildx", "build", "--provenance=false", "--sbom=false",
                    "--cache-to", f"type=registry,ref={cache},mode=max", "--cache-from", f"type=registry,ref={cache}",
                    "--cache-from", latest, *build_args,
                    "-f", os.path.join(tmp, "Dockerfile"), *[x for t in tags for x in ("-t", t)], ctx,
                    stdout=sys.stderr if a.verbose else subprocess.DEVNULL, stderr=subprocess.STDOUT if a.verbose else subprocess.PIPE)
        built = time.monotonic() - t0
        if r.returncode != 0:
            print(f"ERROR: {a.image.rsplit('/', 1)[-1]} did not build ({built:.0f}s): {(r.stderr or '').strip()[-600:]}")
            return 1
        t1 = time.monotonic()
        for t in tags:
            p = _docker("push", "-q", t, capture_output=True)
            if p.returncode != 0:
                print(f"ERROR: {t} could not be pushed: {(p.stderr or '').strip()[-300:]}")
                return 1
        pushed = time.monotonic() - t1
    ref = find_image(a.image, key)
    if not ref:
        print(f"ERROR: {tag} is not in Artifact Registry after the push")
        return 1
    size = image_size(a.image, ref)
    print(f"built {a.image.rsplit('/', 1)[-1]}: build {built:.0f}s, push {pushed:.0f}s"
          + (f", {size / 1048576:.0f} MB compressed in the registry" if size else ""), file=sys.stderr)
    print(ref)
    return 0


def cmd_zip(a):
    ignore = Ignore.load(os.path.join(a.dir, ".gcloudignore")) if os.path.exists(os.path.join(a.dir, ".gcloudignore")) else Ignore()
    skip = set(a.exclude or []) | {".gcloudignore"}
    files = [f for f in context_files(a.dir, ["."], ignore) if f.split("/")[-1] not in skip]
    key = content_key(a.dir, files)
    url = f"gs://{a.bucket}/function-sources/{a.function}/{key}.zip"
    if _gcloud("storage", "objects", "describe", url, "--format=value(name)").returncode == 0:
        print(f"{url} is already built ({len(files)} file(s))", file=sys.stderr)
        if a.mark:
            _mark_zip(url, a.mark)
        print(url)
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, f"{key}.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in files:
                info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(os.path.join(a.dir, rel), "rb") as f:
                    z.writestr(info, f.read())
        r = _gcloud("storage", "cp", "--no-clobber", path, url)
        if r.returncode != 0:
            print(f"ERROR: the function source could not be uploaded to {url}: {r.stderr.strip()[:300]}")
            return 1
    if a.mark:
        _mark_zip(url, a.mark)
    print(f"built {url} ({len(files)} file(s))", file=sys.stderr)
    print(url)
    return 0


def _mark_zip(url, mark):
    """The marker object beside a zip (gs://.../<key>.<mark>): who built it and when; zip_marked() reads it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, mark)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"by": mark, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, f)
        _gcloud("storage", "cp", path, f"{url[:-4]}.{mark}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("key", "stage", "build"):
        s = sub.add_parser(name)
        s.add_argument("--dockerfile", required=True)
        s.add_argument("--context", required=True)
        s.add_argument("--ignore", default="")
        s.add_argument("--arg", action="append", default=[], help="a build argument, K=V")
        if name == "stage":
            s.add_argument("--out", required=True)
        if name == "build":
            s.add_argument("--image", required=True)
            s.add_argument("--force", action="store_true", help="build and push even when the key is in the registry")
            s.add_argument("--verbose", action="store_true", help="show docker's build output")
            s.add_argument("--mark", default="", help="also tag IMAGE:<mark>-<key> (the desktop's builds: 'desktop')")
    for name in ("find", "resolve"):
        s = sub.add_parser(name)
        s.add_argument("--image", required=True)
        s.add_argument("--key", required=True)
        if name == "resolve":
            s.add_argument("--tag", action="append")
        if name == "find":
            s.add_argument("--mark", default="", help="print true/false: whether IMAGE:<mark>-<key> exists")
    s = sub.add_parser("from-mark")
    s.add_argument("--image", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--sha", required=True)
    s = sub.add_parser("zip")
    s.add_argument("--dir", required=True)
    s.add_argument("--function", required=True)
    s.add_argument("--bucket", required=True)
    s.add_argument("--exclude", action="append")
    s.add_argument("--mark", default="", help="also write the marker object <key>.<mark> beside the zip")
    a = p.parse_args(argv)
    return {"key": cmd_key, "stage": cmd_stage, "build": cmd_build, "find": cmd_find, "resolve": cmd_resolve,
            "from-mark": cmd_from_mark, "zip": cmd_zip}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
