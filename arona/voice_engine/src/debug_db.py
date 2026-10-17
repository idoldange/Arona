"""Database page under /debug/db: spreadsheet-style editor for the SQLite and JSON data files of the bot.

Sources: every *.db / *.sqlite / *.sqlite3 / *.json under ./database plus the EXTRA globs (relative to the project root).
Grid = Tabulator (vendored in ../static, no CDN) with server-side paging / sorting / search. A click on a cell opens its FULL content
in the panel below the grid (type toggle + Commit); double-click edits short cells inline. SQL identifiers are checked against
sqlite_master / PRAGMA table_info and values are bound parameters. JSON files are shown as a table (list of objects, dict of
objects, key/value) and rewritten atomically. The first write to a file per run makes a backup in backup/dbedit/.
Same login as the rest of /debug (auth_middleware).
"""
import asyncio
import glob
import json
import math
import os
import shutil
import sqlite3
import threading
import time

from aiohttp import web

from console import console

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_DIR = os.path.join(ROOT, "database")
EXTRA = ["games/*.json", "key_state.json"]       # data files outside ./database
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
BACKUP_DIR = os.path.join(ROOT, "backup", "dbedit")
PREVIEW = 300                                     # grid cells longer than this are cut with "..." (full text: click the cell)
BLOB_MAX = 1 << 20                                # bytes of a blob loaded into the panel (bigger = shown partially, read-only)
_SQL = (".db", ".sqlite", ".sqlite3")
_NUM = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")
_backed_up: set = set()
_wlock = threading.Lock()


# ───────────────────────────── sources ─────────────────────────────
def _sources() -> dict:
    out = {}

    def add(p):
        e, k = os.path.splitext(p)[1].lower(), os.path.relpath(p, ROOT).replace("\\", "/")
        if e in _SQL:
            out[k] = (p, "sqlite")
        elif e == ".json":
            out[k] = (p, "json")

    for dp, dn, fs in os.walk(DB_DIR):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fs:
            add(os.path.join(dp, f))
    for pat in EXTRA:
        for p in glob.glob(os.path.join(ROOT, pat)):
            add(p)
    return out


def _src(key: str):
    s = _sources().get(key)
    if not s:
        raise ValueError("unknown file")
    return s


def _q(n: str) -> str:
    return '"' + n.replace('"', '""') + '"'


def _backup_file(key: str, path: str, conn=None) -> None:
    if key in _backed_up:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, "%s.%s.bak" % (key.replace("/", "_"), time.strftime("%Y%m%d-%H%M%S")))
    if conn is not None:
        d = sqlite3.connect(dst)
        try:
            conn.backup(d)
        finally:
            d.close()
    else:
        shutil.copy2(path, dst)
    _backed_up.add(key)


def _prev(v):
    """(value for the grid, was it cut?)"""
    if isinstance(v, (bytes, bytearray)):
        return "<blob %d B>" % len(v), True
    if isinstance(v, str) and len(v) > PREVIEW:
        return v[:PREVIEW] + "…", True
    return v, False


def _page(rows: list, a: dict) -> dict:
    return {"data": rows, "last_page": max(1, math.ceil(a["_total"] / a["size"])), "total": a["_total"]}


def _list(_a=None):
    out = []
    for key, (p, kind) in sorted(_sources().items()):
        e = {"name": key, "kind": kind, "size": os.path.getsize(p), "tables": ["(root)"] if kind == "json" else []}
        if kind == "sqlite":
            try:
                c = sqlite3.connect(p, timeout=5)
                c.execute("pragma query_only=1")
                e["tables"] = [r[0] for r in c.execute("select name from sqlite_master where type in ('table','view') and name not like 'sqlite_%' order by name")]
                c.close()
            except sqlite3.Error as ex:
                e["error"] = str(ex)
        out.append(e)
    return {"dbs": out}


# ───────────────────────────── sqlite ─────────────────────────────
def _meta(c, table: str):
    r = c.execute("select type from sqlite_master where name=? and type in ('table','view')", (table,)).fetchone()
    if not r:
        raise ValueError("unknown table")
    cols = [{"name": x[1], "type": x[2] or "", "notnull": bool(x[3]), "pk": x[5]} for x in c.execute("pragma table_info(%s)" % _q(table))]
    mode = None
    if r[0] == "table":
        try:
            c.execute("select rowid from %s limit 0" % _q(table))
            mode = "rowid"
        except sqlite3.Error:
            pk = [k["name"] for k in sorted((k for k in cols if k["pk"]), key=lambda k: k["pk"])]
            mode = pk or None
    return r[0], cols, mode


def _why(kind, mode):
    return "view" if kind != "table" else (None if mode else "no rowid or primary key")


def _where(mode, rid):
    if mode == "rowid":
        return "rowid=?", [int(rid)]
    if not isinstance(rid, list) or len(rid) != len(mode):
        raise ValueError("bad row id")
    return " and ".join("%s=?" % _q(n) for n in mode), [bytes.fromhex(v["$hex"]) if isinstance(v, dict) and "$hex" in v else v for v in rid]


def _conv(col_type: str, v, vt: str):
    if vt == "null":
        return None
    if vt == "blob":
        try:
            return bytes.fromhex("".join(str(v).split()))
        except ValueError:
            raise ValueError("not valid hex")
    if vt == "num":
        s = str(v).strip()
        for f in (int, float):
            try:
                return f(s)
            except ValueError:
                pass
        raise ValueError("not a number")
    if vt == "text":
        return str(v)
    if v is None:                                  # inline edit ("auto"): follow the column affinity
        return None
    if any(k in col_type.upper() for k in _NUM):
        if v == "":
            return None
        if isinstance(v, str):
            for f in (int, float):
                try:
                    return f(v)
                except ValueError:
                    pass
    return v


def _sql_cols(a):
    c = sqlite3.connect(_src(a["db"])[0], timeout=5)
    kind, cols, mode = _meta(c, a["table"])
    c.close()
    return {"cols": cols, "why": _why(kind, mode), "kind": "sqlite", "types": ["text", "num", "blob", "null"], "mode": mode if mode == "rowid" else "pk"}


def _sql_rows(a):
    c = sqlite3.connect(_src(a["db"])[0], timeout=5)
    c.execute("pragma query_only=1")
    kind, cols, mode = _meta(c, a["table"])
    names, t, where, args = [k["name"] for k in cols], _q(a["table"]), "", []
    if a["q"]:
        where = " where " + " or ".join("cast(%s as text) like ?" % _q(n) for n in names)
        args = ["%" + a["q"] + "%"] * len(names)
    if a["sort"] in names:
        order = " order by %s %s" % (_q(a["sort"]), "desc" if a["dir"] == "desc" else "asc")
    else:
        order = " order by " + ("rowid" if mode == "rowid" else ", ".join(_q(n) for n in mode)) if mode else ""
    total = c.execute("select count(*) from %s%s" % (t, where), args).fetchone()[0]
    cur = c.execute("select %s* from %s%s%s limit ? offset ?" % ("rowid as __rid, " if mode == "rowid" else "", t, where, order),
                    args + [a["size"], (a["page"] - 1) * a["size"]])
    heads, rows = [d[0] for d in cur.description], []
    for r in cur.fetchall():
        row, tr = {}, []
        for h, v in zip(heads, r):
            row[h], cut = _prev(v)
            if cut:
                tr.append(h)
        if isinstance(mode, list):
            row["__rid"] = [{"$hex": v.hex()} if isinstance(v, bytes) else v for v in (r[heads.index(n)] for n in mode)]
        row["__tr"] = tr
        rows.append(row)
    c.close()
    a["_total"] = total
    return {**_page(rows, a), "why": _why(kind, mode)}


def _sql_cell(a):
    c = sqlite3.connect(_src(a["db"])[0], timeout=5)
    c.execute("pragma query_only=1")
    kind, cols, mode = _meta(c, a["table"])
    if a["col"] not in [k["name"] for k in cols]:
        raise ValueError("unknown column")
    w, args = _where(mode, a["rid"]) if mode else (None, None)
    if w is None:
        raise ValueError("view: no row id")
    r = c.execute("select %s from %s where %s" % (_q(a["col"]), _q(a["table"]), w), args).fetchone()
    c.close()
    if r is None:
        raise ValueError("row not found")
    v = r[0]
    if v is None:
        return {"value": "", "kind": "null"}
    if isinstance(v, bytes):
        return {"value": v[:BLOB_MAX].hex(), "kind": "blob", "partial": len(v) > BLOB_MAX, "size": len(v)}
    if isinstance(v, (int, float)):
        return {"value": str(v), "kind": "num"}
    return {"value": v, "kind": "text"}


def _sql_write(a, op):
    p = _src(a["db"])[0]
    c = sqlite3.connect(p, timeout=10)
    try:
        kind, cols, mode = _meta(c, a["table"])
        if _why(kind, mode):
            raise ValueError("read-only: " + _why(kind, mode))
        _backup_file(a["db"], p, c)
        t = _q(a["table"])
        if op == "insert":
            c.execute("insert into %s default values" % t)
        else:
            w, args = _where(mode, a.get("rid"))
            if op == "delete":
                cur = c.execute("delete from %s where %s" % (t, w), args)
            else:
                col = next((k for k in cols if k["name"] == a.get("col")), None)
                if col is None:
                    raise ValueError("unknown column")
                cur = c.execute("update %s set %s=? where %s" % (t, _q(col["name"]), w), [_conv(col["type"], a.get("value"), a.get("vtype", "auto"))] + args)
            if cur.rowcount != 1:
                raise ValueError("row not found (changed by the bot meanwhile?)")
        c.commit()
        return {"ok": True}
    finally:
        c.close()


# ───────────────────────────── json ─────────────────────────────
def _jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _jsave(p, data):
    tmp = p + ".dbedit.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _union(items):
    seen = {}
    for x in items:
        for k in x:
            seen.setdefault(k, 1)
    return list(seen)


def _jmodel(data):
    """-> (mode, columns, [(row id, {col: value})])"""
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            return "list", _union(data), list(enumerate(data))
        return "scalars", ["value"], [(i, {"value": x}) for i, x in enumerate(data)]
    if isinstance(data, dict):
        if data and all(isinstance(v, dict) for v in data.values()):
            return "dictrows", ["_key"] + _union(data.values()), [(k, {"_key": k, **v}) for k, v in data.items()]
        return "kv", ["_key", "value"], [(k, {"_key": k, "value": v}) for k, v in data.items()]
    return "scalar", ["value"], [(0, {"value": data})]


def _jshow(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def _jkind(v):
    return "null" if v is None else ("text" if isinstance(v, str) else "json")


def _j_cols(a):
    mode, cols, _ = _jmodel(_jload(_src(a["db"])[0]))
    return {"cols": [{"name": c, "type": "", "notnull": False, "pk": 0} for c in cols], "why": None, "kind": "json", "types": ["text", "json", "null"], "mode": mode}


def _j_rows(a):
    mode, cols, rows = _jmodel(_jload(_src(a["db"])[0]))
    if a["q"]:
        ql = a["q"].lower()
        rows = [r for r in rows if ql in json.dumps(r[1], ensure_ascii=False).lower()]
    if a["sort"] in cols:
        f = a["sort"]

        def key(r):
            v = r[1].get(f)
            return (v is None, 0, v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (v is None, 1, str(v))
        rows = sorted(rows, key=key, reverse=a["dir"] == "desc")
    a["_total"] = len(rows)
    out = []
    for rid, d in rows[(a["page"] - 1) * a["size"]: a["page"] * a["size"]]:
        row, tr = {}, []
        for c in cols:
            row[c], cut = _prev(_jshow(d.get(c)))
            if cut:
                tr.append(c)
        row["__rid"], row["__tr"] = rid, tr
        out.append(row)
    return {**_page(out, a), "why": None}


def _jcell_value(data, mode, rid, col):
    if mode == "list":
        return data[int(rid)].get(col)
    if mode == "dictrows":
        return rid if col == "_key" else data[rid].get(col)
    if mode == "kv":
        return rid if col == "_key" else data[rid]
    if mode == "scalars":
        return data[int(rid)]
    return data


def _j_cell(a):
    data = _jload(_src(a["db"])[0])
    mode = _jmodel(data)[0]
    try:
        v = _jcell_value(data, mode, a["rid"], a["col"])
    except (KeyError, IndexError):
        raise ValueError("row not found")
    k = _jkind(v)
    return {"value": "" if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2)), "kind": k}


def _jconv(cur, v, vt):
    if vt == "null":
        return None
    if vt == "text":
        return str(v)
    if vt == "json":
        try:
            return json.loads(v)
        except ValueError as e:
            raise ValueError("invalid JSON: %s" % e)
    if v is None:
        return None
    if isinstance(cur, str):
        return str(v)
    try:
        return json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return str(v)


def _j_write(a, op):
    p = _src(a["db"])[0]
    with _wlock:
        data = _jload(p)
        mode, cols, _ = _jmodel(data)
        rid = a.get("rid")
        if op == "insert":
            if mode == "list":
                data.append({})
            elif mode == "scalars":
                data.append(None)
            elif mode in ("dictrows", "kv"):
                n = 1
                while "new_%d" % n in data:
                    n += 1
                data["new_%d" % n] = {} if mode == "dictrows" else None
            else:
                raise ValueError("cannot add a row to a single value")
        elif op == "delete":
            try:
                if mode in ("list", "scalars"):
                    data.pop(int(rid))
                elif mode in ("dictrows", "kv"):
                    data.pop(rid)
                else:
                    raise ValueError("cannot delete the root value")
            except (KeyError, IndexError):
                raise ValueError("row not found")
        else:
            col, vt = a.get("col"), a.get("vtype", "auto")
            try:
                if mode in ("dictrows", "kv") and col == "_key":
                    nk = str(a.get("value"))
                    if nk != rid and nk in data:
                        raise ValueError("key already exists")
                    new = {(nk if k == rid else k): v for k, v in data.items()}
                    data.clear()
                    data.update(new)
                elif mode == "scalar":
                    data = _jconv(data, a.get("value"), vt)
                elif mode == "scalars":
                    data[int(rid)] = _jconv(data[int(rid)], a.get("value"), vt)
                elif mode == "kv":
                    data[rid] = _jconv(data[rid], a.get("value"), vt)
                else:
                    box = data[int(rid)] if mode == "list" else data[rid]
                    box[col] = _jconv(box.get(col), a.get("value"), vt)
            except (KeyError, IndexError):
                raise ValueError("row not found")
        _backup_file(a["db"], p)
        _jsave(p, data)
    return {"ok": True}


# ───────────────────────────── http ─────────────────────────────
def _dispatch(a, sql_fn, json_fn, *extra):
    return (sql_fn if _src(a["db"])[1] == "sqlite" else json_fn)(a, *extra)


async def _call(fn, *args) -> web.Response:
    try:
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, fn, *args), headers={"Cache-Control": "no-store"})
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as e:
        return web.json_response({"error": str(e)}, status=400)


def _qi(request: web.Request, key: str, default: int, lo: int, hi: int) -> int:
    try:
        return min(hi, max(lo, int(request.query.get(key, default))))
    except ValueError:
        return default


def _args(request: web.Request) -> dict:
    q = request.query
    return {"db": q.get("db", ""), "table": q.get("table", ""), "q": q.get("q", ""), "sort": q.get("sort[0][field]", ""), "dir": q.get("sort[0][dir]", "asc"),
            "page": _qi(request, "page", 1, 1, 10 ** 9), "size": _qi(request, "size", 100, 1, 2000), "col": q.get("col", "")}


async def api_list(request):
    return await _call(_list)


async def api_cols(request):
    return await _call(lambda a: _dispatch(a, _sql_cols, _j_cols), _args(request))


async def api_rows(request):
    return await _call(lambda a: _dispatch(a, _sql_rows, _j_rows), _args(request))


async def api_cell(request):
    a = _args(request)
    try:
        a["rid"] = json.loads(request.query.get("rid", "null"))
    except ValueError:
        return web.json_response({"error": "bad row id"}, status=400)
    return await _call(lambda a: _dispatch(a, _sql_cell, _j_cell), a)


def _post(op: str):
    async def h(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "bad request"}, status=400)
        try:
            a = await request.json()
            assert isinstance(a, dict)
        except (ValueError, AssertionError):
            return web.json_response({"error": "bad request"}, status=400)
        console.log("[DebugWeb] db edit: %s %s %s%s" % (op, a.get("db"), a.get("table"), " rid=%s" % a.get("rid") if "rid" in a else ""), "INFO")
        return await _call(lambda a: _dispatch(a, _sql_write, _j_write, op), a)
    return h


async def static(request: web.Request) -> web.StreamResponse:
    n = request.match_info["name"]
    if n not in ("tabulator.min.js", "tabulator_midnight.min.css"):
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(STATIC, n), headers={"Cache-Control": "public, max-age=86400"})


BODY = r"""
<link rel="stylesheet" href="/debug/static/tabulator_midnight.min.css">
<style>
main{max-width:none;padding:0}
#app{display:flex;height:calc(100vh - 49px)}
#side{width:290px;min-width:180px;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column}
#flt{margin:8px}
#tree{overflow:auto;flex:1;padding:0 6px 10px;font-size:13px}
#tree summary{cursor:pointer;color:var(--muted);padding:3px 4px}
.tf{padding:4px 6px 1px;display:flex;gap:6px;color:var(--text)}.tf .sz{margin-left:auto;color:var(--muted);font-size:11px}
.tf.jf{cursor:pointer;border-radius:5px}.tf.jf:hover,.tt:hover{background:#2b2d31}
.tt{padding:2px 6px 2px 22px;cursor:pointer;border-radius:5px;color:#b5bac1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.on{background:var(--accent)!important;color:#fff!important}
#main{flex:1;min-width:0;display:flex;flex-direction:column}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 12px;background:var(--panel);border-bottom:1px solid var(--line)}
#q{width:220px}#msg{margin-left:auto;font-size:13px}#cur{font-weight:600}
#gw{flex:1;min-height:0}
#grid{border:0;background:var(--bg);font-size:13px}
#grid .tabulator-header,#grid .tabulator-header .tabulator-col{background:#1a1b1e;color:var(--muted)}
#grid .tabulator-row{background:var(--bg);border-color:var(--line)}#grid .tabulator-row.tabulator-row-even{background:#16171a}
#grid .tabulator-row:hover{background:#23252a}#grid .tabulator-cell{border-color:var(--line)}
#grid .tabulator-cell.selc{outline:2px solid var(--accent);outline-offset:-2px}
.nul{opacity:.45}.del{color:#f23f43;cursor:pointer}
#panel{height:250px;border-top:1px solid var(--line);background:var(--panel);display:flex;flex-direction:column;padding:8px 12px;gap:6px}
#sql{font:12px ui-monospace,Consolas,monospace;color:#8ea1ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#val{flex:1;width:100%;resize:none;min-height:0}
.row2{display:flex;gap:8px;align-items:center}
.seg{display:flex}.seg button{background:#2b2d31;color:var(--muted);border-radius:0;padding:5px 10px}.seg button:first-child{border-radius:6px 0 0 6px}.seg button:last-child{border-radius:0 6px 6px 0}
.seg button.on{background:#3b3e45!important;color:#fff}
</style>
<div id="app">
  <div id="side"><input id="flt" placeholder="Filter files / tables..."><div id="tree"></div></div>
  <div id="main">
    <div class="bar"><span id="cur" class="muted">Pick a table on the left</span><input id="q" placeholder="Search all columns...">
      <button id="add" class="alt">+ Row</button><button id="rl" class="alt">Reload</button><span id="info" class="muted"></span><span id="msg"></span></div>
    <div id="gw"><div id="grid"></div></div>
    <div id="panel">
      <div id="sql" class="muted">Click a cell to see and edit its full content here (double-click edits short cells in place).</div>
      <textarea id="val" spellcheck="false" disabled></textarea>
      <div class="row2"><div class="seg" id="seg"></div><button id="com" disabled>Commit <small>Ctrl+Enter</small></button><button id="rev" class="alt" disabled>Revert</button><span id="note" class="muted"></span></div>
    </div>
  </div>
</div>
<script src="/debug/static/tabulator.min.js"></script>"""

SCRIPT = r"""
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const api = (p, o) => fetch('/debug/api/db/' + p, o).then(r => r.json());
const post = (p, b) => api(p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
const num = t => /INT|REAL|FLOA|DOUB|NUM|DEC/i.test(t);
const fmt = b => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
const openDirs = new Set(['database']);
let srcs = [], grid = null, cur = {}, why = null, meta = {}, sel = null, selEl = null, vt = 'text', ovt = 'text', orig = '', partial = false;
function say(m, bad) { const e = $('msg'); e.textContent = m; e.style.color = bad ? '#f23f43' : '#23a55a'; }

function renderTree() {
  const f = $('flt').value.toLowerCase(), root = {d: {}, f: []};
  srcs.filter(s => !f || s.name.toLowerCase().includes(f) || s.tables.some(t => t.toLowerCase().includes(f))).forEach(s => {
    const p = s.name.split('/'); p.pop(); let o = root; p.forEach(x => o = (o.d[x] = o.d[x] || {d: {}, f: []})); o.f.push(s); });
  const walk = (o, into, path) => {
    Object.keys(o.d).sort().forEach(k => { const det = document.createElement('details'), sm = document.createElement('summary');
      const dp = path + k; sm.textContent = k; det.appendChild(sm); det.open = !!f || openDirs.has(dp); sm.onclick = () => setTimeout(() => det.open ? openDirs.add(dp) : openDirs.delete(dp));
      walk(o.d[k], det, dp + '/'); into.appendChild(det); });
    o.f.forEach(s => {
      const row = document.createElement('div'), nm = document.createElement('span'), sz = document.createElement('span');
      row.className = 'tf' + (s.kind === 'json' ? ' jf' : ''); nm.textContent = (s.kind === 'json' ? '{ } ' : '') + s.name.split('/').pop(); sz.className = 'sz'; sz.textContent = fmt(s.size);
      row.append(nm, sz); into.appendChild(row);
      if (s.kind === 'json') { if (cur.db === s.name) row.classList.add('on'); row.onclick = () => openTable(s.name, '(root)'); }
      else (s.error ? [] : s.tables).filter(t => !f || s.name.toLowerCase().includes(f) || t.toLowerCase().includes(f)).forEach(t => {
        const e = document.createElement('div'); e.className = 'tt' + (cur.db === s.name && cur.table === t ? ' on' : ''); e.textContent = t; e.title = t; e.onclick = () => openTable(s.name, t); into.appendChild(e); });
    });
  };
  const box = $('tree'); box.replaceChildren(); walk(root, box, '');
}
function openTable(db, table) {
  cur = {db, table}; db.split('/').slice(0, -1).reduce((p, x) => { p = p ? p + '/' + x : x; openDirs.add(p); return p; }, ''); try { localStorage.setItem('dbpage:last', JSON.stringify(cur)); } catch (e) {}
  $('cur').textContent = db + (table === '(root)' ? '' : '  /  ' + table); renderTree(); load();
}
function clearSel() { sel = null; if (selEl) selEl.classList.remove('selc'); selEl = null; $('val').value = ''; $('val').disabled = true; $('seg').replaceChildren();
  $('sql').textContent = 'Click a cell to see and edit its full content here (double-click edits short cells in place).'; $('note').textContent = ''; dirty(); }
function dirty() { const ch = sel && !why && !partial && ($('val').value !== orig || vt !== ovt); $('com').disabled = !ch; $('rev').disabled = !ch; }
function setType(t) { vt = t; [...$('seg').children].forEach(b => b.classList.toggle('on', b.dataset.t === t)); $('val').disabled = !sel || !!why || partial || t === 'null'; dirty(); }
async function pick(cell) {
  const d = cell.getData(), col = cell.getField(); if (selEl) selEl.classList.remove('selc'); selEl = cell.getElement(); selEl.classList.add('selc');
  sel = {rid: d.__rid, col};
  const rid = JSON.stringify(d.__rid), t = '`' + cur.table + '`';
  $('sql').textContent = meta.kind === 'sqlite' ? 'UPDATE ' + t + ' SET `' + col + '` = ? WHERE ' + (meta.mode === 'rowid' ? 'rowid' : 'pk') + ' = ' + rid : cur.db + '  ·  ' + (meta.mode === 'list' || meta.mode === 'scalars' ? '[' + rid + ']' : '[' + rid + ']') + '  ·  ' + col;
  $('val').value = 'loading...'; $('val').disabled = true;
  const r = await api('cell?' + new URLSearchParams({...cur, rid, col}));
  if (!sel || sel.col !== col || JSON.stringify(sel.rid) !== rid) return;
  if (r.error) { $('val').value = ''; return say(r.error, 1); }
  partial = !!r.partial; orig = r.value; ovt = r.kind; $('val').value = r.value;
  $('seg').replaceChildren(...meta.types.map(x => { const b = document.createElement('button'); b.textContent = x === 'num' ? 'NUMERIC' : x.toUpperCase(); b.dataset.t = x; b.onclick = () => setType(x); return b; }));
  $('note').textContent = (r.kind === 'blob' ? 'hex, ' + r.size + ' bytes' + (partial ? ' - first 1 MB only, read-only' : '') : (r.value.length + ' chars'));
  setType(r.kind);
}
async function commit() {
  if (!sel) return; const r = await post('update', {...cur, rid: sel.rid, col: sel.col, value: $('val').value, vtype: vt});
  if (r.error) return say(r.error, 1);
  say('saved'); const keep = meta.kind === 'json' && sel.col === '_key' ? null : sel; orig = $('val').value; ovt = vt;
  await grid.replaceData(); if (keep) dirty(); else clearSel();
}
async function load() {
  if (!cur.table) return;
  const c = await api('cols?' + new URLSearchParams(cur)); if (c.error) return say(c.error, 1);
  meta = c; why = c.why; $('add').disabled = !!why; clearSel();
  const cols = c.cols.map(k => ({
    title: k.name, field: k.name, headerTooltip: k.type + (k.pk ? ' (pk)' : '') + (k.notnull ? ' not null' : ''), maxWidth: 520,
    editor: why ? false : (num(k.type) ? 'number' : 'textarea'), editorParams: num(k.type) ? {selectContents: true} : {shiftEnterSubmit: true},
    editable: cell => !((cell.getData().__tr || []).includes(cell.getField())),
    formatter: cell => { const v = cell.getValue(); return v === null || v === undefined ? '<i class="nul">NULL</i>' : esc(v); },
    cellClick: (e, cell) => pick(cell),
  }));
  cols.unshift({title: '#', field: '__rid', width: 70, headerSort: false, formatter: cell => '<span class="muted">' + esc(JSON.stringify(cell.getValue()) ?? '').replace(/^&quot;|&quot;$/g, '') + '</span>'});
  if (!why) cols.unshift({title: '', width: 36, hozAlign: 'center', headerSort: false, formatter: () => '<span class="del">&#10005;</span>',
    cellClick: async (e, cell) => { if (!confirm('Delete this row?')) return; const r = await post('delete', {...cur, rid: cell.getData().__rid});
      if (r.error) say(r.error, 1); else { say('deleted'); clearSel(); grid.replaceData(); } }});
  if (grid) grid.destroy();
  grid = new Tabulator('#grid', {
    height: $('gw').clientHeight, layout: 'fitData', columns: cols, editTriggerEvent: 'dblclick', ajaxURL: '/debug/api/db/rows', ajaxParams: () => ({...cur, q: $('q').value}),
    pagination: true, paginationMode: 'remote', sortMode: 'remote', paginationSize: 100, paginationSizeSelector: [50, 100, 500, 1000], movableColumns: true,
    ajaxResponse: (u, p, r) => { $('info').textContent = (r.total ?? 0).toLocaleString() + ' rows' + (r.why ? ' · read-only (' + r.why + ')' : ''); return r; },
    ajaxError: () => say('load failed', 1),
  });
  grid.on('cellEdited', async cell => {
    const r = await post('update', {...cur, rid: cell.getData().__rid, col: cell.getField(), value: cell.getValue(), vtype: 'auto'});
    if (r.error) { cell.restoreOldValue(); say(r.error, 1); } else { say('saved'); if (sel && sel.col === cell.getField()) pick(cell); }
  });
}
(async () => {
  srcs = (await api('list')).dbs || [];
  let last = null; try { last = JSON.parse(localStorage.getItem('dbpage:last')); } catch (e) {}
  const ok = last && srcs.some(s => s.name === last.db && s.tables.includes(last.table));
  if (ok) openTable(last.db, last.table); else { renderTree(); const s = srcs.find(x => x.tables.length); if (s) openTable(s.name, s.tables[0]); }
})();
$('flt').oninput = renderTree; $('rl').onclick = () => grid && grid.replaceData();
let qt; $('q').oninput = () => { clearTimeout(qt); qt = setTimeout(() => grid && grid.setData(), 350); };
$('add').onclick = async () => { const r = await post('insert', cur); if (r.error) say(r.error, 1); else { say('row added'); grid.setData(); } };
$('val').oninput = dirty; $('com').onclick = commit;
$('val').onkeydown = e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (!$('com').disabled) commit(); } };
$('rev').onclick = () => { $('val').value = orig; setType(ovt); };
window.addEventListener('resize', () => grid && grid.setHeight($('gw').clientHeight));
"""


async def page_handler(request: web.Request) -> web.Response:
    from arona.voice_engine.src import debug_panel as dp      # lazy: debug_panel imports this module at add_routes time
    return dp.page("Database", "/debug/db", BODY, SCRIPT)


def add_routes(app: web.Application) -> None:
    app.add_routes([
        web.get("/debug/db", page_handler), web.get("/debug/static/{name}", static),
        web.get("/debug/api/db/list", api_list), web.get("/debug/api/db/cols", api_cols), web.get("/debug/api/db/rows", api_rows), web.get("/debug/api/db/cell", api_cell),
        web.post("/debug/api/db/update", _post("update")), web.post("/debug/api/db/insert", _post("insert")), web.post("/debug/api/db/delete", _post("delete")),
    ])
