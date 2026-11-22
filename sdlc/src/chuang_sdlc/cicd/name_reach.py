"""Which names of a host's shared package a deploy runs, and which names a push changed (SMADPickleBot, 10/24/26:
"Do stop redeploying functions that didn't change"; moved from its pipeline/shared/name_reach.py by #2160).

A deploy gate that redeploys a function whenever a push changes a shared-package module its entry reaches is a
file-level test: one removed constant every function imports and none reads redeploys them all. This narrows it to
names: a function redeploys when a package name it can reach changed, a module's start-up code changed, or anything
the walk cannot follow.

    uses(entry_files, package_dir, package)   -> {module: {names} | ALL}   what the entries can reach, transitively
    changed_names(before_src, after_src)      -> {names} | ALL           what one module's push changed
    hit(uses, module, changed)                -> True when the two meet

A module's top-level names are its functions, classes, assignments and import aliases; everything else at the top
(calls, `if`, `try`, `with`, loops) is its start-up code, INIT, which every importer runs, so a change there counts for
all of them. Docstrings are left out of the comparison (a docstring edit deploys nothing); comments never reach the
syntax tree. Whenever the walk is unsure it says ALL: `import *`, a module object used other than as `mod.name` or
`getattr(mod, 'name')`, `globals()`, `vars()`, `__getattr__` on a module, a module that cannot be parsed. `package` is
the host's shared package's import name. Standard library only.
"""
import ast
import functools
import os

__all__ = ['ALL', 'INIT', 'DYNAMIC', 'definitions', 'changed_names', 'uses', 'hit']

ALL = '*'          # every name of the module
INIT = '__init__'  # a module's start-up code: the top-level statements that are not a def, class, assignment or import
DYNAMIC = ('globals', 'vars', '__import__')


def _strip(node):
    """A copy of `node` with every docstring removed (module, function, class)."""
    node = ast.parse(ast.unparse(node))
    for n in ast.walk(node):
        body = getattr(n, 'body', None)
        if (isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            n.body = body[1:] or [ast.Pass()]
    return node


def _bound(stmt):
    """The names one top-level statement binds, or None when it is start-up code."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return [a.asname or a.name.split('.')[0] for a in stmt.names if a.name != '*'] or None
    targets = (stmt.targets if isinstance(stmt, ast.Assign) else
               [stmt.target] if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) else None)
    if targets and all(isinstance(t, ast.Name) for t in targets):
        return [t.id for t in targets]
    return None


def definitions(tree):
    """{name: [its top-level statements]} and the module's start-up statements, the module's docstring left out."""
    defs, init = {}, []
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    for stmt in body:
        names = _bound(stmt)
        if names is None:
            init.append(stmt)
        else:
            for name in names:
                defs.setdefault(name, []).append(stmt)
    return defs, init


def _dump(stmts):
    return '\n'.join(ast.dump(_strip(ast.Module(body=[s], type_ignores=[]))) for s in stmts)


def changed_names(before, after):
    """The names one module's push changed between two sources (None for a side that does not exist): a name whose
    statements differ, appeared or went, and INIT when the start-up code differs; ALL when a side does not parse."""
    try:
        old = definitions(ast.parse(before)) if before is not None else ({}, [])
        new = definitions(ast.parse(after)) if after is not None else ({}, [])
    except SyntaxError:
        return ALL
    out = {n for n in set(old[0]) | set(new[0]) if _dump(old[0].get(n, [])) != _dump(new[0].get(n, []))}
    if _dump(old[1]) != _dump(new[1]):
        out.add(INIT)
    return out


def _module_of(node, here, package):
    """The `package` module an import names, or None: `from <package>.X ...` and, inside the package, `from .X ...`;
    '' for the package itself (`from <package> import X`, `from . import X`)."""
    if isinstance(node, ast.ImportFrom):
        if node.level == 1 and here:
            return node.module.split('.')[0] if node.module else ''
        if node.level == 0 and node.module:
            if node.module == package:
                return ''
            if node.module.startswith(package + '.'):
                return node.module.split('.')[1]
    return None


class _Uses:
    """The names one file's code reaches, read from its syntax tree."""

    def __init__(self, tree, here, package):
        self.here = here            # the module's name when the file is in the package, else ''
        self.package = package
        self.found = {}             # {module: set(names) | ALL}
        self.aliases = {}           # {local name: package module} for a bound module object
        self.local = set()          # the module's own top-level names it references (package files only)
        self._aliases(tree)

    def add(self, module, name):
        if self.found.get(module) == ALL:
            return
        if name == ALL:
            self.found[module] = ALL
        else:
            self.found.setdefault(module, set()).add(name)

    def _aliases(self, tree):
        package = self.package
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith(package + '.'):
                        mod = a.name.split('.')[1]
                        self.add(mod, INIT)
                        if a.asname:
                            self.aliases[a.asname] = mod
                        else:
                            self.aliases.setdefault(package, None)   # <package>.X.attr, read in visit()
            mod = _module_of(n, self.here, package)
            if mod is None:
                continue
            for a in n.names:
                if mod == '':                       # from <package> import X: X is a module
                    self.add(a.name, INIT)
                    self.aliases[a.asname or a.name] = a.name
                elif a.name == '*':
                    self.add(mod, ALL)
                else:
                    self.add(mod, INIT)
                    self.add(mod, a.name)

    def visit(self, node, top_names=()):
        """Every reference in `node`: a module alias's attributes, a module object used otherwise (ALL), the
        module's own top-level names (for the caller's closure), and the dynamic lookups that defeat the walk."""
        for n in ast.walk(node):
            if isinstance(n, ast.Attribute):
                base = n.value
                if isinstance(base, ast.Name) and base.id in self.aliases and self.aliases[base.id]:
                    self.add(self.aliases[base.id], n.attr)
                elif (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                      and base.value.id == self.package):
                    self.add(base.attr, INIT)
                    self.add(base.attr, n.attr)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if n.func.id in DYNAMIC:
                    self.add(self.here or '<entry>', ALL)
                elif (n.func.id == 'getattr' and n.args and isinstance(n.args[0], ast.Name)
                      and self.aliases.get(n.args[0].id)):
                    attr = n.args[1] if len(n.args) > 1 else None
                    mod = self.aliases[n.args[0].id]
                    self.add(mod, attr.value if isinstance(attr, ast.Constant) and isinstance(attr.value, str) else ALL)
        # A module alias used as a bare value (passed, returned, stored) can reach any of its names.
        attr_bases = {id(n.value) for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        getattr_args = {id(n.args[0]) for n in ast.walk(node)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'getattr' and n.args}
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if n.id in self.aliases and self.aliases[n.id] and id(n) not in attr_bases | getattr_args:
                    self.add(self.aliases[n.id], ALL)
                if n.id in top_names:
                    self.local.add(n.id)


@functools.lru_cache(maxsize=None)
def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None


@functools.lru_cache(maxsize=None)
def _module_aliases(path, package):
    """{local name: package module} for every module object a package file binds anywhere."""
    tree = _read(path)
    return dict(_Uses(tree, os.path.basename(path)[:-3], package).aliases) if tree is not None else {}


def uses(entries, core_dir, package):
    """{module: {names} | ALL} that the entry files (whole: a main.py or a job's script runs top to bottom) can
    reach through `package`, transitively: a reached name brings the module's names its statements reference and
    their imports, and every reached module its start-up code (INIT) and whatever that references. A module the walk
    cannot read is ALL. `core_dir` is the package's folder."""
    found = {}

    def add(module, names):
        if found.get(module) == ALL:
            return False
        if names == ALL:
            found[module] = ALL
            return True
        have = found.setdefault(module, set())
        new = set(names) - have
        have |= new
        return bool(new)

    queue = []
    for path in entries:
        tree = _read(path)
        if tree is None:
            continue
        u = _Uses(tree, '', package)
        u.visit(tree)
        for mod, names in u.found.items():
            if add(mod, names):
                queue.append(mod)
    done = {}
    while queue:
        mod = queue.pop()
        if mod == '<entry>':
            continue
        tree = _read(os.path.join(core_dir, f'{mod}.py'))
        if tree is None:
            found[mod] = ALL
            continue
        defs, init = definitions(tree)
        want = found.get(mod)
        names = set(defs) | {INIT} if want == ALL else set(want or ()) | {INIT}
        todo = names - done.setdefault(mod, set())
        while todo:
            name = todo.pop()
            done[mod].add(name)
            stmts = init if name == INIT else defs.get(name, [])
            body = ast.Module(body=list(stmts), type_ignores=[])
            u = _Uses(body, mod, package)
            # aliases come from the whole module: a function body uses the module's top-level imports
            u.aliases.update(_module_aliases(os.path.join(core_dir, f'{mod}.py'), package))
            u.visit(body, set(defs))
            for other, onames in u.found.items():
                if other == mod:
                    if onames == ALL:
                        found[mod] = ALL
                        todo |= (set(defs) | {INIT}) - done[mod]
                    else:
                        todo |= set(onames) - done[mod]
                    continue
                if add(other, onames):
                    queue.append(other)
            todo |= u.local - done[mod]
        found[mod] = ALL if found.get(mod) == ALL else (found.get(mod, set()) | done[mod])
    return found


def hit(reached, module, changed):
    """Whether a push's `changed` names of `module` meet what a deploy `reached` of it ({module: names | ALL})."""
    if module not in reached or not changed:
        return False
    want = reached[module]
    return changed == ALL or want == ALL or bool(set(changed) & set(want))
