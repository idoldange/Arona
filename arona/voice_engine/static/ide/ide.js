/* Arona IDE: a VS Code-like page (Monaco + Pyright over WebSocket + file API + hot reload). Vanilla JS, no build step. */
(() => {
'use strict';
const CFG = window.IDE, MB = '/debug/static/monaco/';
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const enc = encodeURIComponent, sleep = ms => new Promise(r => setTimeout(r, ms));
const LS = {get(k, d) { try { const v = localStorage.getItem('ide.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
            set(k, v) { try { localStorage.setItem('ide.' + k, JSON.stringify(v)); } catch (e) {} }};
const S = {tabs: [], active: null, expanded: new Set(LS.get('exp', [])), mode: LS.get('mode', 'both'), view: LS.get('view', 'explorer'),
           ptab: LS.get('ptab', 'console'), sre: false, scase: false};
const MODES = {both: 'Save + hot reload', disk: 'Save to disk only', ram: 'Hot reload only (RAM)'};
const HEAD = 'from main import *  # noqa  (hidden: gives Pyright the bot globals)\n';
const ci = (n, extra = '') => '<span class="codicon codicon-' + n + '" ' + extra + '></span>';

window.addEventListener('unhandledrejection', e => { if (e.reason && (e.reason.message === 'Canceled' || e.reason === 'closed')) e.preventDefault(); });
$('#app').innerHTML = `
<div id="act">
  <div class="ab" data-v="explorer" title="Explorer (Ctrl+Shift+E)">${ci('files')}</div>
  <div class="ab" data-v="search" title="Search (Ctrl+Shift+F)">${ci('search')}</div>
  <div class="ab" data-v="vars" title="Bot variables">${ci('symbol-variable')}</div>
  <div class="sp"></div>
  <div class="ab" id="ab-panel" title="Toggle panel (Ctrl+J)">${ci('layout-panel')}</div>
</div>
<div id="side">
  <div class="sv" id="sv-explorer"><div class="sh"><span class="t" id="rootname"></span>
    <span class="ic" id="b-nf" title="New File">${ci('new-file')}</span><span class="ic" id="b-nd" title="New Folder">${ci('new-folder')}</span>
    <span class="ic" id="b-rf" title="Refresh">${ci('refresh')}</span><span class="ic" id="b-ca" title="Collapse all">${ci('collapse-all')}</span></div>
    <div id="tree"></div></div>
  <div class="sv" id="sv-search"><div class="sh"><span class="t">Search</span></div>
    <div class="sv-pad"><input class="in" id="sq" placeholder="Search in project (Enter)">
      <div class="sopt"><label><input type="checkbox" id="s-case"> Aa</label><label><input type="checkbox" id="s-re"> .*</label><span id="s-n" style="margin-left:auto"></span></div></div>
    <div class="sres" id="sres"></div></div>
  <div class="sv" id="sv-vars"><div class="sh"><span class="t">Bot variables</span><span class="ic" id="b-rv" title="Refresh">${ci('refresh')}</span></div>
    <div class="sv-pad"><input class="in" id="vq" placeholder="filter"></div><div class="sres" id="vlist"></div></div>
</div>
<div class="gut" id="g1"></div>
<div id="mid">
  <div id="tabsrow"><div id="tabs"></div><div id="tabact">
    <span class="ic" id="b-hot" title="Hot reload this file now, RAM only, nothing written (Alt+R)">${ci('zap')}</span>
    <span class="ic" id="b-save" title="Save (Ctrl+S)">${ci('save')}</span></div></div>
  <div id="crumbs"></div>
  <div id="edwrap"><div id="ed"></div>
    <div id="welcome"><h1>Arona IDE</h1>
      <div><span>Open file</span><span><kbd>Ctrl</kbd> <kbd>P</kbd></span></div>
      <div><span>Search in project</span><span><kbd>Ctrl</kbd> <kbd>Shift</kbd> <kbd>F</kbd></span></div>
      <div><span>Save + hot reload</span><span><kbd>Ctrl</kbd> <kbd>S</kbd></span></div>
      <div><span>Hot reload, RAM only</span><span><kbd>Alt</kbd> <kbd>R</kbd></span></div>
      <div><span>Command palette</span><span><kbd>F1</kbd></span></div>
      <div><span>Toggle panel</span><span><kbd>Ctrl</kbd> <kbd>J</kbd></span></div></div></div>
  <div class="gut h" id="g2"></div>
  <div id="panel">
    <div id="ptabs"><span class="pt" data-p="problems">Problems<i id="pb-n">0</i></span><span class="pt" data-p="output">Output</span>
      <span class="pt" data-p="console">Python console</span><span class="pt" data-p="terminal">Terminal</span><span class="sp"></span>
      <span class="ic" id="b-pclr" title="Clear">${ci('clear-all')}</span><span class="ic" id="b-pclose" title="Close panel">${ci('close')}</span></div>
    <div class="pv" id="pv-problems"><div class="list" id="plist"></div></div>
    <div class="pv" id="pv-output"><div class="list" id="olist"></div></div>
    <div class="pv" id="pv-console"><div id="con-out"></div><div id="con-in"></div>
      <div id="con-bar"><button class="btn" id="con-run">Run</button><span>Ctrl+Enter = run, Alt+Up/Down = history. Runs inside the bot, in the globals of main.py; top-level await works.</span><span id="con-st" style="margin-left:auto"></span></div></div>
    <div class="pv" id="pv-terminal"></div>
  </div>
</div>
<div id="status">
  <div class="si" id="si-hot" title="What Ctrl+S does (click to change)"></div><div class="si" id="si-prob" title="Problems"></div><div class="si" id="si-lsp" title="Pyright language server"></div>
  <div class="sp"></div>
  <div class="si" id="si-live" title="This module is loaded in the running bot"></div><div class="si" id="si-pos"></div><div class="si" id="si-ind"></div>
  <div class="si" id="si-enc">UTF-8</div><div class="si" id="si-eol" title="Toggle line endings"></div><div class="si" id="si-lang"></div>
</div>
<div id="qo"><input class="in" id="qi" placeholder="Go to file..."><div class="l" id="ql"></div></div>
<div id="ctx"></div><div id="modal"><div class="box"><p id="m-msg"></p><input class="in" id="m-in"><div class="bt"><button class="btn alt" id="m-no">Cancel</button><button class="btn" id="m-ok">OK</button></div></div></div>
<div id="toasts"></div>`;
// extra css that depends on the markup above
const st = document.createElement('style');
st.textContent = '#tabsrow{display:flex;background:var(--side);flex:none}#tabs{flex:1;min-width:0}#tabact{display:flex;align-items:center;padding:0 6px;gap:2px;flex:none}' +
  '#tabact .ic{width:26px;height:26px;display:inline-flex;align-items:center;justify-content:center;border-radius:4px;cursor:pointer;color:#c5c5c5;font-size:16px}#tabact .ic:hover{background:#ffffff1a}';
document.head.appendChild(st);

// ───────────────────────────── helpers: toast / modal / output ─────────────────────────────
function toast(msg, cls = '', ms = 5000) {
  const t = document.createElement('div'); t.className = 'toast ' + cls; t.textContent = msg; $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), ms); t.onclick = () => t.remove();
}
function modal({msg, input, ok = 'OK'}) {
  return new Promise(res => {
    const M = $('#modal'), inp = $('#m-in'); $('#m-msg').textContent = msg; $('#m-ok').textContent = ok;
    inp.style.display = input == null ? 'none' : ''; inp.value = input || ''; M.classList.add('on');
    const done = v => { M.classList.remove('on'); $('#m-ok').onclick = $('#m-no').onclick = M.onkeydown = null; ed && ed.focus(); res(v); };
    $('#m-ok').onclick = () => done(input == null ? true : inp.value); $('#m-no').onclick = () => done(null);
    M.onkeydown = e => { if (e.key === 'Enter') done(input == null ? true : inp.value); else if (e.key === 'Escape') done(null); };
    setTimeout(() => (input == null ? $('#m-ok') : inp).focus(), 0); if (input != null) inp.select();
  });
}
let outCount = 0;
function outLog(msg, cls = '') {
  const d = document.createElement('div'); d.className = 'it'; const t = new Date().toTimeString().slice(0, 8);
  d.innerHTML = '<span class="src">' + t + '</span><span class="' + cls + '"></span>'; d.lastChild.textContent = msg; d.lastChild.style.whiteSpace = 'pre-wrap';
  $('#olist').appendChild(d); $('#olist').scrollTop = 1e9; outCount++;
}
async function api(path, body) {
  const r = await fetch('/debug/api/ide/' + path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : undefined);
  if (r.status === 401) { location = '/debug/login?next=/debug/ide'; throw new Error('not logged in'); }
  const t = await r.text(); let j; try { j = JSON.parse(t); } catch (e) { j = null; }
  if (!r.ok) throw new Error((j && j.error) || t || ('HTTP ' + r.status));
  return j;
}

// ───────────────────────────── layout: views, panel, resizers ─────────────────────────────
function setView(v) {
  if (S.view === v && !$('#side').classList.contains('hide')) { $('#side').classList.add('hide'); $('#g1').classList.add('hide'); $('#app').style.gridTemplateColumns = '48px 0 0 1fr'; LS.set('sbhide', true); }
  else { S.view = v; $('#side').classList.remove('hide'); $('#g1').classList.remove('hide'); $('#app').style.gridTemplateColumns = ''; LS.set('sbhide', false); }
  document.querySelectorAll('#act .ab[data-v]').forEach(a => a.classList.toggle('on', a.dataset.v === S.view && !$('#side').classList.contains('hide')));
  document.querySelectorAll('.sv').forEach(s => s.classList.toggle('on', s.id === 'sv-' + S.view)); LS.set('view', S.view);
  if (S.view === 'search') $('#sq').focus(); if (S.view === 'vars') loadVars();
}
document.querySelectorAll('#act .ab[data-v]').forEach(a => a.onclick = () => setView(a.dataset.v));
function showSide(v) { $('#side').classList.add('hide'); setView(v); }
function showPanel(p) {
  if (p) { S.ptab = p; LS.set('ptab', p); }
  $('#panel').classList.remove('hide'); $('#g2').classList.remove('hide'); LS.set('phide', false);
  document.querySelectorAll('#ptabs .pt').forEach(t => t.classList.toggle('on', t.dataset.p === S.ptab));
  document.querySelectorAll('.pv').forEach(v => v.classList.toggle('on', v.id === 'pv-' + S.ptab));
  if (S.ptab === 'terminal' && !$('#term-fr')) { const f = document.createElement('iframe'); f.id = 'term-fr'; f.src = '/debug/terminal?embed=1'; $('#pv-terminal').appendChild(f); }
  if (S.ptab === 'console' && conEd) conEd.focus();
  if (S.ptab === 'problems') updateProblems();
}
function togglePanel() {
  if ($('#panel').classList.contains('hide')) showPanel(); else { $('#panel').classList.add('hide'); $('#g2').classList.add('hide'); LS.set('phide', true); ed && ed.focus(); }
}
document.querySelectorAll('#ptabs .pt').forEach(t => t.onclick = () => showPanel(t.dataset.p));
$('#b-pclose').onclick = togglePanel; $('#ab-panel').onclick = togglePanel;
$('#b-pclr').onclick = () => { if (S.ptab === 'output') { $('#olist').textContent = ''; outCount = 0; } else if (S.ptab === 'console') $('#con-out').textContent = ''; };
function drag(handle, onmove, cls) {
  handle.onpointerdown = e => { e.preventDefault(); handle.setPointerCapture(e.pointerId); handle.classList.add('drag');
    const mv = ev => onmove(ev), up = () => { handle.classList.remove('drag'); handle.removeEventListener('pointermove', mv); handle.removeEventListener('pointerup', up); };
    handle.addEventListener('pointermove', mv); handle.addEventListener('pointerup', up); };
}
drag($('#g1'), e => { const w = Math.max(150, Math.min(700, e.clientX - 48)); $('#app').style.setProperty('--sw', w + 'px'); LS.set('sw', w); });
drag($('#g2'), e => { const r = $('#mid').getBoundingClientRect(), h = Math.max(70, Math.min(r.height - 120, r.bottom - e.clientY)); $('#app').style.setProperty('--ph', h + 'px'); LS.set('ph', h); });
if (LS.get('sw')) $('#app').style.setProperty('--sw', LS.get('sw') + 'px');
if (LS.get('ph')) $('#app').style.setProperty('--ph', LS.get('ph') + 'px');

// ───────────────────────────── file tree ─────────────────────────────
const nodes = new Map(), treeEl = $('#tree');
$('#rootname').textContent = CFG.name;
const EXT_ICON = {py: ['file-code', '#4b8bbe'], pyw: ['file-code', '#4b8bbe'], js: ['file-code', '#cbcb41'], mjs: ['file-code', '#cbcb41'], ts: ['file-code', '#3178c6'], json: ['json', '#cbcb41'],
  md: ['markdown', '#519aba'], html: ['file-code', '#e44d26'], css: ['file-code', '#42a5f5'], java: ['file-code', '#e76f00'], bat: ['terminal', '#89e051'], sh: ['terminal', '#89e051'],
  png: ['file-media', '#a074c4'], jpg: ['file-media', '#a074c4'], gif: ['file-media', '#a074c4'], txt: ['file-text', '#aaa'], log: ['file-text', '#aaa'], db: ['database', '#d19a66'], yml: ['settings-gear', '#cb4b16'], yaml: ['settings-gear', '#cb4b16']};
function fileIcon(name, dir, open) {
  if (dir) return ci(open ? 'folder-opened' : 'folder', 'style="color:#dcb67a"').replace('codicon ', 'codicon fi ');
  const e = EXT_ICON[(name.split('.').pop() || '').toLowerCase()] || ['file', '#9d9d9d'];
  return ci(e[0], 'style="color:' + e[1] + '"').replace('codicon ', 'codicon fi ');
}
async function fillDir(path, kids, depth) {
  let items;
  try { items = await api('tree?path=' + enc(path)); } catch (e) { kids.textContent = ''; toast('Cannot list ' + (path || '/') + ': ' + e.message, 'err'); return; }
  kids.textContent = ''; const frag = document.createDocumentFragment();
  for (const it of items) frag.appendChild(mkNode(it, path, depth)); kids.appendChild(frag); markActive();
}
function mkNode(it, parent, depth) {
  const p = parent ? parent + '/' + it.n : it.n, wrap = document.createElement('div'), row = document.createElement('div');
  row.className = 'tr' + (it.x ? ' dim' : ''); row.style.paddingLeft = (6 + depth * 10) + 'px'; row.title = p;
  row.innerHTML = (it.d ? ci('chevron-right', 'style="font-size:16px"').replace('codicon ', 'codicon chev ') : '<span class="chev"></span>') + fileIcon(it.n, it.d, false) + '<span class="nm"></span><span class="dot"></span>';
  row.querySelector('.nm').textContent = it.n;
  const n = {p, row, wrap, dir: it.d, depth, kids: null, open: false, pl: null}; nodes.set(p, n); wrap.appendChild(row);
  if (it.d) { n.kids = document.createElement('div'); n.kids.hidden = true; wrap.appendChild(n.kids); row.onclick = () => toggleDir(n); if (S.expanded.has(p)) toggleDir(n, true); }
  else row.onclick = () => openFile(p);
  row.oncontextmenu = e => { e.preventDefault(); ctxMenu(e, n); };
  const t = S.tabs.find(x => x.path === p); if (t && t.dirty) row.querySelector('.dot').textContent = '●';
  return wrap;
}
async function toggleDir(n, force) {
  const open = force !== undefined ? force : !n.open; n.open = open; n.kids.hidden = !open;
  n.row.querySelector('.chev').className = 'codicon chev codicon-chevron-' + (open ? 'down' : 'right');
  n.row.querySelector('.fi').className = 'codicon fi codicon-folder' + (open ? '-opened' : '');
  if (open) { S.expanded.add(n.p); if (!n.pl) n.pl = fillDir(n.p, n.kids, n.depth + 1); await n.pl; } else S.expanded.delete(n.p);
  LS.set('exp', [...S.expanded]);
}
function clearUnder(p) { for (const k of [...nodes.keys()]) if (p === '' || k === p || k.startsWith(p + '/')) if (k !== p) nodes.delete(k); }
async function refreshDir(dir) {
  if (!dir) { nodes.clear(); await fillDir('', treeEl, 0); return; }
  const n = nodes.get(dir); if (!n) return; clearUnder(dir); n.pl = fillDir(dir, n.kids, n.depth + 1); await n.pl;
}
async function revealInTree(path) {
  const parts = path.split('/'); let acc = '';
  for (let i = 0; i < parts.length - 1; i++) { acc = acc ? acc + '/' + parts[i] : parts[i]; const n = nodes.get(acc); if (!n) return; if (!n.open) await toggleDir(n, true); else if (n.pl) await n.pl; }
  markActive(true);
}
function markActive(scroll) {
  treeEl.querySelectorAll('.tr.act').forEach(r => r.classList.remove('act'));
  const n = S.active && nodes.get(S.active); if (n) { n.row.classList.add('act'); if (scroll) n.row.scrollIntoView({block: 'nearest'}); }
}
function markDirty(path, d) { const n = nodes.get(path); if (n) n.row.querySelector('.dot').textContent = d ? '●' : ''; }
$('#b-rf').onclick = () => refreshDir('');
$('#b-ca').onclick = () => { S.expanded.clear(); LS.set('exp', []); refreshDir(''); };
$('#b-nf').onclick = () => newItem(selDir(), 'file'); $('#b-nd').onclick = () => newItem(selDir(), 'dir');
function selDir() { const n = S.active && nodes.get(S.active); return n ? S.active.split('/').slice(0, -1).join('/') : ''; }

// context menu + file operations
function ctxMenu(e, n) {
  const c = $('#ctx'), items = [];
  const dir = n ? (n.dir ? n.p : n.p.split('/').slice(0, -1).join('/')) : '';
  items.push(['New File...', () => newItem(dir, 'file')], ['New Folder...', () => newItem(dir, 'dir')]);
  if (n) items.push(null, ['Rename...', () => renameItem(n)], ['Delete', () => deleteItem(n)], null,
    ['Copy Path', () => navigator.clipboard && navigator.clipboard.writeText(CFG.root + '/' + n.p)], ['Copy Relative Path', () => navigator.clipboard && navigator.clipboard.writeText(n.p)]);
  c.innerHTML = ''; items.forEach(i => { if (!i) { c.appendChild(document.createElement('hr')); return; } const d = document.createElement('div'); d.textContent = i[0]; d.onclick = () => { c.style.display = 'none'; i[1](); }; c.appendChild(d); });
  c.style.display = 'block'; c.style.left = Math.min(e.clientX, innerWidth - 190) + 'px'; c.style.top = Math.min(e.clientY, innerHeight - c.offsetHeight - 6) + 'px';
}
treeEl.oncontextmenu = e => { if (e.target === treeEl) { e.preventDefault(); ctxMenu(e, null); } };
document.addEventListener('click', () => { $('#ctx').style.display = 'none'; });
async function newItem(dir, kind) {
  const name = await modal({msg: 'New ' + (kind === 'file' ? 'file' : 'folder') + ' in ' + (dir || CFG.name), input: ''}); if (!name || !name.trim()) return;
  const p = (dir ? dir + '/' : '') + name.trim();
  try { await api('fs', {op: kind === 'file' ? 'new_file' : 'new_dir', path: p}); } catch (e) { toast(e.message, 'err'); return; }
  await refreshDir(dir); if (dir && nodes.get(dir) && !nodes.get(dir).open) await toggleDir(nodes.get(dir), true);
  if (kind === 'file') openFile(p);
}
function tabsUnder(p) { return S.tabs.filter(t => t.path === p || t.path.startsWith(p + '/')); }
async function renameItem(n) {
  const old = n.p, parent = old.split('/').slice(0, -1).join('/'), name = await modal({msg: 'Rename ' + old, input: old.split('/').pop()}); if (!name || name === old.split('/').pop()) return;
  const aff = tabsUnder(old); if (aff.some(t => t.dirty)) { toast('Save or close the modified tabs first', 'warn'); return; }
  const wasOpen = aff.map(t => t.path); aff.forEach(t => closeTab(t, true));
  const to = (parent ? parent + '/' : '') + name.trim();
  try { await api('fs', {op: 'rename', path: old, to}); } catch (e) { toast(e.message, 'err'); return; }
  await refreshDir(parent); if (!n.dir && wasOpen.length) openFile(to);
}
async function deleteItem(n) {
  if (!await modal({msg: 'Delete ' + n.p + '?\nIt is moved to backup/ide/<run>/_deleted, not erased.', ok: 'Delete'})) return;
  tabsUnder(n.p).forEach(t => closeTab(t, true));
  try { await api('fs', {op: 'delete', path: n.p}); } catch (e) { toast(e.message, 'err'); return; }
  await refreshDir(n.p.split('/').slice(0, -1).join('/'));
}

// ───────────────────────────── Monaco + editor state ─────────────────────────────
let ed = null, conEd = null, conModel = null;
const relOf = uri => { const p = decodeURIComponent(uri.path).replace(/^\//, ''), r = CFG.root; return p.toLowerCase().startsWith(r.toLowerCase() + '/') ? p.slice(r.length + 1) : null; };
const tabOf = p => S.tabs.find(t => t.path === p);
function renderTabs() {
  const box = $('#tabs'); box.textContent = '';
  for (const t of S.tabs) {
    const d = document.createElement('div'); d.className = 'tab' + (t.path === S.active ? ' on' : '') + (t.dirty ? ' dirty' : ''); d.title = t.path;
    d.innerHTML = fileIcon(t.path.split('/').pop(), false).replace('codicon fi', 'codicon') + '<span class="nm"></span><span class="x">' + ci('close') + '</span>';
    d.querySelector('.nm').textContent = t.path.split('/').pop() + (t.ram ? ' ⚡' : '');
    d.onclick = () => activate(t); d.onauxclick = e => { if (e.button === 1) closeTab(t); };
    d.querySelector('.x').onclick = e => { e.stopPropagation(); closeTab(t); }; box.appendChild(d);
    if (t.path === S.active) d.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  $('#welcome').style.display = S.tabs.length ? 'none' : 'flex';
}
function persistTabs() { LS.set('tabs', S.tabs.map(t => t.path)); LS.set('active', S.active); }
function activate(t) {
  const prev = S.active && tabOf(S.active); if (prev && prev !== t) prev.vs = ed.saveViewState();
  S.active = t ? t.path : null;
  if (t) { ed.setModel(t.model); if (t.vs) ed.restoreViewState(t.vs); ed.focus(); document.title = t.path.split('/').pop() + ' - IDE'; revealInTree(t.path); }
  else { ed.setModel(null); document.title = 'IDE - Arona debug'; }
  renderTabs(); renderCrumbs(); updateStatus(); persistTabs(); markActive(true);
}
function renderCrumbs() {
  const c = $('#crumbs'); c.textContent = ''; if (!S.active) return;
  S.active.split('/').forEach((p, i, a) => { if (i) { const s = document.createElement('span'); s.className = 's'; s.innerHTML = ci('chevron-right', 'style="font-size:12px"'); c.appendChild(s); } const e = document.createElement('span'); e.className = 'c'; e.textContent = p; c.appendChild(e); });
}
function onEdit(t) {
  const d = t.model.getAlternativeVersionId() !== t.savedVid;
  if (d !== t.dirty) { t.dirty = d; if (d === false) t.ram = false; renderTabs(); markDirty(t.path, d); }
  if (t.syntaxMarked) { monaco.editor.setModelMarkers(t.model, 'syntax', []); t.syntaxMarked = false; }
}
async function openFile(path, pos, quiet) {
  let t = tabOf(path);
  if (!t) {
    let r; try { r = await api('read?path=' + enc(path)); } catch (e) { toast(path + ': ' + e.message, 'err'); return; }
    if (r.error) { toast(path + ': ' + r.error, 'warn'); return; }
    const uri = monaco.Uri.file(CFG.root + '/' + path), model = monaco.editor.getModel(uri) || monaco.editor.createModel(r.content, undefined, uri);
    t = {path, model, mtime: r.mtime, bom: r.bom, loaded: r.loaded, savedVid: model.getAlternativeVersionId(), dirty: false, ram: false};
    t.sub = model.onDidChangeContent(() => onEdit(t)); S.tabs.push(t);
    const rec = LS.get('recent', []).filter(x => x !== path); rec.unshift(path); LS.set('recent', rec.slice(0, 30));
  }
  if (!quiet) activate(t); else { renderTabs(); persistTabs(); }
  if (pos && !quiet) { const p = {lineNumber: pos.line, column: pos.col || 1}; ed.setPosition(p); if (pos.len) ed.setSelection(new monaco.Range(pos.line, pos.col, pos.line, pos.col + pos.len)); ed.revealPositionInCenter(p); ed.focus(); }
  return t;
}
function closeTab(t, force) {
  const go = () => {
    const i = S.tabs.indexOf(t); S.tabs.splice(i, 1); t.sub.dispose(); markDirty(t.path, false);
    if (S.active === t.path) { const nx = S.tabs[Math.min(i, S.tabs.length - 1)]; S.active = null; activate(nx || null); } else { renderTabs(); persistTabs(); }
    t.model.dispose();
  };
  if (t.dirty && !force) modal({msg: t.path + ' has unsaved changes. Close and discard them?', ok: 'Discard'}).then(v => v && go()); else go();
}

// ───────────────────────────── save + hot reload ─────────────────────────────
async function save(t, {write = true, apply = false, force = false} = {}) {
  const body = {path: t.path, content: t.model.getValue(), mtime: t.mtime, bom: t.bom, write, apply, force};
  let r, j;
  try { r = await fetch('/debug/api/ide/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); j = await r.json(); }
  catch (e) { toast('Save failed: ' + e, 'err'); return; }
  if (r.status === 401) { location = '/debug/login?next=/debug/ide'; return; }
  if (r.status === 409) { if (await modal({msg: t.path + ' changed on disk since you opened it.\nOverwrite it with your version?', ok: 'Overwrite'})) return save(t, {write, apply, force: true}); return; }
  if (!r.ok) { toast('Save failed: ' + (j.error || r.status), 'err'); return; }
  if (write) { t.mtime = j.mtime; t.savedVid = t.model.getAlternativeVersionId(); onEdit(t); t.stale = false; }
  else if (apply && !j.syntax) { t.ram = true; renderTabs(); }
  report(t, j, write, apply); updateStatus();
}
function report(t, j, write, apply) {
  const name = t.path.split('/').pop(); monaco.editor.setModelMarkers(t.model, 'syntax', []); t.syntaxMarked = false;
  if (j.syntax) {
    const s = j.syntax; t.syntaxMarked = true;
    monaco.editor.setModelMarkers(t.model, 'syntax', [{severity: 8, message: s.msg, source: 'Python', startLineNumber: s.line, startColumn: Math.max(1, s.col), endLineNumber: s.line, endColumn: Math.max(2, s.col + 1)}]);
    const m = name + ': SyntaxError line ' + s.line + ': ' + s.msg + (write ? '\nSaved to disk, but NOT applied to the running bot.' : '');
    outLog(m, 'e'); toast(m, 'err', 9000); return;
  }
  if (!apply) { outLog('Saved ' + t.path, 'ok'); toast('Saved ' + name, 'ok', 2000); return; }
  const h = j.hot || {}; const lines = [];
  if (h.error) { outLog(name + ': hot reload failed: ' + h.error, 'e'); toast('Hot reload failed: ' + h.error, 'err', 9000); return; }
  if (!h.loaded) { const m = (write ? 'Saved ' : '') + name + ': module not loaded in the bot (applies on next import / restart)'; outLog(m, 'w'); toast(m, 'warn'); return; }
  const n = h.patched.length + h.added.length + h.consts.length + h.imports.length, bits = [];
  if (h.patched.length) { bits.push(h.patched.length + ' patched'); lines.push('patched: ' + h.patched.join(', ')); }
  if (h.added.length) { bits.push(h.added.length + ' added'); lines.push('added: ' + h.added.join(', ')); }
  if (h.consts.length) { bits.push(h.consts.length + ' constants'); lines.push('constants: ' + h.consts.join(', ')); }
  if (h.imports.length) lines.push('imports: ' + h.imports.join(', '));
  (h.skipped || []).forEach(s => lines.push('SKIPPED ' + s.n + ': ' + s.why)); (h.restart || []).forEach(s => lines.push('needs restart: ' + s));
  const warn = (h.skipped || []).length || (h.restart || []).length;
  const head = (write ? 'Saved + ' : 'RAM only: ') + '⚡ ' + name + (n ? ' - ' + bits.join(', ') : ' - no runtime change') + (h.baseline ? '' : ' (no baseline: constants not compared)');
  outLog([head].concat(lines.map(l => '    ' + l)).join('\n'), warn ? 'w' : 'ok');
  toast(head + (warn ? '\n' + lines.filter(l => /^(SKIPPED|needs)/.test(l)).join('\n') : ''), warn ? 'warn' : 'ok', warn ? 9000 : 3500);
  if (t.path === S.active) { t.loaded = true; updateStatus(); }
}
const saveActive = () => { const t = tabOf(S.active); if (t) save(t, {write: S.mode !== 'ram', apply: S.mode !== 'disk'}); };
const hotActive = () => { const t = tabOf(S.active); if (t) save(t, {write: false, apply: true}); };
$('#b-save').onclick = saveActive; $('#b-hot').onclick = hotActive;
$('#si-hot').onclick = e => { e.stopPropagation(); const c = $('#ctx'); c.innerHTML = '';
  Object.entries(MODES).forEach(([k, v]) => { const d = document.createElement('div'); d.textContent = (S.mode === k ? '✓ ' : ' ') + v; d.onclick = () => { S.mode = k; LS.set('mode', k); updateStatus(); }; c.appendChild(d); });
  const r = e.target.getBoundingClientRect(); c.style.display = 'block'; c.style.left = r.left + 'px'; c.style.top = (r.top - c.offsetHeight - 4) + 'px'; };

// external changes (file edited elsewhere): checked when the window gets focus
async function checkDisk() {
  if (!S.tabs.length) return; let m; try { m = await api('stat', {paths: S.tabs.map(t => t.path)}); } catch (e) { return; }
  for (const t of S.tabs) {
    const ms = m[t.path]; if (ms == null || Math.abs(ms - t.mtime) <= 2) continue;
    if (t.dirty) { if (!t.stale) { t.stale = true; toast(t.path + ' changed on disk while you have unsaved edits (saving will ask before overwriting)', 'warn', 8000); } continue; }
    try { const r = await api('read?path=' + enc(t.path)); if (r.error) continue;
      t.model.pushEditOperations([], [{range: t.model.getFullModelRange(), text: r.content}], () => null); t.savedVid = t.model.getAlternativeVersionId(); t.mtime = r.mtime; onEdit(t); toast(t.path + ' reloaded from disk', '', 2500); } catch (e) {}
  }
}
window.addEventListener('focus', checkDisk);
window.addEventListener('beforeunload', e => { if (S.tabs.some(t => t.dirty)) { e.preventDefault(); e.returnValue = ''; } });

// ───────────────────────────── status bar + problems ─────────────────────────────
function updateStatus() {
  $('#si-hot').innerHTML = ci('zap') + '<span>' + MODES[S.mode] + '</span>'; $('#status').classList.toggle('dis', false);
  const t = tabOf(S.active), m = t && t.model;
  $('#si-live').innerHTML = t && t.loaded ? ci('circle-filled', 'style="font-size:9px;color:#9be39b"') + ' live in bot' : '';
  $('#si-eol').textContent = m ? (m.getEOL() === '\r\n' ? 'CRLF' : 'LF') : '';
  const o = m && m.getOptions(); $('#si-ind').textContent = o ? (o.insertSpaces ? 'Spaces: ' : 'Tab Size: ') + o.tabSize : '';
  const lang = m && monaco.languages.getLanguages().find(l => l.id === m.getLanguageId()); $('#si-lang').textContent = m ? (lang && lang.aliases && lang.aliases[0]) || m.getLanguageId() : '';
  const p = ed && ed.getPosition(); $('#si-pos').textContent = p && m ? 'Ln ' + p.lineNumber + ', Col ' + p.column : '';
}
$('#si-eol').onclick = () => { const t = tabOf(S.active); if (t) { t.model.setEOL(t.model.getEOL() === '\r\n' ? 0 : 1); updateStatus(); } };
$('#si-pos').onclick = () => ed && ed.trigger('status', 'editor.action.gotoLine');
$('#si-prob').onclick = () => showPanel('problems');
let pTimer; const SEVI = {8: ['error', 'e'], 4: ['warning', 'w'], 2: ['info', 'i'], 1: ['lightbulb', 'i']};
function updateProblems() {
  const ms = monaco.editor.getModelMarkers({}).filter(m => m.owner === 'pyright' || m.owner === 'syntax').sort((a, b) => b.severity - a.severity || a.startLineNumber - b.startLineNumber);
  const e = ms.filter(m => m.severity === 8).length, w = ms.filter(m => m.severity === 4).length;
  $('#si-prob').innerHTML = ci('error') + ' ' + e + ' ' + ci('warning') + ' ' + w; $('#pb-n').textContent = ms.length;
  const L = $('#plist'); if (S.ptab !== 'problems') return; L.textContent = '';
  if (!ms.length) { L.innerHTML = '<div class="it" style="color:#858585">No problems in the open files.</div>'; return; }
  for (const m of ms.slice(0, 500)) {
    const rel = relOf(m.resource) || (m.resource.path.split('/').pop()), s = SEVI[m.severity] || SEVI[8], d = document.createElement('div'); d.className = 'it';
    d.innerHTML = ci(s[0], 'class="' + s[1] + '"').replace('class="codicon', 'class="' + s[1] + ' codicon') + '<span></span><span class="src"></span><span class="loc"></span>';
    d.children[1].textContent = m.message; d.children[2].textContent = m.source || ''; d.children[3].textContent = rel + ' [' + m.startLineNumber + ', ' + m.startColumn + ']';
    d.onclick = () => { const r = relOf(m.resource); if (r && r !== '__debug_console__.py') openFile(r, {line: m.startLineNumber, col: m.startColumn}); else if (conEd) conEd.focus(); }; L.appendChild(d);
  }
}

// ───────────────────────────── quick open (Ctrl+P) ─────────────────────────────
let qfiles = null, qsel = 0, qitems = [];
function fuzzy(q, s) {
  const ql = q.toLowerCase(), sl = s.toLowerCase(), bn = s.lastIndexOf('/') + 1; let qi = 0, sc = 0, last = -2; const idx = [];
  for (let i = 0; i < sl.length && qi < ql.length; i++) if (sl[i] === ql[qi]) { sc += (i === last + 1 ? 5 : 1) + (i >= bn ? 3 : 0) + (i === 0 || '/_.-'.indexOf(sl[i - 1]) >= 0 ? 4 : 0); idx.push(i); last = i; qi++; }
  return qi === ql.length ? [sc - s.length * 0.05, idx] : null;
}
const hl = (s, idx, off) => { let o = '', set = new Set(idx); for (let i = 0; i < s.length; i++) o += set.has(i + off) ? '<mark>' + esc(s[i]) + '</mark>' : esc(s[i]); return o; };
async function qoOpen() {
  $('#qo').classList.add('on'); $('#qi').value = ''; $('#qi').focus(); if (!qfiles) { try { qfiles = await api('files'); } catch (e) { qfiles = []; } } qoRender();
}
function qoClose() { $('#qo').classList.remove('on'); ed && ed.focus(); }
function qoRender() {
  const q = $('#qi').value.trim(), L = $('#ql'); let r;
  if (!q) r = LS.get('recent', []).map(p => [p, [], 0]).concat((qfiles || []).slice(0, 40).map(p => [p, [], 0])).slice(0, 60);
  else r = (qfiles || []).map(p => { const f = fuzzy(q, p); return f && [p, f[1], f[0]]; }).filter(Boolean).sort((a, b) => b[2] - a[2]).slice(0, 60);
  qitems = r; qsel = Math.min(qsel, Math.max(0, r.length - 1)); L.textContent = '';
  r.forEach(([p, idx], i) => { const d = document.createElement('div'), b = p.lastIndexOf('/') + 1; d.className = 'it' + (i === qsel ? ' on' : '');
    d.innerHTML = '<span>' + hl(p.slice(b), idx, b) + '</span><span class="d">' + hl(p.slice(0, b), idx, 0) + '</span>'; d.onclick = () => { qoClose(); openFile(p); }; L.appendChild(d); });
}
$('#qi').oninput = () => { qsel = 0; qoRender(); };
$('#qi').onkeydown = e => {
  if (e.key === 'Escape') qoClose();
  else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); qsel = (qsel + (e.key === 'ArrowDown' ? 1 : -1) + qitems.length) % Math.max(1, qitems.length); qoRender(); const on = $('#ql .on'); on && on.scrollIntoView({block: 'nearest'}); }
  else if (e.key === 'Enter' && qitems[qsel]) { const p = qitems[qsel][0]; qoClose(); openFile(p); }
};
$('#qi').onblur = () => setTimeout(() => $('#qo').classList.remove('on'), 150);

// ───────────────────────────── search + variables views ─────────────────────────────
let sTimer = null, sSeq = 0;
async function doSearch() {
  const q = $('#sq').value, box = $('#sres'); if (!q) { box.textContent = ''; $('#s-n').textContent = ''; return; }
  const my = ++sSeq; $('#s-n').textContent = '...'; let r;
  try { r = await api('search?q=' + enc(q) + '&re=' + (S.sre ? 1 : 0) + '&case=' + (S.scase ? 1 : 0)); } catch (e) { box.textContent = e.message; return; }
  if (my !== sSeq) return; box.textContent = '';
  if (r.error) { box.textContent = r.error; $('#s-n').textContent = ''; return; }
  let total = 0; const frag = document.createDocumentFragment();
  for (const f of r) {
    const h = document.createElement('div'); h.className = 'fh'; h.innerHTML = fileIcon(f.p.split('/').pop(), false) + '<span></span><span class="n">' + f.m.length + '</span>'; h.children[1].textContent = f.p; h.title = f.p;
    const box2 = document.createElement('div'); h.onclick = () => { box2.hidden = !box2.hidden; }; frag.appendChild(h);
    for (const [line, col, len, text, off] of f.m) { total++; const d = document.createElement('div'); d.className = 'ln';
      d.innerHTML = esc(text.slice(0, off)) + '<b>' + esc(text.slice(off, off + len)) + '</b>' + esc(text.slice(off + len)); d.onclick = () => openFile(f.p, {line, col, len}); box2.appendChild(d); }
    frag.appendChild(box2);
  }
  box.appendChild(frag); $('#s-n').textContent = total + ' in ' + r.length + ' files' + (total >= 600 ? ' (limit)' : '');
}
$('#sq').oninput = () => { clearTimeout(sTimer); sTimer = setTimeout(doSearch, 350); };
$('#sq').onkeydown = e => { if (e.key === 'Enter') { clearTimeout(sTimer); doSearch(); } };
$('#s-case').onchange = e => { S.scase = e.target.checked; doSearch(); }; $('#s-re').onchange = e => { S.sre = e.target.checked; doSearch(); };
async function loadVars() {
  const box = $('#vlist'); box.textContent = 'loading...';
  try {
    const rows = await (await fetch('/debug/api/vars?q=' + enc($('#vq').value))).json(); box.textContent = '';
    rows.forEach(v => { const d = document.createElement('div'); d.className = 'vrow'; d.innerHTML = '<b></b><span></span><div></div>'; d.children[0].textContent = v.name; d.children[1].textContent = v.type; d.children[2].textContent = v.repr;
      d.onclick = () => { showPanel('console'); conEd.trigger('vars', 'type', {text: v.name}); conEd.focus(); }; box.appendChild(d); });
  } catch (e) { box.textContent = 'failed'; }
}
$('#b-rv').onclick = loadVars; $('#vq').onkeydown = e => { if (e.key === 'Enter') loadVars(); };

// ───────────────────────────── python console (runs inside the bot) ─────────────────────────────
let hist = LS.get('hist', []), hi = hist.length;
function conAdd(s, cls) { const o = $('#con-out'), sp = document.createElement('span'); if (cls) sp.className = cls; sp.textContent = s; o.appendChild(sp); o.scrollTop = o.scrollHeight; }
async function conExec() {
  const src = conEd.getValue(); if (!src.trim()) return; hist.push(src); hist = hist.slice(-100); hi = hist.length; LS.set('hist', hist);
  const run = $('#con-run'); run.disabled = true; $('#con-st').textContent = 'running...'; conAdd('>>> ' + src.split('\n').join('\n... ') + '\n', 'i');
  try {
    const r = await fetch('/debug/api/exec', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: src})});
    if (r.status === 401) { location = '/debug/login?next=/debug/ide'; return; }
    const j = await r.json(); if (j.out) conAdd(j.out.endsWith('\n') ? j.out : j.out + '\n'); if (j.result != null) conAdd(j.result + '\n', 'ok'); if (j.error) conAdd(j.error + '\n', 'e'); $('#con-st').textContent = j.ms + ' ms';
  } catch (e) { conAdd('request failed: ' + e + '\n', 'e'); $('#con-st').textContent = ''; }
  run.disabled = false; conEd.setValue(''); conEd.focus();
}
$('#con-run').onclick = conExec;

// ───────────────────────────── LSP client (Pyright over WebSocket) ─────────────────────────────
const lsp = {ws: null, up: false, seq: 0, pend: {}, vers: new Map(), byNu: new Map(), queue: new Map(), timer: null, ready: Promise.resolve(), ok: null};
const nu = u => decodeURIComponent(u).toLowerCase().replace(/\\/g, '/'), uriOf = m => m.uri.toString(), offOf = m => (m === conModel ? 1 : 0);
const lsend = o => lsp.ws && lsp.ws.readyState === 1 && lsp.ws.send(JSON.stringify({jsonrpc: '2.0', ...o}));
const lreq = (method, params) => new Promise((res, rej) => { const id = ++lsp.seq; lsp.pend[id] = [res, rej]; lsend({id, method, params}); });
const setLsp = s => { $('#si-lsp').innerHTML = ci('pulse') + ' ' + esc(s); };
function lspOpen(m) {
  if (!lsp.up || m.getLanguageId() !== 'python' || lsp.vers.has(m)) return;
  lsp.vers.set(m, 1); lsp.byNu.set(nu(uriOf(m)), m);
  lsend({method: 'textDocument/didOpen', params: {textDocument: {uri: uriOf(m), languageId: 'python', version: 1, text: (offOf(m) ? HEAD : '') + m.getValue()}}});
}
function lspFlush() {
  clearTimeout(lsp.timer); lsp.timer = null;
  for (const [m, q] of lsp.queue) { const v = lsp.vers.get(m) + 1; lsp.vers.set(m, v); lsend({method: 'textDocument/didChange', params: {textDocument: {uri: uriOf(m), version: v}, contentChanges: q}}); }
  lsp.queue.clear();
}
function lspChange(m, e) {
  if (!lsp.up || !lsp.vers.has(m)) return; const o = offOf(m), q = lsp.queue.get(m) || [];
  for (const c of e.changes) q.push({range: {start: {line: c.range.startLineNumber - 1 + o, character: c.range.startColumn - 1}, end: {line: c.range.endLineNumber - 1 + o, character: c.range.endColumn - 1}}, text: c.text});
  lsp.queue.set(m, q); if (!lsp.timer) lsp.timer = setTimeout(lspFlush, 60);
}
function lspStart() {
  setLsp('Pyright: starting...'); const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/debug/ws/lsp'); lsp.ws = ws; lsp.up = false;
  lsp.ready = new Promise(ok => { lsp.ok = ok; });
  ws.onopen = async () => {
    try {
      await lreq('initialize', {processId: null, rootUri: CFG.lspRoot, workspaceFolders: [{uri: CFG.lspRoot, name: CFG.name}], capabilities: {
        textDocument: {synchronization: {}, publishDiagnostics: {}, completion: {completionItem: {snippetSupport: false, documentationFormat: ['markdown', 'plaintext'], resolveSupport: {properties: ['documentation', 'detail']}}},
          hover: {contentFormat: ['markdown', 'plaintext']}, definition: {linkSupport: true}, documentSymbol: {hierarchicalDocumentSymbolSupport: true},
          signatureHelp: {signatureInformation: {documentationFormat: ['markdown', 'plaintext'], parameterInformation: {labelOffsetSupport: false}}}},
        workspace: {configuration: true, workspaceFolders: true}}});
      lsend({method: 'initialized', params: {}}); lsp.up = true; monaco.editor.getModels().forEach(lspOpen); lsp.ok(); setLsp('Pyright');
    } catch (e) { setLsp('Pyright: init failed'); }
  };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id !== undefined && !m.method) { const p = lsp.pend[m.id]; delete lsp.pend[m.id]; if (p) m.error ? p[1](m.error) : p[0](m.result); }
    else if (m.method === 'textDocument/publishDiagnostics') {
      const model = lsp.byNu.get(nu(m.params.uri)); if (!model || model.isDisposed()) return; const o = offOf(model);
      monaco.editor.setModelMarkers(model, 'pyright', m.params.diagnostics.filter(d => d.range.start.line >= o && !/"await" allowed only within async function/.test(d.message))
        .map(d => ({severity: {1: 8, 2: 4, 3: 2, 4: 1}[d.severity] || 8, message: d.message, source: 'Pyright' + (d.code ? ' ' + d.code : ''), startLineNumber: d.range.start.line - o + 1, startColumn: d.range.start.character + 1,
          endLineNumber: d.range.end.line - o + 1, endColumn: d.range.end.character + 1})));
    }
  };
  ws.onclose = () => { if (lsp.ws !== ws) return; lsp.up = false; lsp.vers.clear(); lsp.byNu.clear(); lsp.queue.clear(); setLsp('Pyright: disconnected (click to reconnect)'); Object.values(lsp.pend).forEach(p => p[1]('closed')); lsp.pend = {}; };
}
$('#si-lsp').onclick = () => { if (!lsp.up) lspStart(); };
const lask = async (method, m, p, extra) => {
  await Promise.race([lsp.ready, sleep(20000)]); if (!lsp.up || !lsp.vers.has(m)) return null; lspFlush();
  try { return await lreq(method, {textDocument: {uri: uriOf(m)}, position: {line: p.lineNumber - 1 + offOf(m), character: p.column - 1}, ...extra}); } catch (e) { return null; }
};
function registerLanguage() {
  const K = monaco.languages.CompletionItemKind, SK = monaco.languages.SymbolKind;
  const KM = {1: K.Text, 2: K.Method, 3: K.Function, 4: K.Constructor, 5: K.Field, 6: K.Variable, 7: K.Class, 8: K.Interface, 9: K.Module, 10: K.Property, 11: K.Unit, 12: K.Value, 13: K.Enum, 14: K.Keyword,
    15: K.Snippet, 16: K.Color, 17: K.File, 18: K.Reference, 19: K.Folder, 20: K.EnumMember, 21: K.Constant, 22: K.Struct, 23: K.Event, 24: K.Operator, 25: K.TypeParameter};
  const md = d => d ? (typeof d === 'string' ? d : d.value) : undefined, doc = d => md(d) ? {value: md(d)} : undefined;
  const rg = (m, r) => new monaco.Range(r.start.line - offOf(m) + 1, r.start.character + 1, r.end.line - offOf(m) + 1, r.end.character + 1);
  monaco.languages.registerCompletionItemProvider('python', {triggerCharacters: ['.'],
    provideCompletionItems: async (m, p) => { const r = await lask('textDocument/completion', m, p, {context: {triggerKind: 1}}); const items = Array.isArray(r) ? r : (r && r.items) || [];
      const w = m.getWordUntilPosition(p), dr = new monaco.Range(p.lineNumber, w.startColumn, p.lineNumber, w.endColumn);
      return {incomplete: !!(r && r.isIncomplete), suggestions: items.map(i => { const te = i.textEdit, tr = te && (te.range || te.replace);
        return {label: i.label, kind: KM[i.kind] || K.Text, detail: i.detail, documentation: doc(i.documentation), insertText: (te && te.newText) || i.insertText || i.label,
          range: tr && tr.start.line === tr.end.line ? rg(m, tr) : dr, sortText: i.sortText, filterText: i.filterText, _i: i}; })}; },
    resolveCompletionItem: async item => { try { const r = await lreq('completionItem/resolve', item._i); if (r) { item.documentation = doc(r.documentation) || item.documentation; item.detail = r.detail || item.detail; } } catch (e) {} return item; }});
  monaco.languages.registerHoverProvider('python', {provideHover: async (m, p) => { const r = await lask('textDocument/hover', m, p); if (!r || !r.contents) return null; const c = Array.isArray(r.contents) ? r.contents : [r.contents];
    return {contents: c.map(x => ({value: typeof x === 'string' ? x : (x.language ? '```' + x.language + '\n' + x.value + '\n```' : x.value)})), range: r.range ? rg(m, r.range) : undefined}; }});
  monaco.languages.registerSignatureHelpProvider('python', {signatureHelpTriggerCharacters: ['(', ','], provideSignatureHelp: async (m, p) => {
    const r = await lask('textDocument/signatureHelp', m, p); if (!r || !r.signatures || !r.signatures.length) return null;
    return {value: {signatures: r.signatures.map(s => ({label: s.label, documentation: doc(s.documentation), parameters: (s.parameters || []).map(x => ({label: x.label, documentation: doc(x.documentation)}))})),
      activeSignature: r.activeSignature || 0, activeParameter: r.activeParameter || 0}, dispose() {}}; }});
  monaco.languages.registerDefinitionProvider('python', {provideDefinition: async (m, p) => { const r = await lask('textDocument/definition', m, p); if (!r) return null;
    return (Array.isArray(r) ? r : [r]).map(l => { const uri = l.targetUri || l.uri, rr = l.targetSelectionRange || l.range, o = nu(uri) === nu(conModel.uri.toString()) ? 1 : 0;
      return {uri: monaco.Uri.parse(uri), range: new monaco.Range(rr.start.line - o + 1, rr.start.character + 1, rr.end.line - o + 1, rr.end.character + 1)}; }); }});
  const sym = (m, s) => ({name: s.name, detail: s.detail || '', kind: Math.max(0, (s.kind || 13) - 1), tags: [], range: rg(m, s.range), selectionRange: rg(m, s.selectionRange), children: (s.children || []).map(c => sym(m, c))});
  monaco.languages.registerDocumentSymbolProvider('python', {provideDocumentSymbols: async m => { await Promise.race([lsp.ready, sleep(20000)]); if (!lsp.up || !lsp.vers.has(m)) return [];
    lspFlush(); try { const r = await lreq('textDocument/documentSymbol', {textDocument: {uri: uriOf(m)}}); return (r || []).filter(s => s.range).map(s => sym(m, s)); } catch (e) { return []; } }});
  // ctrl+click / F12 into another project file opens it as a tab
  monaco.editor.registerEditorOpener({openCodeEditor: (src, res, sel) => { const rel = relOf(res); if (!rel) { toast('Definition is outside the project: ' + res.path.split('/').slice(-2).join('/'), 'warn'); return true; }
    const pos = sel ? {line: sel.startLineNumber || sel.lineNumber, col: sel.startColumn || sel.column} : null; openFile(rel, pos); return true; }});
}

// ───────────────────────────── boot ─────────────────────────────
self.MonacoEnvironment = {getWorkerUrl: () => URL.createObjectURL(new Blob(["self.MonacoEnvironment={baseUrl:'" + location.origin + MB + "'};importScripts('" + location.origin + MB + "vs/base/worker/workerMain.js');"], {type: 'text/javascript'}))};
require.config({paths: {vs: MB + 'vs'}});
require(['vs/editor/editor.main'], async () => {
  const common = {theme: 'vs-dark', automaticLayout: true, fontFamily: 'Consolas, "Cascadia Mono", "Courier New", monospace', fixedOverflowWidgets: true, scrollBeyondLastLine: false,
    unicodeHighlight: {ambiguousCharacters: false, invisibleCharacters: false, nonBasicASCII: false}, wordBasedSuggestions: 'off', quickSuggestions: {other: true, comments: false, strings: false}};
  ed = monaco.editor.create($('#ed'), {...common, model: null, fontSize: 14, minimap: {enabled: true, renderCharacters: false, maxColumn: 100}, smoothScrolling: true, cursorBlinking: 'smooth',
    cursorSmoothCaretAnimation: 'on', bracketPairColorization: {enabled: true}, guides: {bracketPairs: true, indentation: true}, stickyScroll: {enabled: true}, renderWhitespace: 'selection', mouseWheelZoom: true,
    padding: {top: 4}, tabSize: 4, insertSpaces: true, folding: true, showFoldingControls: 'mouseover', renderLineHighlight: 'all'});
  conModel = monaco.editor.createModel('', 'python', monaco.Uri.file(CFG.root + '/__debug_console__.py'));
  conEd = monaco.editor.create($('#con-in'), {...common, model: conModel, fontSize: 13, minimap: {enabled: false}, lineNumbers: 'off', glyphMargin: false, folding: false, lineDecorationsWidth: 6, overviewRulerLanes: 0,
    renderLineHighlight: 'none', padding: {top: 4}, tabSize: 4, insertSpaces: true, scrollbar: {alwaysConsumeMouseWheel: false}});
  conEd.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, conExec);
  conEd.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.UpArrow, () => { if (hi > 0) conEd.setValue(hist[--hi]); });
  conEd.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.DownArrow, () => { hi = Math.min(hist.length, hi + 1); conEd.setValue(hist[hi] || ''); });
  ed.onDidChangeCursorPosition(updateStatus); ed.onDidChangeModelOptions(updateStatus); ed.onDidChangeModelLanguage(updateStatus);
  monaco.editor.onDidCreateModel(m => { m.onDidChangeContent(e => lspChange(m, e)); lspOpen(m); });
  monaco.editor.onWillDisposeModel(m => { if (lsp.vers.has(m)) { lsend({method: 'textDocument/didClose', params: {textDocument: {uri: uriOf(m)}}}); lsp.vers.delete(m); lsp.byNu.delete(nu(uriOf(m))); lsp.queue.delete(m); } });
  monaco.editor.getModels().forEach(m => m.onDidChangeContent(e => lspChange(m, e)));
  monaco.editor.onDidChangeMarkers(() => { clearTimeout(pTimer); pTimer = setTimeout(updateProblems, 150); });
  ed.addAction({id: 'ide.hot', label: 'Hot reload this file (RAM only)', keybindings: [monaco.KeyMod.Alt | monaco.KeyCode.KeyR], run: hotActive});
  ed.addAction({id: 'ide.save', label: 'Save (per mode in the status bar)', run: saveActive});
  registerLanguage(); lspStart(); updateStatus(); updateProblems();
  await refreshDir(''); setView(S.view === 'explorer' || LS.get('sbhide') ? S.view : S.view); if (LS.get('sbhide')) { S.view = LS.get('view', 'explorer'); setView(S.view); }
  if (LS.get('phide')) { $('#panel').classList.add('hide'); $('#g2').classList.add('hide'); } else showPanel();
  const saved = LS.get('tabs', []), act = LS.get('active');
  for (const p of saved) await openFile(p, null, true);
  const t = tabOf(act) || S.tabs[S.tabs.length - 1]; if (t) activate(t); else renderTabs();
  setInterval(() => { if (document.hasFocus()) checkDisk(); }, 15000);
});

// global shortcuts (capture phase: they win over Monaco and over the browser)
document.addEventListener('keydown', e => {
  const k = e.key.toLowerCase(), c = e.ctrlKey || e.metaKey; let used = true;
  if (c && !e.shiftKey && !e.altKey && k === 's') saveActive();
  else if (c && !e.shiftKey && !e.altKey && k === 'p') qoOpen();
  else if (c && e.shiftKey && k === 'f') showSide('search');
  else if (c && e.shiftKey && k === 'e') showSide('explorer');
  else if (c && e.shiftKey && k === 'p') ed && ed.trigger('kb', 'editor.action.quickCommand');
  else if (c && !e.shiftKey && k === 'b') setView(S.view);
  else if (c && !e.shiftKey && k === 'j') togglePanel();
  else if (c && k === '`') { showPanel('terminal'); }
  else if (e.altKey && !c && k === 'r') hotActive();
  else if (e.altKey && !c && k === 'w') { const t = tabOf(S.active); t && closeTab(t); }
  else used = false;
  if (used) { e.preventDefault(); e.stopPropagation(); }
}, true);
})();
