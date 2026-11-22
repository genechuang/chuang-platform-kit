"""A deploy gate's import walk: which third-party modules a deploy's entry file imports, through the host's shared
package, and whether its requirements install them (SMADPickleBot, 8/27/26: a shared module's new `import pytz` killed a
function whose own requirements never named it; moved from its pipeline/shared/check-function-deps.py by #2160).

    imports(tree)                          -> (module_level, lazy)   top-level import names; inside any def is lazy
    package_targets(tree, package)         -> {module}               the shared package's modules one file pulls in
    compute_facts(text, package)           -> {top, lazy, shared, scripts, strings}   what a walker reads of a file
    facts_of(raw, kept, new, package)      -> facts                  by content: from `kept`, else parsed into `new`
    load_facts / save_facts                                          those facts kept across processes in one file
    reach(entry, package_dir, targets)     -> frozenset of file names the entry reaches in the package
    walk(entry, package_dir, file_facts, first_party) -> (module_level, lazy) third-party names, transitively
    requirements(path) / norm(name)                                  a requirements file's bare package names
    satisfied(mod, pkgs, import_to_pkg, namespace_prefixes)          an import name met by an installed package
    unused(direct, mods, runtime, ...)                               direct requirements no reachable import names

Module-level imports are the ones that run at start-up and can stop a container from starting; one inside a function
fails only when its code runs. `package` is the host's shared package's import name, `package_dir` its folder; the
host keeps its own import-to-package map, namespace prefixes and the packages its runtime needs that no import names.
Standard library only.
"""
import ast
import hashlib
import json
import os
import re
import sys
import tempfile

__all__ = ['SCRIPT_NAME', 'norm', 'requirements', 'imports', 'package_targets', 'string_facts', 'compute_facts',
           'facts_of', 'load_facts', 'save_facts', 'reach', 'walk', 'satisfied', 'unused']

SCRIPT_NAME = re.compile(r"""['"]([A-Za-z0-9_./-]+\.py)['"]""")   # a script file named in a string


def norm(name):
    """A requirement line or import name as the bare, lower-case, dashed package name: `Foo_Bar>=1` and
    `foo-bar @ git+https://...@v0.1.0` both give `foo-bar`."""
    return re.split(r"[<>=!~\[@\s]", name.strip(), maxsplit=1)[0].strip().lower().replace("_", "-")


def requirements(path):
    """Normalized package names from one requirements file; empty when it is missing. Comments and option lines
    (`-r`, `--hash`) are skipped."""
    pkgs = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                pkgs.add(norm(line))
    except FileNotFoundError:
        pass
    return pkgs


def imports(tree):
    """(module_level, lazy) sets of top-level import names; an import inside any function is lazy, and a relative
    import is first-party and left out. One walk that carries "inside a function" down (SMADPickleBot 10/13/26: a walk
    of each function's subtree per enclosing function was 1.4 million node visits a run)."""
    top, lazy = set(), set()
    stack = [(tree, False)]
    while stack:
        node, inside = stack.pop()
        for n in ast.iter_child_nodes(node):
            if isinstance(n, ast.Import):
                (lazy if inside else top).update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                (lazy if inside else top).add(n.module.split(".")[0])
            stack.append((n, inside or isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))))
    return top, lazy


def package_targets(tree, package):
    """Which modules of `package` one file pulls in: `from <package>.x`, `from .x`, `from . import x`,
    `from <package> import x`, `import <package>.x`."""
    dotted = package + "."
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level > 0 and n.module:
                out.add(n.module.split(".")[0])
            elif n.level > 0:
                out.update(a.name for a in n.names)
            elif n.module and n.module.startswith(dotted):
                out.add(n.module.split(".")[1])
            elif n.module == package:
                out.update(a.name for a in n.names)
        elif isinstance(n, ast.Import):
            out.update(a.name.split(".")[1] for a in n.names if a.name.startswith(dotted))
    return out


def string_facts(tree):
    """The string literals a walker could read as a path or a module (a slash, a dot or a folder name, no space, under
    160 characters) and the joins of consecutive string arguments of a call (os.path.join(ROOT, 'scripts', 'x.py')
    gives 'scripts/x.py')."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
        if isinstance(n, ast.Call):
            parts = []
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value and ' ' not in a.value:
                    parts.append(a.value)
                else:
                    if len(parts) >= 2:
                        out.add('/'.join(parts))
                    parts = []
            if len(parts) >= 2:
                out.add('/'.join(parts))
    return {s for s in out if s and ' ' not in s and len(s) <= 160
            and ('/' in s or '\\' in s or '.' in s or s in ('scripts', 'tests'))}


def compute_facts(text, package):
    """One file's facts from its source, JSON-ready sorted lists: `top` and `lazy` import names, `shared` (the
    package's modules it pulls in), `scripts` (script file names it mentions) and `strings` (string_facts)."""
    tree = ast.parse(text)
    top, lazy = imports(tree)
    return {"top": sorted(top), "lazy": sorted(lazy), "shared": sorted(package_targets(tree, package)),
            "scripts": sorted(set(SCRIPT_NAME.findall(text))), "strings": sorted(string_facts(tree))}


def facts_of(raw, kept, new, package):
    """One file's facts by its bytes: from `kept` (read from the cache file) or `new` (this process's) when this exact
    content was seen, else computed now and put in `new` for save_facts. Keyed by the content's SHA-1."""
    key = hashlib.sha1(raw).hexdigest()
    got = kept.get(key) or new.get(key)
    if got is None:
        got = compute_facts(raw.decode("utf-8"), package)
        new[key] = got
    return got


def load_facts(path, version):
    """The facts kept in the cache file at `path` ({sha1: facts}), {} when there is none, it cannot be read, or it was
    written by another `version`."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            kept = json.load(f)
        return kept.get("files", {}) if kept.get("v") == version else {}
    except (OSError, ValueError):
        return {}


def save_facts(path, version, new, kept=None, prefix="import-facts-", keep=4000):
    """`new` merged into the cache file at `path`: read again first (another process may have written meanwhile),
    merged, written whole through a temporary file beside it. Past `keep` entries only the ones seen this run (`new`
    and `kept`) are kept. A write that fails keeps nothing and says nothing; `new` is cleared either way."""
    if not new or not path:
        return
    try:
        files = load_facts(path, version)
        files.update(new)
        if len(files) > keep:
            seen = set(new) | set(kept or {})
            files = {k: v for k, v in files.items() if k in seen}
        fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=os.path.dirname(path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"v": version, "files": files}, f, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass
    new.clear()


def reach(entry_path, package_dir, targets):
    """The package's modules `entry_path` reaches, as file names ('config.py'), `__init__.py` always among them (any
    `<package>.x` import runs it). `targets(path)` is the host's (cached) package_targets of one file; a module file
    that does not exist ends that branch."""
    seen, queue = set(), [entry_path]
    reached = {"__init__.py"}
    while queue:
        path = queue.pop()
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        if os.path.dirname(path) == package_dir:
            reached.add(os.path.basename(path))
        for mod in targets(path):
            queue.append(os.path.join(package_dir, f"{mod}.py"))
    return frozenset(reached)


def walk(entry_path, package_dir, file_facts, first_party=()):
    """(module_level, lazy) third-party import names from `entry_path` through the package, transitively. The standard
    library, a module beside the entry file, names starting `_` and `first_party` (the host's own packages and the
    serving framework's) are left out. `file_facts(path)` is the host's facts reader."""
    seen, queue = set(), [entry_path]
    top, lazy = set(), set()
    while queue:
        path = queue.pop()
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        f = file_facts(path)
        top |= set(f["top"])
        lazy |= set(f["lazy"])
        for mod in f["shared"]:
            queue.append(os.path.join(package_dir, f"{mod}.py"))
    siblings = {f[:-3] for f in os.listdir(os.path.dirname(entry_path)) if f.endswith(".py")}
    drop = set(sys.stdlib_module_names) | siblings | set(first_party)
    return ({m for m in top if m not in drop and not m.startswith("_")},
            {m for m in lazy if m not in drop and not m.startswith("_")})


def satisfied(mod, pkgs, import_to_pkg=None, namespace_prefixes=None):
    """True when an import name is installed by one of `pkgs` (normalized names): by its own name, by the host's
    `import_to_pkg` entry ('yaml' -> 'PyYAML'), or for a namespace package by any package with its prefix
    ('google' -> 'google-')."""
    if norm((import_to_pkg or {}).get(mod, mod)) in pkgs:
        return True
    prefix = (namespace_prefixes or {}).get(mod)
    return bool(prefix) and any(p.startswith(prefix) for p in pkgs)


def unused(direct, mods, runtime=(), import_to_pkg=None, namespace_prefixes=None):
    """The direct requirements that no reachable import name is met by, sorted: a dead package is a build, a deploy and
    a cold start paying for nothing. `runtime` names the packages the host's runtime needs that no import names (its
    server, a driver loaded by name)."""
    return sorted(p for p in direct if p not in runtime
                  and not any(satisfied(m, {p}, import_to_pkg, namespace_prefixes) for m in mods))
