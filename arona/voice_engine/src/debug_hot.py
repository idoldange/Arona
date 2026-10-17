"""Hot patching of the running bot (used by the /debug/ide editor): apply an edited .py file to the live process, no restart.

The new source is compiled; nothing at module level is re-run. Instead, for the module(s) of that file that are loaded:
  - every function / method whose code changed gets its __code__ (and defaults) swapped IN PLACE, so every existing reference
    (event handlers, registered commands, other modules that did `from x import f`, running tasks' next calls) sees the new code.
    Decorated objects (discord commands, functools.wraps, lru_cache, property, staticmethod/classmethod, tasks.loop) are unwrapped;
  - new top-level functions / classes / methods are defined, missing imports and missing globals are executed;
  - literal constants (`X = 5`) that changed between the previously loaded version and the new text are re-assigned.
Anything else at top level (changed statements, new decorators on old functions, changed class bodies...) is only reported:
that needs a restart. Frames that are executing right now keep running the old code until their next call.
"""
import ast
import asyncio
import copy
import linecache
import os
import sys
import time
import types
from typing import Any, Dict, List, Optional

PROC_START = time.time() - 5             # disk content older than this is what the process loaded
_last: Dict[str, str] = {}               # path -> source applied last (baseline for "what changed")
_lock = asyncio.Lock()
_CO_OPT = 0x1                            # CO_OPTIMIZED: real functions (not class bodies / module code)


def modules_for(path: str) -> List[types.ModuleType]:
    want = os.path.normcase(os.path.realpath(path))
    base = os.path.basename(path).lower()
    out = []
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if not f or not isinstance(f, str) or os.path.basename(f).lower() != base:
            continue
        try:
            if os.path.normcase(os.path.realpath(f)) == want:
                out.append(m)
        except OSError:
            pass
    return out


def check_syntax(src: str, path: str) -> Optional[Dict[str, Any]]:
    try:
        compile(src, path, "exec", dont_inherit=True)
    except SyntaxError as e:
        return {"line": e.lineno or 1, "col": e.offset or 1, "msg": e.msg}
    except (ValueError, MemoryError) as e:
        return {"line": 1, "col": 1, "msg": str(e)}
    return None


def _sig(c: types.CodeType):
    return (c.co_code, tuple(_sig(k) if isinstance(k, types.CodeType) else k for k in c.co_consts), c.co_names, c.co_varnames,
            c.co_freevars, c.co_cellvars, c.co_argcount, c.co_kwonlyargcount, c.co_posonlyargcount, c.co_flags)


def _collect(code: types.CodeType, out: Dict[str, List[types.CodeType]]) -> None:
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            q = c.co_qualname
            if c.co_flags & _CO_OPT and "<" not in q:
                out.setdefault(q, []).append(c)
            _collect(c, out)


def _nodes(tree: ast.AST) -> Dict[str, List[ast.AST]]:
    out: Dict[str, List[ast.AST]] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(prefix + ch.name, []).append(ch)
            elif isinstance(ch, ast.ClassDef):
                visit(ch, prefix + ch.name + ".")
            elif not isinstance(ch, ast.Lambda):
                visit(ch, prefix)
    visit(tree, "")
    return out


def _funcs_of(obj: Any, depth: int = 0) -> List[types.FunctionType]:
    """The real function objects hiding behind a decorated attribute."""
    if obj is None or depth > 6:
        return []
    if isinstance(obj, (staticmethod, classmethod)):
        return _funcs_of(obj.__func__, depth + 1)
    if isinstance(obj, property):
        return [f for p in (obj.fget, obj.fset, obj.fdel) for f in _funcs_of(p, depth + 1)]
    out: List[types.FunctionType] = []
    if isinstance(obj, types.FunctionType):
        out.append(obj)
    for attr in ("__wrapped__", "callback", "coro", "__func__", "func"):
        try:
            v = getattr(obj, attr, None)
        except Exception:
            v = None
        if v is not None and v is not obj:
            out += _funcs_of(v, depth + 1)
    return out


def _live(mod: types.ModuleType, qual: str) -> List[types.FunctionType]:
    parts = qual.split(".")
    obj = mod.__dict__.get(parts[0])
    for p in parts[1:]:
        d = getattr(obj, "__dict__", None) if isinstance(obj, type) else None
        if d is None or p not in d:
            return []
        obj = d[p]
    return [f for f in _funcs_of(obj) if f.__code__.co_qualname == qual]


def _exec_nodes(nodes: List[ast.stmt], g: Dict[str, Any], filename: str) -> None:
    tree = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(tree)
    exec(compile(tree, filename, "exec", dont_inherit=True), g)


def _new_defaults(node: ast.AST, g: Dict[str, Any], in_class: bool):
    n = copy.copy(node)
    n.decorator_list = []
    n.body = [ast.Pass()]
    top: ast.stmt = n
    if in_class:
        top = ast.ClassDef(name="_T", bases=[], keywords=[], body=[n], decorator_list=[])
    ns = dict(g)
    _exec_nodes([top], ns, "<hot-defaults>")
    f = ns["_T"].__dict__[n.name] if in_class else ns[n.name]
    return f.__defaults__, f.__kwdefaults__


def _toplevel(body: List[ast.stmt]):
    for st in body:
        yield st
        if isinstance(st, ast.If):
            yield from _toplevel(st.body)
            yield from _toplevel(st.orelse)
        elif isinstance(st, ast.Try):
            yield from _toplevel(st.body)
            for h in st.handlers:
                yield from _toplevel(h.body)
            yield from _toplevel(st.orelse)
            yield from _toplevel(st.finalbody)


def _assign_map(tree: ast.Module) -> Dict[str, str]:
    out = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            out[st.targets[0].id] = ast.dump(st.value)
    return out


def _other_dumps(tree: ast.Module) -> set:
    skip = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)
    out = set()
    for st in tree.body:
        if isinstance(st, skip) or (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)):
            continue
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            continue
        out.add(ast.dump(st))
    return out


def _apply_module(mod: types.ModuleType, tree: ast.Module, codes: Dict[str, List[types.CodeType]], nodes: Dict[str, List[ast.AST]],
                  base_tree: Optional[ast.Module], filename: str, res: Dict[str, Any]) -> None:
    g = mod.__dict__
    # 1) imports / globals that the new code may need
    for st in _toplevel(tree.body):
        try:
            if isinstance(st, ast.Import):
                for a in st.names:
                    if (a.asname or a.name.split(".")[0]) not in g:
                        _exec_nodes([ast.Import(names=[a])], g, filename)
                        res["imports"].append("import " + a.name)
            elif isinstance(st, ast.ImportFrom):
                for a in st.names:
                    if a.name != "*" and (a.asname or a.name) not in g:
                        _exec_nodes([ast.ImportFrom(module=st.module, names=[a], level=st.level)], g, filename)
                        res["imports"].append("from %s import %s" % ("." * st.level + (st.module or ""), a.name))
            elif isinstance(st, ast.Assign) and all(isinstance(t, ast.Name) for t in st.targets) and any(t.id not in g for t in st.targets):
                _exec_nodes([st], g, filename)
                res["added"].append(", ".join(t.id for t in st.targets))
        except Exception as e:
            res["skipped"].append({"n": "top-level statement line %d" % st.lineno, "why": "%s: %s" % (type(e).__name__, e)})
    # 2) literal constants changed since the previous version
    if base_tree is not None:
        old_map = _assign_map(base_tree)
        for st in tree.body:
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                name = st.targets[0].id
                if name in old_map and old_map[name] != ast.dump(st.value) and name in g:
                    try:
                        g[name] = ast.literal_eval(st.value)
                        res["consts"].append(name)
                    except ValueError:
                        res["restart"].append("%s = ... changed (not a literal)" % name)
        n_changed = len(_other_dumps(tree) ^ _other_dumps(base_tree))
        if n_changed:
            res["restart"].append("%d other top-level statement(s) changed or were added/removed" % n_changed)
    # 3) new top-level functions / classes
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and st.name not in g:
            try:
                _exec_nodes([st], g, filename)
                res["added"].append(st.name)
            except Exception as e:
                res["skipped"].append({"n": st.name, "why": "%s: %s" % (type(e).__name__, e)})
    # 4) new methods in existing classes
    for st in tree.body:
        cls = g.get(st.name) if isinstance(st, ast.ClassDef) else None
        if not isinstance(cls, type):
            continue
        for m in st.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name not in cls.__dict__:
                try:
                    holder = ast.ClassDef(name="_T", bases=[], keywords=[], body=[m], decorator_list=[])
                    ns = dict(g)
                    _exec_nodes([holder], ns, filename)
                    setattr(cls, m.name, ns["_T"].__dict__[m.name])
                    res["added"].append("%s.%s" % (st.name, m.name))
                except Exception as e:
                    res["skipped"].append({"n": "%s.%s" % (st.name, m.name), "why": "%s: %s" % (type(e).__name__, e)})
    # 5) swap code of existing functions
    for qual, new_list in codes.items():
        old_list = _live(mod, qual)
        if not old_list:
            continue
        if len(old_list) != len(new_list):
            res["skipped"].append({"n": qual, "why": "function layout changed (restart needed)"})
            continue
        nds = nodes.get(qual) or []
        for i, (old, new) in enumerate(zip(old_list, new_list)):
            changed = _sig(old.__code__) != _sig(new)
            try:
                old.__code__ = new
            except ValueError as e:
                res["skipped"].append({"n": qual, "why": "closure changed: %s (restart needed)" % e})
                continue
            if changed:
                res["patched"].append(qual)
                if i < len(nds):
                    try:
                        d, kd = _new_defaults(nds[i], g, "." in qual)
                        old.__defaults__, old.__kwdefaults__ = d, kd
                    except Exception:
                        pass
            else:
                res["same"] += 1


def _prepare(src: str, filename: str):
    tree = ast.parse(src, filename)
    code = compile(src, filename, "exec", dont_inherit=True)
    codes: Dict[str, List[types.CodeType]] = {}
    _collect(code, codes)
    return tree, codes, _nodes(tree)


async def apply(path: str, src: str, disk_before: Optional[str] = None) -> Dict[str, Any]:
    """Applies `src` (the new text of `path`) to the loaded module(s) of that file. disk_before = previous file content, used as the baseline
    when this is the first apply since the bot started and the file was not changed on disk since then."""
    res: Dict[str, Any] = {"loaded": False, "patched": [], "added": [], "consts": [], "imports": [], "same": 0, "skipped": [], "restart": []}
    mods = modules_for(path)
    if not mods:
        return res
    res["loaded"] = True
    res["modules"] = [m.__name__ for m in mods]
    loop = asyncio.get_running_loop()
    async with _lock:
        base_src = _last.get(path)
        if base_src is None:
            base_src = disk_before          # the caller passes it only if the file was not touched since the bot started
        base_tree = None
        if base_src is not None:
            try:
                base_tree = ast.parse(base_src)
            except SyntaxError:
                base_tree = None
        res["baseline"] = base_tree is not None
        for m in mods:
            fn = getattr(m, "__file__", path)
            tree, codes, nodes = await loop.run_in_executor(None, _prepare, src, fn)
            _apply_module(m, tree, codes, nodes, base_tree, fn, res)
            linecache.checkcache(fn)
        _last[path] = src
    res["patched"] = sorted(set(res["patched"]))
    return res


def remember(path: str, src: str) -> None:
    """Records `src` as the version the process is running (after a successful apply)."""
    _last[path] = src
