"""Hot reload without restarting the bot.

Compares each loaded project module's current source with the baseline snapshot (AST, per top-level
statement) and applies only what changed to the LIVE module (including __main__):
  - def / async def : code of the existing function object is patched in place, so every existing
                      reference (callbacks, dicts, `from x import f` elsewhere, discord handlers) uses new code
  - class           : methods patched in place / added on the existing class, so live instances see them
  - import          : re-executed
  - simple assigns  : re-executed only when that exact line changed (constants, prompts, config values)
Everything else (clients, singletons, caches, `if __name__` blocks, assigns calling unknown functions)
is never re-run, so runtime state is preserved. Things skipped are reported ("needs restart").
"""
import ast
import copy
import linecache
import importlib
import os
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = time.time()
_MISSING = object()
_SNAP = {}  # module name -> {"src", "defs", "stmts", "order", "imports"}
_SKIP_TOP = {"temp", "node_modules", "activity", "backup", "tests", "docker", "public", "temp_audio"}
_SKIP_ANY = {"runtime", "site-packages", "ttsapi", "__pycache__"}
_CLS_SKIP = {"__dict__", "__weakref__", "__module__", "__qualname__", "__doc__",
             "__firstlineno__", "__static_attributes__"}
_SAFE_CALLS = {
    "int", "float", "str", "bool", "len", "list", "dict", "set", "tuple", "frozenset", "range",
    "sorted", "min", "max", "abs", "round", "bytes", "getenv", "os.getenv", "os.environ.get",
    "os.path.join", "os.path.abspath", "os.path.dirname", "re.compile", "Path", "pathlib.Path",
    "dedent", "textwrap.dedent", "timedelta", "datetime.timedelta", "json.loads",
}


def _log(msg, lvl="INFO"):
    try:
        from . import console
        console.log(msg, lvl)
    except Exception:
        print(f"[hotreload] {msg}")


def _project_modules():
    """name -> (module, abs path) for project .py modules currently loaded."""
    root = os.path.normcase(ROOT)
    mf = getattr(sys.modules.get("__main__"), "__file__", None)
    main_file = os.path.normcase(os.path.abspath(mf)) if mf else None
    out = {}
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f or not f.endswith(".py"):
            continue
        p = os.path.normcase(os.path.abspath(f))
        if not p.startswith(root + os.sep):
            continue
        parts = os.path.relpath(p, root).split(os.sep)
        if parts[0] in _SKIP_TOP or any(x in _SKIP_ANY for x in parts):
            continue
        if p == main_file and name != "__main__":
            continue  # shadow copy created by `import main`
        out[name] = (mod, p)
    return out


def _dotted(n):
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        d = _dotted(n.value)
        return f"{d}.{n.attr}" if d else ""
    return ""


def _simple(value):
    """True if re-running this expression is harmless (no unknown calls / awaits)."""
    for c in ast.walk(value):
        if isinstance(c, (ast.Await, ast.Yield, ast.YieldFrom)):
            return False
        if isinstance(c, ast.Call) and _dotted(c.func) not in _SAFE_CALLS:
            return False
    return True


def _store_names(node):
    names = []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for t in targets:
        for n in ast.walk(t):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                names.append(n.id)
    return names


def _index(src):
    tree = ast.parse(src)
    idx = {"src": src, "defs": {}, "stmts": set(), "order": [], "imports": []}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.dump(n)
            idx["defs"][n.name] = d
            idx["order"].append(("def", n.name, n, d))
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)):
            if isinstance(n, ast.AnnAssign) and n.value is None:
                continue
            d = ast.dump(n)
            idx["stmts"].add(d)
            idx["order"].append(("stmt", None, n, d))
            if isinstance(n, ast.ImportFrom):
                idx["imports"].append((n.level, n.module, [(a.name, a.asname or a.name) for a in n.names]))
    return idx


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _exec_node(mod, node, path):
    code = compile(ast.Module(body=[node], type_ignores=[]), path, "exec")
    exec(code, mod.__dict__)


def _build(node, mod, path):
    n = copy.deepcopy(node)
    n.decorator_list = []
    loc = {}
    exec(compile(ast.Module(body=[n], type_ignores=[]), path, "exec"), mod.__dict__, loc)
    return loc[node.name]


def _fn_of(obj):
    for _ in range(4):
        if isinstance(obj, types.FunctionType):
            return obj
        obj = getattr(obj, "__func__", None) or getattr(obj, "callback", None) or getattr(obj, "__wrapped__", None)
        if obj is None:
            return None
    return None


def _patch(old, new):
    if len(old.__code__.co_freevars) != len(new.__code__.co_freevars):
        return False
    old.__code__ = new.__code__
    old.__defaults__ = new.__defaults__
    old.__kwdefaults__ = new.__kwdefaults__
    old.__annotations__ = new.__annotations__
    old.__doc__ = new.__doc__
    return True


def _fix_cells(fn, cls):
    """Point a method's __class__ cell (super()) at the live class instead of the scratch one."""
    if fn and fn.__closure__:
        for nm, cell in zip(fn.__code__.co_freevars, fn.__closure__):
            if nm == "__class__":
                cell.cell_contents = cls


def _merge_class(old, new):
    for k, v in vars(new).items():
        if k in _CLS_SKIP:
            continue
        cur = vars(old).get(k, _MISSING)
        fv = _fn_of(v)
        if cur is not _MISSING:
            fc = _fn_of(cur)
            if fc and fv and type(cur) is type(v) and _patch(fc, fv):
                continue
            if isinstance(cur, (list, dict, set)):
                continue
        _fix_cells(fv, old)
        setattr(old, k, v)


def _apply_def(mod, node, path):
    new = _build(node, mod, path)
    old = mod.__dict__.get(node.name, _MISSING)
    if old is _MISSING:
        _exec_node(mod, node, path)
    elif isinstance(old, type) and isinstance(new, type):
        _merge_class(old, new)
    else:
        fo, fn = _fn_of(old), _fn_of(new)
        if not (fo and fn and _patch(fo, fn)):
            _exec_node(mod, node, path)


def _reload_one(name, mod, path, changed, notes):
    src = _read(path)
    base = _SNAP.get(name)
    if base is None:
        if os.path.getmtime(path) > START:
            notes.append(f"{name}: no baseline (loaded after start) - restart needed")
        _SNAP[name] = _index(src)
        return 0
    if src == base["src"]:
        return 0
    new = _index(src)  # SyntaxError -> caller reports, baseline kept
    ok, n = True, 0
    for kind, dname, node, dump in new["order"]:
        if kind == "def":
            if base["defs"].get(dname) == dump:
                continue
        elif dump in base["stmts"]:
            continue
        try:
            if kind == "def":
                _apply_def(mod, node, path)
                changed.setdefault(name, []).append(dname)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if not _simple(node.value):
                    notes.append(f"{name}:{node.lineno} assignment calls unknown code - restart needed")
                    continue
                _exec_node(mod, node, path)
                changed.setdefault(name, []).extend(_store_names(node))
            else:
                _exec_node(mod, node, path)
            n += 1
        except Exception as e:
            ok = False
            notes.append(f"{name}:{getattr(node, 'lineno', '?')} FAILED {type(e).__name__}: {e}")
    if ok:
        _SNAP[name] = new
    return n


def _propagate(mods, changed):
    """Rebind changed names in modules that did `from X import name` / `import *`."""
    for imp_name, (imp_mod, _p) in mods.items():
        for level, modname, names in (_SNAP.get(imp_name) or {}).get("imports", []):
            if level:
                base = (imp_mod.__package__ or "").rsplit(".", level - 1)[0]
                modname = f"{base}.{modname}" if modname else base
            ch = changed.get(modname)
            src = mods.get(modname, (None,))[0]
            if not ch or src is None:
                continue
            for nm, alias in names:
                targets = [(n, n) for n in ch if not n.startswith("_")] if nm == "*" else ([(nm, alias)] if nm in ch else [])
                for sn, dn in targets:
                    if sn in src.__dict__:
                        imp_mod.__dict__[dn] = src.__dict__[sn]


def snapshot(only_new=True):
    for name, (_m, path) in _project_modules().items():
        if only_new and name in _SNAP:
            continue
        try:
            _SNAP[name] = _index(_read(path))
        except Exception:
            pass


def reload_all():
    """Apply source changes to live modules. Returns (summary, notes)."""
    importlib.invalidate_caches()
    linecache.checkcache()
    mods = _project_modules()
    order = sorted(mods, key=lambda n: (n == "__main__", n != "config", n))
    changed, notes, total = {}, [], 0
    for name in order:
        mod, path = mods[name]
        try:
            total += _reload_one(name, mod, path, changed, notes)
        except Exception as e:
            notes.append(f"{name}: FAILED {type(e).__name__}: {e}")
    _propagate(mods, changed)
    return f"{total} change(s) applied in {len(changed)} module(s): {', '.join(sorted(changed)) or '-'}", notes


for _t in (30, 120):
    _tm = threading.Timer(_t, snapshot)
    _tm.daemon = True
    _tm.start()
snapshot()
