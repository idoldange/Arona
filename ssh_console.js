// ssh_console.js - the console client sshd runs for the SSH user "arona" (dispatched from C:\CustomShell\arch.py).
// Logs in to the local web console with the password in pass.txt (never printed) and relays commands, like client.py.
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { io } = require('socket.io-client');

const BASE = 'http://127.0.0.1:3000';
const USER = 'idoldange';
const C = { red: '\x1b[31m', green: '\x1b[32m', yellow: '\x1b[33m', cyan: '\x1b[36m', magenta: '\x1b[35m', off: '\x1b[0m' };
const COLORS = { '#000': 30, '#A00': 31, '#0A0': 32, '#A50': 33, '#00A': 34, '#A0A': 35, '#0AA': 36, '#AAA': 37, '#555': 90 };
const say = (c, s) => process.stdout.write(`${c}${s}${C.off}\n`);

// Server output is HTML (colored spans, <br>, <img>, <audio>) -> ANSI text.
function htmlToAnsi(html) {
  const ents = { '&nbsp;': ' ', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&amp;': '&' };
  const attr = (a, n) => (new RegExp(n + '\\s*=\\s*"([^"]*)"', 'i').exec(a) || [])[1] || '';
  const re = /<\s*(\/?)\s*([a-z0-9]+)([^>]*)>|([^<]+)/gi;
  let out = '', m, audioDone = false;
  const stack = [];
  while ((m = re.exec(html))) {
    if (m[4] !== undefined) { out += m[4].replace(/&(?:nbsp|lt|gt|quot|#39|amp);/g, e => ents[e]); continue; }
    const close = m[1] === '/', tag = m[2].toLowerCase(), a = m[3] || '';
    if (tag === 'br') out += '\n';
    else if (tag === 'span') {
      if (close) { stack.pop(); out += C.off + (stack[stack.length - 1] || ''); }
      else {
        const col = /color:\s*([^;"']+)/i.exec(a);
        const code = col && COLORS[col[1].trim()];
        stack.push(code ? `\x1b[${code}m` : (stack[stack.length - 1] || ''));
        if (code) out += `\x1b[${code}m`;
      }
    } else if (tag === 'img' && !close) out += `![${attr(a, 'alt') || 'image'}](${attr(a, 'src')})`;
    else if (tag === 'audio') {
      if (close) audioDone = false;
      else if (attr(a, 'src')) { out += `[audio](${attr(a, 'src')})`; audioDone = true; }
    } else if (tag === 'source' && !audioDone) out += `[audio](${attr(a, 'src')})`;
  }
  return out + C.off;
}

async function login() {
  let pw;
  try { pw = fs.readFileSync(path.join(__dirname, 'pass.txt'), 'utf8').trim(); }
  catch (e) { say(C.red, 'Cannot read pass.txt (' + e.code + ').'); return null; }
  try {
    const r = await fetch(BASE + '/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: USER, password: pw }), signal: AbortSignal.timeout(10000),
    });
    if (r.ok) return r.headers.getSetCookie().map(c => c.split(';')[0]).join('; ');
    say(C.red, `Login failed (HTTP ${r.status}).`);
  } catch (e) { say(C.red, 'Login error: ' + ((e.cause && e.cause.code) || e.message)); }
  return null;
}

let socket, showLogs = false;
const schedules = new Map();
let seq = 0;

function dispatch(cmd) {
  const low = cmd.toLowerCase();
  if (low === 'logs') { showLogs = true; socket.emit('loadLogs', ''); say(C.cyan, 'Fetching log list...'); }
  else if (low.startsWith('read ')) socket.emit('loadLogs', cmd.slice(5).trim());
  else if (['start', 'stop', 'restart', 'kill'].includes(low)) socket.emit(low);
  else socket.emit('command', cmd);   // 'clear' and everything else
}

// schedule [daily] HH:MM[:SS] <command>
function parseTime(t) {
  const p = t.split(':');
  if (p.length === 2) p.push('0');
  const n = p.map(Number);
  if (n.length !== 3 || !n.every(Number.isInteger) || n[0] < 0 || n[0] > 23 || n[1] < 0 || n[1] > 59 || n[2] < 0 || n[2] > 59)
    throw new Error('Time must be HH:MM or HH:MM:SS');
  return n;
}

function addSchedule(timeStr, command, daily) {
  const [h, m, s] = parseTime(timeStr);
  const id = (++seq).toString(36) + Math.random().toString(36).slice(2, 5);
  const info = { time: timeStr, command, daily, timer: null };
  schedules.set(id, info);
  const arm = () => {
    const t = new Date(); t.setHours(h, m, s, 0);
    if (t <= new Date()) t.setDate(t.getDate() + 1);
    say(C.cyan, `[Schedule #${id}] '${command}' will run at ${t.toLocaleString()}`);
    info.timer = setTimeout(() => {
      say(C.cyan, `[Schedule #${id}] Sending: ${command}`);
      dispatch(command);
      if (daily) arm(); else schedules.delete(id);
    }, t - Date.now());
  };
  arm();
  return id;
}

function handleLine(line) {
  const cmd = line.trim(), low = cmd.toLowerCase();
  if (!cmd) return;
  if (low === 'exit') return quit(0);
  if (low === 'schedules') {
    if (!schedules.size) return say(C.yellow, 'No schedules.');
    return schedules.forEach((i, id) => console.log(`[${id}] ${i.time} (${i.daily ? 'daily' : 'once'}) -> ${i.command}`));
  }
  if (low.startsWith('unschedule ')) {
    const id = cmd.slice(11).trim(), i = schedules.get(id);
    if (!i) return say(C.red, `Schedule #${id} not found`);
    clearTimeout(i.timer); schedules.delete(id);
    return say(C.green, `Cancelled schedule #${id}`);
  }
  if (low.startsWith('schedule ')) {
    let raw = cmd.slice(9).trim(), daily = false;
    if (raw.toLowerCase().startsWith('daily ')) { daily = true; raw = raw.slice(6).trim(); }
    const sp = raw.indexOf(' '), time = sp < 0 ? '' : raw.slice(0, sp), command = sp < 0 ? '' : raw.slice(sp + 1).trim();
    if (!time || !command) return say(C.yellow, 'Usage: schedule [daily] HH:MM[:SS] <command>   e.g. schedule daily 06:00 restart');
    try { const id = addSchedule(time, command, daily); say(C.green, `Created schedule #${id} (${daily ? 'daily' : 'once'}) at ${time} -> '${command}'`); }
    catch (e) { say(C.red, 'Error: ' + e.message); }
    return;
  }
  dispatch(cmd);
}

function quit(code) {
  schedules.forEach(i => clearTimeout(i.timer));
  try { socket && socket.disconnect(); } catch (_) { /* ignore */ }
  process.exit(code);
}

(async () => {
  const cookie = await login();
  if (!cookie) process.exit(1);
  socket = io(BASE, { extraHeaders: { Cookie: cookie }, reconnectionDelay: 1000, reconnectionDelayMax: 10000 });
  socket.on('connect', () => say(C.green, '[+] Connected to the server.'));
  socket.on('disconnect', r => say(C.red, `[!] Disconnected (${r}). Reconnecting...`));
  socket.on('connect_error', e => say(C.red, `[!] Connection error: ${e.message}`));
  socket.on('output', d => process.stdout.write(htmlToAnsi(String(d))));
  socket.on('status', s => say(C.yellow, `Bot Status: ${String(s).toUpperCase()}`));
  socket.on('logFiles', files => {
    if (!showLogs) return;
    showLogs = false;
    say(C.cyan, '--- Available Log Files ---');
    files.forEach((f, i) => console.log(`[${i}] ${f}`));
    say(C.cyan, "Type: 'read <filename>' to view content.");
  });
  socket.on('logData', d => { say(C.magenta, '--- Log File Content ---'); console.log(htmlToAnsi(String(d))); say(C.magenta, '--- End of File ---'); });

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: !!process.stdin.isTTY, prompt: '' });
  rl.on('line', handleLine);
  rl.on('SIGINT', () => quit(0));
  rl.on('close', () => quit(0));
})();
