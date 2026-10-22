// Interactive chess board inside the Arona Discord Activity.
// The Discord side (utils/discord_ui_chess.py) starts the game; this page renders
// the same board in a chess.com-style layout, plays moves back through the bot's
// /api/chess/* endpoints and follows every move both sides make.
//
// Layout rules (from the chess.com-style mock):
// - Your pieces are always at the bottom (spectators get White at the bottom).
// - Opponent bar: real name + avatar in PvP, "Arona" + engine Elo in PvE, with no
//   captured-pieces display.
// - Right panel: mode title (Play vs Arona / PvP Game), move list, resign button
//   for both modes; take-back and hint buttons only in engine games (no undo in PvP).
const PIECE_NAMES = { p: "pawn", n: "knight", b: "bishop", r: "rook", q: "queen", k: "king" }
const PIECE_VALUES = { p: 1, n: 3, b: 3, r: 5, q: 9 }
const FULL_COUNT = { p: 8, n: 2, b: 2, r: 2, q: 1 }
const POLL_MS = 1200

const $ = (id) => document.getElementById(id)

let state = null            // latest /api/chess/state payload
let sel = null              // selected square index (python-chess indexing)
let hint = null             // {from, to} currently displayed hint
let busy = false            // a mutating request is in flight
let optimistic = null       // {from, to} square idx of OUR move while its POST is in flight -> piece snaps instantly
let lastAnimatedUci = ""    // last move already slid on screen; skip re-animating the same one (server echo, self echo)
let prevFen = null          // FEN before the latest state replace: used to detect captures by the moving side
let stopPoll = false
let clockSnap = null        // latest clock snapshot + receipt time; the countdown runs locally between polls
let slotColor = { top: "black", bottom: "white" }   // which side's clock each player bar shows
let lastZeroPoll = 0
let pollError = ""          // last /api/chess/state failure, shown on the gate so a blank board explains itself
const BUILD = "chess-clock-5"

// ── âm lượng di chuyển (SE_Chess cua Blue Archive wiki) ──
// 01 tram (di buon), 02 trung (an quan), 03 cao (chieu het / tran ket thuc), ui-select (bam nut / grab quan).
// -v2 & ui-select: da cat am im o dau de "dat quan la nghe ngay".
const SFX_URL = { move: "/sfx/chess-01-v2.mp3", capture: "/sfx/chess-02-v2.mp3", mate: "/sfx/chess-03-v2.mp3", ui: "/sfx/ui-select.mp3" }
const sfxEls = {}
function sfx(kind) {
    try {
        const el = sfxEls[kind] || (sfxEls[kind] = new Audio(SFX_URL[kind]))
        el.currentTime = 0
        el.play().catch(() => {})      // autoplay bi chan thi im lang, khong vang loi
    } catch (ignored) { /* khong co Audio */ }
}

// ── nhạc nền (BGM) + jingle thang/thua ──
// Server trich YouTube bang yt-dlp va phuc vu file tinh: /api/chess/bgm/resolve -> /bgm-audio/<id>.<ext>
// Giao dien o panel: dropdown playlist de tinh, paste link YouTube bat ky, volume slider; luu localStorage.
const WIN_URL = "https://youtu.be/XC0UwqE13U8"
const DEFEAT_URL = "https://youtu.be/v6Zw9m89CS8"
const JINGLE_URLS = { win: WIN_URL, defeat: DEFEAT_URL }
const jingleSrc = {}             // win/defeat -> src file dai cache tren server, trich lan dau roi dung ngay
let bgmAudio = null              // the <audio> shuffle BGM (loop)
let bgmOpening = false
let bgmPlayingKey = ""
let bgmVolume = 35
let jinglePlayedKey = ""         // so nuoc cua tran da phat jingle -> choi dung 1 lan moi tran

function bgmPrefs() {
    const defaults = { key: "", label: "", custom: "", vol: 35 }
    try { return { ...defaults, ...(JSON.parse(localStorage.getItem("arona-bgm") || "{}")) } }
    catch (ignored) { /* private mode -> khong luu */ return defaults }
}
function bgmSave(p) { try { localStorage.setItem("arona-bgm", JSON.stringify(p)) } catch (ignored) { /* private mode */ } }

function updateBgmToggle() {
    const btn = $("bgm-toggle")
    if (btn) { btn.textContent = `🎵 ${bgmPrefs().label || "none"}` }
}

function applyVolume(vol) {
    bgmVolume = vol
    if (bgmAudio) { bgmAudio.volume = vol / 100 }
    for (const el of Object.values(sfxEls)) { el.volume = vol / 100 }
    updateBgmStatus(`volume ${vol}%`)
    bgmSave({ ...bgmPrefs(), vol })
}

function updateBgmStatus(text) { $("bgm-status").textContent = text }

function stopBgm() {
    if (bgmAudio) { try { bgmAudio.pause() } catch (ignored) { /* */ } }
    bgmAudio = null
    bgmPlayingKey = ""
    updateBgmStatus("stopped")
    bgmSave({ ...bgmPrefs(), key: "", label: "" })
    updateBgmToggle()
}

async function bgmPlay(entry) {
    const url = entry.custom || (entry.key ? `https://www.youtube.com/watch?v=${entry.key}` : "")
    if (!url) { stopBgm(); return }
    updateBgmStatus("loading...")
    try {
        const res = await apiGet(`/api/chess/bgm/resolve?url=${encodeURIComponent(url)}`)
        if (res.error) { updateBgmStatus(res.error); return }
        if (res.playlist) {
            fillPlaylist(res.entries || [])
            updateBgmStatus("playlist loaded")
            return
        }
        if (bgmAudio) { try { bgmAudio.pause() } catch (ignored) { /* */ } }
        bgmAudio = new Audio(res.src)
        bgmAudio.loop = true
        bgmAudio.volume = bgmVolume / 100
        await bgmAudio.play()
        bgmPlayingKey = entry.key || "custom"
        const title = res.title || entry.label || "playing"
        $(`bgm-status`).textContent = `▶ ${title}`
        const p = bgmPrefs()
        p.label = title
        if (entry.key) { p.key = entry.key } else { p.key = ""; p.custom = entry.custom || p.custom }
        bgmSave(p)
        updateBgmToggle()
    } catch (e) {
        updateBgmStatus((e && e.message) || "load failed")
    }
}

function fillPlaylist(entries) {
    const select = $("bgm-select")
    const prefs = bgmPrefs()
    select.innerHTML = ""
    const none = document.createElement("option")
    none.value = ""
    none.textContent = "music: none"
    select.appendChild(none)
    const custom = document.createElement("option")
    custom.value = "__custom"
    custom.textContent = prefs.custom ? `custom: ${prefs.custom}` : "paste a link below..."
    select.appendChild(custom)
    for (const entry of entries) {
        const opt = document.createElement("option")
        opt.value = entry.id
        opt.textContent = (entry.title || entry.id).slice(0, 64)
        select.appendChild(opt)
    }
    if (prefs.key && entries.some(e => e.id === prefs.key)) { select.value = prefs.key }
    else if (prefs.custom) { select.value = "__custom" }
}

async function loadBgmList() {
    if (bgmOpening) return
    bgmOpening = true
    updateBgmStatus("loading playlist...")
    try {
        const payload = await apiGet("/api/chess/bgm/list")
        if (payload.error) { updateBgmStatus(payload.error) }
        else { fillPlaylist(payload.entries || []); updateBgmStatus("ready") }
    } catch (e) {
        updateBgmStatus((e && e.message) || "playlist failed")
    }
    bgmOpening = false
}

function ensureJingleResolved() {
    // Cho phat hien tran bat dau -> tai ngam 2 bang nhac: lan dau server chay yt-dlp (may chut), lan sau nhat file av.
    for (const [kind, url] of Object.entries(JINGLE_URLS)) {
        if (jingleSrc[kind]) continue
        apiGet(`/api/chess/bgm/resolve?url=${encodeURIComponent(url)}`)
            .then(r => { if (r && r.src) jingleSrc[kind] = r.src; else if (r && r.error) updateBgmStatus(`jingle: ${r.error}`) })
            .catch(() => {})
    }
}

let jingleAudio = null          // bang sau ket qua (win/defeat), loop de tiep tuc phat khi tran da ket thuc

function stopJingle() {
    if (jingleAudio) { try { jingleAudio.pause() } catch (ignored) { /* */ } }
    jingleAudio = null
}

function playJingle(kind) {
    const start = () => {
        stopJingle()
        jingleAudio = new Audio(jingleSrc[kind])
        jingleAudio.loop = true           // ca win & defeat vong lap cho den khi bat tran moi
        jingleAudio.volume = bgmVolume / 100
        jingleAudio.play().catch(() => {})
    }
    if (jingleSrc[kind]) { start(); return }
    ensureJingleResolved()
    let tries = 0
    const wait = setInterval(() => {
        if (jingleSrc[kind]) { clearInterval(wait); start(); return }
        if (++tries > 60) { clearInterval(wait); updateBgmStatus("jingle not ready") }
    }, 500)
}

// Gọi sau khi state moi ve (poll hoac POST); phat 1 lan khi tran vua chot ket qua cho nguoi xem.
function maybeJingle(prev) {
    if (!state || !state.over) {
        if (prev && prev.over) {
            jinglePlayedKey = ""      // tran moi chay lai sau khi vua ket thuc -> cho phep jingle lan sau
            stopJingle()              // tat bang jingle ket qua va chuyen ve BGM binh thuong
        }
        return
    }
    if (!prev || prev.over || !state.winner || state.you.role === "spectator") return
    const key = `${state.winner}:${state.moves.length}`
    if (jinglePlayedKey === key) return
    jinglePlayedKey = key
    stopBgm()                                                // coi BGM de nghe ro jingle ket qua
    if (state.winner === myColor()) { playJingle("win") }
    else { playJingle("defeat") }
}

// BGM UI (phan tu dem trong board.html): listeners dem bam mot lan khi module chay (dom co san roi).
(function initBgm() {
    const select = $("bgm-select")
    select.addEventListener("change", () => {
        sfx("ui")                                     // mo/doi bai trong dropdown -> click loi
        const v = select.value
        if (v === "") { stopBgm(); return }
        if (v === "__custom") { $("bgm-custom").focus(); return }
        bgmPlay({ key: v })
    })
    $("bgm-custom-btn").addEventListener("click", () => {
        sfx("ui")                                     // nut play link paste -> click loi
        const url = $("bgm-custom").value.trim()
        if (!url) return
        bgmPlay({ custom: url, label: "custom" })
    })
    $("bgm-custom").addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { $("bgm-custom-btn").click(); ev.preventDefault() }
    })
    $("bgm-stop").addEventListener("click", () => { sfx("ui"); stopBgm() })
    // Keo anh volng: chot "ui" theo nhip 120ms de khong spam SFX lint tuc luc keo.
    let lastVolClick = 0
    $("bgm-vol").addEventListener("input", (ev) => {
        const now = performance.now()
        if (now - lastVolClick > 120) { lastVolClick = now; sfx("ui") }
        applyVolume(Number(ev.target.value || 0))
    })
    // Reload trang + da co BGM luu truoc -> bat sau lan tuong tac dau (autoplay chinh sach cua trinh duyet).
    document.addEventListener("pointerdown", () => {
        if (bgmAudio) return
        const p = bgmPrefs()
        if (p.key) { bgmPlay({ key: p.key, label: p.label }) }
        else if (p.custom) { bgmPlay({ custom: p.custom, label: p.label }) }
    }, { once: true })
    const volSlider = $("bgm-vol")
    if (volSlider) { volSlider.value = String(bgmPrefs().vol); bgmVolume = bgmPrefs().vol }
})()

// ── thanh trượt Elo cho PvE: keo thay do manh cua Arona, gui len server luc nhan New game ──
const ELO_MIN = 400, ELO_MAX = 3190
let eloShown = null          // gia tri slider hien tai (null = chua keo lay, theo state.elo)

function eloSaved() {
    const v = parseInt(localStorage.getItem("arona-elo") || "", 10)
    return Number.isFinite(v) ? Math.max(ELO_MIN, Math.min(ELO_MAX, v)) : null
}

function syncEloRow() {
    const row = $("elo")
    if (!row) return
    row.hidden = !(state && state.active && state.mode === "engine")
    if (row.hidden) return
    if (!eloShown) { eloShown = eloSaved() || Number(state && state.elo) || 1500 }
    const val = Math.max(ELO_MIN, Math.min(ELO_MAX, Math.round(eloShown / 10) * 10))
    eloShown = val
    const slider = $("elo-slider")
    slider.min = String(ELO_MIN)
    slider.max = String(ELO_MAX)
    slider.step = "10"
    slider.value = String(val)
    $("elo-label").textContent = `⚡ Arona ~${val} elo`
}

;(function initElo() {
    const slider = $("elo-slider")
    let lastUIClick = 0
    slider.addEventListener("input", (ev) => {
        const now = performance.now()
        if (now - lastUIClick > 120) { lastUIClick = now; sfx("ui") }
        eloShown = Math.max(ELO_MIN, Math.min(ELO_MAX, Number(ev.target.value) || 1500))
        $("elo-label").textContent = `⚡ Arona ~${eloShown} elo`
        localStorage.setItem("arona-elo", String(eloShown))
    })
})()

// ── game clock (timed games only: blitz / bullet / any time limit) ──
function syncClock(next) {
    const c = next && next.clock
    clockSnap = c ? {
        at: performance.now(),
        white_ms: c.white_ms,
        black_ms: c.black_ms,
        running: c.running,
        turn: next.turn,
        flagged: c.flagged,
        inc: c.increment || 0,
    } : null
}

function liveMs(color) {
    let ms = color === "white" ? clockSnap.white_ms : clockSnap.black_ms
    if (clockSnap.running && clockSnap.turn === color) ms -= performance.now() - clockSnap.at
    return Math.max(0, ms)
}

function formatClock(ms) {
    const pad = (n) => String(n).padStart(2, "0")
    if (ms < 10000) {
        return `0:${pad(Math.floor(ms / 1000))}.${Math.floor((ms % 1000) / 100)}`
    }
    const secs = Math.floor(ms / 1000)
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    const s = secs % 60
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

// Compare states without the ever-changing remaining-ms, so a ticking clock
// does not force a full board re-render on every poll.
function stateKey(s) {
    if (!s || !s.clock) return JSON.stringify(s)
    const { clock, ...rest } = s
    const { white_ms, black_ms, ...steady } = clock
    return JSON.stringify({ ...rest, clock: steady })
}

// While our own move is in flight (the server may sit on it while Arona thinks)
// pretend the turn already passed, so the right clock keeps running on screen.
function flipClockLocally() {
    if (!clockSnap || !clockSnap.running) return
    const mover = clockSnap.turn
    const left = liveMs(mover) + clockSnap.inc * 1000
    clockSnap = {
        ...clockSnap,
        white_ms: mover === "white" ? left : clockSnap.white_ms,
        black_ms: mover === "black" ? left : clockSnap.black_ms,
        turn: mover === "white" ? "black" : "white",
        at: performance.now(),
    }
}

function updateClocks() {
    for (const slot of ["top", "bottom"]) {
        const el = $(`${slot}-clock`)
        if (!el) continue
        if (!clockSnap || !state || !state.active) {
            el.hidden = true
            continue
        }
        const color = slotColor[slot]
        const ms = liveMs(color)
        const ticking = clockSnap.running && clockSnap.turn === color
        el.hidden = false
        el.textContent = formatClock(ms)
        el.classList.toggle("active", ticking)
        el.classList.toggle("low", ms < 20000)
        el.classList.toggle("flagged", clockSnap.flagged === color)
        if (ticking && ms <= 0 && performance.now() - lastZeroPoll > 800) {
            lastZeroPoll = performance.now()
            pollOnce()      // flag fell: ask the server now instead of waiting for the next poll
        }
    }
}

function myColor() {
    const role = state.you.role
    if (role === "white") return "white"
    if (role === "black") return "black"
    //Engine game: every member plays the human side (the non-bot side).
    if (role === "player") return state.white.is_bot ? "black" : "white"
    return "white"          // spectator: White at the bottom
}

function myTurn() {
    if (!state || state.over || busy) return false
    const turn = state.turn
    if (state.mode === "pvp") return state.you.role === turn
    if (state.you.role === "spectator") return false
    return turn === myColor()
}

function legalTargets(fromIdx) {
    const alg = squareName(fromIdx)
    const out = []
    for (const uci of state.legal_moves) {
        if (uci.slice(0, 2) === alg) out.push({ to: uci.slice(2, 4), promotion: uci.slice(4) || null })
    }
    return out
}

function squareName(idx) {  // python-chess index -> "e4"
    return "abcdefgh"[idx & 7] + (((idx >> 3) & 7) + 1)
}

function parseSquare(name) {
    return ((((name.charCodeAt(1) - 49)) & 7) << 3) | ("abcdefgh".indexOf(name[0]) & 7)
}

// FEN placement -> python-chess indexed map (0 = a1 ... 63 = h8)
function parseFen(fen) {
    const map = new Array(64).fill(null)
    const rows = fen.split(" ")[0].split("/")
    rows.forEach((row, rowIdx) => {
        let file = 0
        for (const ch of row) {
            if (/\d/.test(ch)) {
                file += Number(ch)
                continue
            }
            const sq = ((7 - rowIdx) * 8) + file
            map[sq] = { symbol: ch, white: ch === ch.toUpperCase() }
            file += 1
        }
    })
    return map
}

function capturedList(map, color) {
    // Pieces of `color` that are missing from the board, i.e. what the OTHER side
    // captured. Promotion skews counts (two queens) — clamp at zero per piece type.
    const remaining = {}
    for (let sq = 0; sq < 64; sq++) {
        const piece = map[sq]
        if (piece && piece.white === (color === "white")) {
            const key = piece.symbol.toLowerCase()
            remaining[key] = (remaining[key] || 0) + 1
        }
    }
    const list = []
    let advantage = 0
    for (const key of Object.keys(FULL_COUNT)) {
        const missing = Math.max(0, FULL_COUNT[key] - (remaining[key] || 0))
        for (let i = 0; i < missing; i++) {
            list.push({ color, symbol: key })
            advantage += PIECE_VALUES[key]
        }
    }
    return { list, advantage }
}

function avatarBackground(name) {
    let hue = 0
    for (const ch of name) hue = (hue * 31 + ch.charCodeAt(0)) >>> 0
    return `hsl(${hue % 360}, 45%, 42%)`
}

function setAvatar(img, letterEl, url, name) {
    if (url) {
        img.hidden = false
        letterEl.hidden = true
        if (img.getAttribute("src") !== url) img.src = url
    } else {
        img.hidden = true
        letterEl.hidden = false
        letterEl.textContent = (name || "?").slice(0, 1).toUpperCase()
        letterEl.style.background = avatarBackground(name || "?")
    }
}

function pieceImg(color, symbol) {
    return `/chess-pieces/${color}-${PIECE_NAMES[symbol.toLowerCase()]}.png`
}

// Display order: dr/dc -> (file, rank) honoring the flip rule (own side bottom).
function displayOrder() {
    const flip = myColor() === "black"
    const cells = []
    for (let dr = 0; dr < 8; dr++) {
        for (let dc = 0; dc < 8; dc++) {
            const file = flip ? 7 - dc : dc
            const rank = flip ? dr : 7 - dr
            cells.push({ dr, dc, file, rank, sq: rank * 8 + file })
        }
    }
    return cells
}

function render() {
    const gate = $("gate")
    if (!state || !state.active) {
        $("root").style.display = "none"
        gate.style.display = "grid"
        gate.textContent = state
            ? "No chess game is running in this channel." + (pollError ? `\n\n${pollError}` : "")
            : "Connecting to the board..."
        return
    }
    gate.style.display = "none"
    // DUNG dung style.display = "": xoa inline thi computed fallback ve rule
    // "#root { display: none }" trong board.html -> ban co (root) an cam mai mai.
    $("root").style.display = "block"

    const map = parseFen(state.fen)
    // Optimistic: khi nuoc cua ta chua duoc server xac nhan, tu di chuyen quan trong ban co tam thoi.
    const ourUci = optimistic ? squareName(optimistic.from) + squareName(optimistic.to) : ""
    if (optimistic && state.last_move !== ourUci) {
        map[optimistic.to] = map[optimistic.from]
        map[optimistic.from] = null
    }
    const flip = myColor() === "black"
    const bottomColor = flip ? "black" : "white"

    // Player bars: bottom is always "your side", spectator sits on White.
    const bottomSeat = bottomSeatProfile(state, bottomColor, state.mode)
    const topColor = bottomColor === "white" ? "black" : "white"
    const topSeat = bottomSeatProfile(state, topColor, state.mode)
    fillBar("top", topSeat, topColor)
    fillBar("bottom", bottomSeat, bottomColor)
    slotColor = { top: topColor, bottom: bottomColor }
    updateClocks()

    let checkSq = null
    if (state.check && !state.over) {
        const kingColor = state.turn === "white"
        for (let sq = 0; sq < 64; sq++) {
            const piece = map[sq]
            if (piece && piece.symbol.toLowerCase() === "k" && piece.white === kingColor) checkSq = sq
        }
    }
    for (const cell of displayOrder()) {
        // leftmost column shows the rank digits, bottom row shows the file letters,
        // just like the rendered PNG board.
        const canMoveNow = myTurn()
        const selOk = sel !== null
        const legal = selOk ? legalTargets(sel).some(t => squareName(cell.sq) === t.to) : false
        const piece = map[cell.sq]
        const selectHere = selOk && sel === cell.sq
        const lastUci = state.last_move
        const lastFrom = lastUci ? lastUci.slice(0, 2) : null
        const lastTo = lastUci ? lastUci.slice(2, 4) : null
        const cellAlg = squareName(cell.sq)
        const inLast = lastUci && (cellAlg === lastFrom || cellAlg === lastTo)
        const dotOrRing = legal && piece ? "ring" : (legal ? "dot" : null)
        renderSquare(cell, {
            light: (cell.dr + cell.dc) % 2 === 0,
            rankLabel: cell.dc === 0 ? String(flip ? cell.rank + 1 : 8 - cell.rank) : null,
            fileLabel: cell.dr === 7 ? "abcdefgh"[cell.file] : null,
            piece, selectHere, legal, dotOrRing,
            lastMove: !selOk && !selectHere && inLast,
            check: checkSq !== null && checkSq === cell.sq,
            hintFrom: hint && hint.from === cell.sq,
            hintTo: hint && hint.to === cell.sq,
            actionable: canMoveNow && piece && (
                state.mode === "pvp"
                    ? ((state.you.role === "white") === piece.white)
                    : (piece.white === (myColor() === "white"))
            ),
        })
    }
    // Slide animation for the newest move (bot/opponent via poll, ours via apiMove commit):
    // only runs when this uci has not been animated yet. While OUR optimistic move renders,
    // state.last_move is still the opponent's PREVIOUS move (already animated) -> must skip,
    // otherwise it replays.
    if (state.last_move && !optimistic && state.last_move !== lastAnimatedUci) {
        lastAnimatedUci = state.last_move
        animateLastMove(state.last_move)
        // Quan doi thu/kenh khac vua dat xuong: chi co tran ket thuc them SFX mate, con neu an quan thi SFX capture.
        if (state.over) {
            sfx("mate")
        } else {
            const captured = Boolean(prevFen && parseFen(prevFen)[parseSquare(state.last_move.slice(2, 4))])
            sfx(captured ? "capture" : "move")
        }
    }
    renderPanel(map)
}

function bottomSeatProfile(state, color, mode) {
    const seat = color === "white" ? state.white : state.black
    const isYou = state.mode !== "pvp"
        ? (state.you.role !== "spectator" && (color === myColor()))
        : (state.you.role === color)
    return { seat, isYou, color }
}

function fillBar(slot, { seat, isYou }, color) {
    setAvatar($(`${slot}-avatar`), $(`${slot}-avatar-lg`), seat.avatar_url, seat.name)
    $(`${slot}-name`).textContent = seat.name || "?"
    let sub = ""
    if (seat.is_bot && seat.elo) sub = `Estimated ~${seat.elo} Elo`
    else if (isYou) sub = ""
    else if (state.mode !== "pvp") sub = ""
    $(`${slot}-sub`).textContent = sub
    // Ca HAI bar deu hien quan da an (cua seat do): ben trai lay cac quan mau doi
    // thieu mat tren ban. Badge +n la chenh lech dai cua seat nay so voi doi thu.
    const cap = $(slot === "bottom" ? "captured" : "captured-top")
    const map = parseFen(state.fen)
    const opposite = color === "white" ? "black" : "white"
    const mine = capturedList(map, opposite)      // pieces this seat captured
    const theirs = capturedList(map, color)       // pieces this seat lost
    const diff = mine.advantage - theirs.advantage
    cap.innerHTML = ""
    for (const capturedPiece of mine.list) {
        const img = document.createElement("img")
        img.src = pieceImg(capturedPiece.color, capturedPiece.symbol)
        cap.appendChild(img)
    }
    if (diff > 0) {
        const badge = document.createElement("span")
        badge.className = "mat-diff"
        badge.textContent = `+${diff}`
        cap.appendChild(badge)
    }
    cap.style.display = "inline-flex"
}

function renderSquare(cell, info) {
    const board = $("board")
    let div = board.querySelector(`[data-sq="${cell.sq}"]`)
    if (!div) {
        div = document.createElement("div")
        div.className = "sq"
        div.dataset.sq = cell.sq
        div.addEventListener("pointerdown", onPointerDown)
        board.appendChild(div)
    }
    // keep grid order stable without a full rebuild
    div.style.order = String(cell.dr * 8 + cell.dc)
    div.classList.toggle("light", info.light)
    div.classList.toggle("dark", !info.light)
    div.classList.toggle("hl", Boolean(info.lastMove || info.selectHere))
    div.classList.toggle("check", Boolean(info.check))
    div.classList.toggle("hint-from", Boolean(info.hintFrom))
    div.classList.toggle("hint-to", Boolean(info.hintTo))
    div.innerHTML = ""
    if (info.rankLabel) {
        addCoord(div, "rank", info.rankLabel, info.light)
    }
    if (info.fileLabel) {
        addCoord(div, "file", info.fileLabel, info.light)
    }
    const canMoveNow = myTurn()
    if (info.legal) {
        const t = document.createElement("span")
        t.className = `target ${info.dotOrRing}`
        div.appendChild(t)
    }
    if (info.piece) {
        const piece = document.createElement("div")
        piece.className = "piece"
        piece.style.backgroundImage = `url("${pieceImg(info.piece.white ? "white" : "black", info.piece.symbol)}")`
        if (info.actionable) {
            piece.dataset.dragSq = cell.sq
        }
        div.appendChild(piece)
    }
    return div
}

function addCoord(div, kind, text, light) {
    const span = document.createElement("span")
    span.className = `coord ${kind}`
    span.textContent = text
    span.style.color = light ? "#779556" : "#ebeed0"
    div.appendChild(span)
}

// Slide the piece that now stands on `uci.to` back to `uci.from`, then let a CSS
// transform transition glide it into place (applies to bot, PvP signs and our own commit).
function animateLastMove(uci) {
    const board = $("board")
    const toCell = board.querySelector(`[data-sq="${parseSquare(uci.slice(2, 4))}"]`)
    const fromCell = board.querySelector(`[data-sq="${parseSquare(uci.slice(0, 2))}"]`)
    const pieceEl = toCell && toCell.querySelector(".piece")
    if (!pieceEl || !fromCell) return
    const rectFrom = fromCell.getBoundingClientRect()
    const rectTo = toCell.getBoundingClientRect()
    const dx = rectFrom.left - rectTo.left
    const dy = rectFrom.top - rectTo.top
    pieceEl.style.transition = "none"
    pieceEl.style.transform = `translate(${dx}px, ${dy}px)`
    pieceEl.getBoundingClientRect()      // force reflow so the start position takes effect
    pieceEl.style.transition = "transform 180ms ease-out"
    pieceEl.style.transform = ""
}

function renderPanel(map) {
    const isPvp = state.mode === "pvp"
    $("panel-title").textContent = isPvp ? "🚩 PvP Game" : "💻 Play vs Arona"
    // Status line: what is happening right now.
    let status
    if (state.over) {
        status = state.result || "Game over."
    } else if (state.mode === "engine" && state.turn !== myColor() && state.you.role !== "spectator") {
        status = "Arona is thinking..."
    } else if (myTurn()) {
        status = "Your turn"
    } else {
        const seat = state.turn === "white" ? state.white : state.black
        status = `${state.turn.charAt(0).toUpperCase() + state.turn.slice(1)} to move${seat.name ? ` — ${seat.name}` : ""}`
    }
    $("status").textContent = status
    let badge = isPvp ? (state.you.role === "spectator" ? "SPECTATOR" : "PvP") : "vs Arona"
    if (state.you.role === "spectator" && state.mode === "engine") badge = "SPECTATOR"
    if (state.clock) badge += ` · ${state.clock.category} ${state.clock.label}`
    $("room").textContent = badge

    // Move list in 1. white black rows; highlight the cell of the last move.
    const moveBox = $("moves")
    moveBox.innerHTML = ""
    for (let i = 0; i < state.moves.length; i += 2) {
        const row = document.createElement("div")
        row.className = "mv-row"
        const num = document.createElement("span")
        num.className = "mv-num"
        num.textContent = `${i / 2 + 1}.`
        row.appendChild(num)
        const cellW = document.createElement("span")
        cellW.className = "mv"
        cellW.textContent = state.moves[i]
        if (i === state.moves.length - 1) cellW.classList.add("last")
        row.appendChild(cellW)
        const cellB = document.createElement("span")
        if (state.moves[i + 1]) {
            cellB.className = "mv"
            cellB.textContent = state.moves[i + 1]
            if (i + 1 === state.moves.length - 1) cellB.classList.add("last")
        } else {
            cellB.className = "mv-empty"
        }
        row.appendChild(cellB)
        moveBox.appendChild(row)
    }
    moveBox.scrollTop = moveBox.scrollHeight

    // Action row: no undo/hint in PvP, rematch once the game is over.
    const actions = $("actions")
    actions.innerHTML = ""
    appendBgmToggle(actions)
    syncEloRow()
    if (state.over) {
        if (state.you.role !== "spectator" || state.mode === "engine") {
            actions.appendChild(actionBtn("↻ New game", "secondary", apiNewGame))
        }
        return
    }
    if (state.you.role === "spectator" && isPvp) return
    if (state.mode === "engine") {
        // No take-backs in timed games (the server refuses them too).
        if (state.turn === myColor() && !busy && !state.clock) actions.appendChild(actionBtn("↩ Undo", "secondary", apiUndo))
        if (state.turn === myColor() && !busy) actions.appendChild(actionBtn("💡 Hint", "secondary", apiHint))
    }
    actions.appendChild(actionBtn("🚩 Resign", "secondary", apiResign))
}

function actionBtn(label, style, handler) {
    const btn = document.createElement("button")
    btn.className = "btn"
    btn.textContent = label
    if (style === "danger") btn.style.background = "var(--danger)"
    btn.addEventListener("click", () => { sfx("ui"); handler(btn) })      // SFX bam nut UI
    return btn
}

// Nut mo/đong kho nhạc nền; giữ selection label mới cập nhật sau mỗi lần render panel.
function appendBgmToggle(actions) {
    const btn = document.createElement("button")
    btn.id = "bgm-toggle"
    btn.className = "btn"
    btn.style.fontSize = "12px"
    btn.textContent = `🎵 ${bgmPrefs().label || "none"}`
    btn.addEventListener("click", () => {
        sfx("ui")                                     // bam nut mo/đong BGM -> click loi
        const box = $("bgm")
        box.hidden = !box.hidden
        if (!box.hidden) { loadBgmList() }
    })
    actions.appendChild(btn)
}

function toast(text, ms = 2600) {
    const t = $("toaster")
    t.textContent = text
    t.style.display = ""
    clearTimeout(toast._tm)
    toast._tm = setTimeout(() => { t.style.display = "none" }, ms)
}

function apiHeaders() {
    const auth = window.__arona && window.__arona.auth
    if (!auth) return {}
    return { "Authorization": `Bearer ${auth.token}`, "X-Channel-Id": auth.channelId }
}

async function apiGet(path) {
    const res = await fetch(path, { headers: apiHeaders(), signal: AbortSignal.timeout(15000) })
    if (!res.ok) throw new Error(await apiError(res))
    return res.json()
}

async function apiPost(path, body) {
    const data = await apiPostRaw(path, body)
    if (data && "clock" in data) syncClock(data)      // keep the countdown in step with every state reply
    return data
}

async function apiPostRaw(path, body) {
    const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...apiHeaders() },
        body: JSON.stringify(body || {}),
        signal: AbortSignal.timeout(30000),      // server co the nam lam vi tri cua quan trong "Arona is thinking"
    })
    if (!res.ok) throw new Error(await apiError(res))
    return res.json()
}

async function apiError(res) {
    try {
        const data = await res.json()
        if (data && data.error) return data.error
    } catch (ignored) { /* fall through to status text */ }
    return `Request failed (${res.status})`
}

async function apiMove(fromIdx, toIdx, promotion) {
    busy = true
    flipClockLocally()
    // Di quan NGAY TAI CHO (khong cho bot tra loi): hien truoc, hoi sau.
    // Promotion tam hien con tuong cho den khi server tra FEN moi.
    const uci = squareName(fromIdx) + squareName(toIdx)
    optimistic = { from: fromIdx, to: toIdx }
    lastAnimatedUci = uci          // echo tu server cung nuoc nay -> khong bi animate lan nua
    const capturedNow = Boolean(parseFen(state.fen)[toIdx])      // oversquare co quan truoc khi dat = nuoc an quan (khong tinh en passant)
    sel = null
    hint = null
    render()
    animateLastMove(uci)
    // SFX nuoc do PHAT LUC DOI DA THA XUONG (drag-drop: pointerup; diem dau-diem cuoi: nhan o dich):
    // thuong di -> 01 tram, an quan -> 02 trung. (Con 03 chi cho tran ket thuc.)
    sfx(capturedNow ? "capture" : "move")
    try {
        const beforeMove = state            // state truoc khi server xac nhan nuoc di
        prevFen = state.fen
        state = await apiPost("/api/chess/move", {
            from: squareName(fromIdx),
            to: squareName(toIdx),
            promotion: promotion || "",
        })
        optimistic = null
        render()
        maybeJingle(beforeMove)
        if (lastAnimatedUci === uci && state.over) {sfx("mate")}      // tran ket thuc do nuoc cua ta ->
    } catch (e) {
        optimistic = null          // that bai -> quan quay ve o cu (render lai tu FEN that)
        toast(e.message)
        render()
    } finally {
        busy = false
    }
}

async function apiResign(btn) {
    if (!confirmOnce(btn, "Resign?")) return
    busy = true
    try {
        const before = state
        state = await apiPost("/api/chess/resign")
        render()
        maybeJingle(before)          // resign -> thua ngay: phat jingle defeat nhu cac ket qua khac
    } catch (e) {
        toast(e.message)
    } finally {
        busy = false
    }
}

async function apiNewGame() {
    busy = true
    try {
        const before = state
        state = await apiPost("/api/chess/newgame", { elo: eloShown || undefined })
        render()
        if (before && before.over) {
            jinglePlayedKey = ""
            stopJingle()          // New gameanime khi van cu da ket thuc -> tat jingle ngay, khong cho poll
        }
        maybeJingle(before)
    } catch (e) {
        toast(e.message)
        busy = false
        return
    }
    busy = false
    pollOnce()
}

async function apiUndo(btn) {
    if (!confirmOnce(btn, "Take back?")) return
    busy = true
    try {
        state = await apiPost("/api/chess/undo")
        render()
    } catch (e) {
        toast(e.message)
        busy = false
        return
    }
    busy = false
    pollOnce()
}

async function apiHint() {
    try {
        const data = await apiPost("/api/chess/hint")
        const uci = data.hint || ""
        if (uci.length >= 4) {
            hint = {
                from: parseSquare(uci.slice(0, 2)),
                to: parseSquare(uci.slice(2, 4)),
                at: Date.now() + 2600,
            }
            render()
            setTimeout(() => {
                hint = null
                render()
            }, 2800)
        }
    } catch (e) {
        toast(e.message)
    }
}

function confirmOnce(btn, text) {
    if (btn.dataset.armed === "1") {
        btn.dataset.armed = ""
        return true
    }
    btn.dataset.armed = "1"
    btn.textContent = `${text} Sure?`
    setTimeout(() => {
        if (btn.isConnected && btn.dataset.armed === "1") {
            btn.dataset.armed = ""
            renderPanel(parseFen(state.fen))
        }
    }, 2800)
    return false
}

// ── selection + drag ───────────────────────────────────────────────
let drag = null

function squareFromEvent(ev) {
    for (const el of document.elementsFromPoint(ev.clientX, ev.clientY)) {
        const sq = el.closest && el.closest(".sq")
        if (sq) return Number(sq.dataset.sq)
    }
    return null
}

function onPointerDown(ev) {
    if (!state || !myTurn()) return
    const sq = squareFromEvent(ev)      // lay o tu toa do chu khong tu dataset -> keo ca vung canh quan cung duoc
    if (sq === null) return
    // Co quan dang chon: nhan vao o dich hop le -> di luon (giong click-commit cu).
    if (sel !== null && sel !== sq) {
        const legal = legalTargets(sel).find(t => t.to === squareName(sq))
        if (legal) {
            if (legal.promotion) {
                askPromotion(sq)
            } else {
                apiMove(sel, sq)
            }
            return
        }
    }
    if (!canSelect(sq)) {
        // Khong phai quan cua ta co nuoc di: bo chon quan cu (neu co) thay vi giu nguyen.
        if (sel !== null) { sel = null; render() }
        return
    }
    const wasSelected = sel === sq
    sfx("ui")      // AM KHI GRAB = cam quan len: giong y het sfx click cac nut UI
    sel = sq
    render()
    startDrag(ev, sq, wasSelected)      // luon cho keo: deselect khi tap nhe, di nuoc khi keo nhu thuong
    ev.preventDefault()
    ev.stopPropagation()
}

function canSelect(sq) {
    const map = parseFen(state.fen)
    const piece = map[sq]
    if (!piece) return false
    const mine = myColor() === "white"
    if (piece.white !== mine) return false
    const alg = squareName(sq)
    return state.legal_moves.some(uci => uci.slice(0, 2) === alg)
}

function startDrag(ev, sq, wasSelected) {
    const pieceEl = $("board").querySelector(`[data-sq="${sq}"] .piece`)
    if (!pieceEl) return
    const rect = $("board").getBoundingClientRect()
    const size = rect.width / 8
    drag = { sq, pieceEl, size }
    pieceEl.classList.add("dragging")
    applyDrag(ev, sq, pieceEl, size)
    let moved = false
    const move = (e) => { moved = true; applyDrag(e, sq, pieceEl, size) }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", (up) => {
        window.removeEventListener("pointermove", move)
        drag = null
        const target = squareFromEvent(up)
        if (!moved) {
            // Tap (khong keo): lan dau chon moi = giu selection; nhan tiep vao quan
            // da chon = bo chon.
            if (wasSelected) { sel = null }
        } else if (target !== null && target !== sq) {
            const legal = legalTargets(sq).find(t => t.to === squareName(target))
            if (legal) {
                if (legal.promotion) {
                    askPromotion(target)
                } else {
                    apiMove(sq, target)
                }
                return
            }
        }
        render()          // drop the piece back on its square (selection persists)
    }, { once: true })
}

function applyDrag(ev, sq, pieceEl, size) {
    pieceEl.style.position = "fixed"
    pieceEl.style.width = `${size}px`
    pieceEl.style.height = `${size}px`
    pieceEl.style.left = `${ev.clientX - size / 2}px`
    pieceEl.style.top = `${ev.clientY - size / 2}px`
    pieceEl.style.pointerEvents = "none"
}

function askPromotion(toIdx, colors) {
    const whitePawn = state.turn === "white"
    const box = $("promote")
    box.innerHTML = ""
    const wrap = document.createElement("div")
    wrap.style.display = "grid"
    for (const code of ["q", "r", "b", "n"]) {
        const img = document.createElement("img")
        img.src = pieceImg(whitePawn ? "white" : "black", code)
        img.title = code.toUpperCase()
        img.addEventListener("click", () => {
            box.style.display = "none"
            apiMove(sel, toIdx, code)
        })
        wrap.appendChild(img)
    }
    box.appendChild(wrap)
    box.style.display = "block"
    const rect = boardSubRect(toIdx)
    box.style.left = `${Math.max(4, rect.left)}px`
    // open below the promotion square for white, above it for black
    box.style.top = whitePawn ? `${rect.top}px` : `${rect.top - 216 + rect.height}px`
}

function boardSubRect(sq) {
    const cell = displayOrder().find(c => c.sq === sq)
    const board = $("board").getBoundingClientRect()
    const size = board.width / 8
    return { left: board.left + cell.dc * size, top: board.top + cell.dr * size, height: size }
}

// ── polling ────────────────────────────────────────────────────────
let polledOnceOk = false

async function pollOnce() {
    if (busy || stopPoll) return
    try {
        const fresh = await apiGet("/api/chess/state")
        pollError = ""
        syncClock(fresh)
        if (!state || stateKey(fresh) !== stateKey(state)) {
            prevFen = state && state.fen ? state.fen : null      // cho biet nuoc moi co an quan khong
            const before = state
            state = fresh
            render()
            if (state.active && (!before || !before.active)) ensureJingleResolved()
            maybeJingle(before)
        }
        if (!polledOnceOk) {
            polledOnceOk = true
            const root = $("root"), gate = $("gate"), boardEl = $("board")
            const cs = getComputedStyle(root)
            const check = `render check: active=${state.active}, mode=${state.mode}, root=${cs.display} h=${root.offsetHeight}, gate=${getComputedStyle(gate).display} h=${gate.offsetHeight}, squares=${boardEl ? boardEl.childElementCount : 0}, fen=${state.fen ? state.fen.split(" ")[0].slice(0, 15) : "-"}`
            const broken = !(root.offsetHeight > 0 && boardEl && boardEl.childElementCount === 64)
            if (window.__aronaPanel) {
                window.__aronaPanel.note(check)
                if (broken) {window.__aronaPanel.note("BOARD NOT VISIBLE — root hidden/empty although state ok", true)}
                else {setTimeout(() => {window.__aronaPanel.close()}, 1200)}      // board that su hien roi: an panel (chip "e" mo lai)
            }
        }
    } catch (e) {
        pollError = e && e.message ? e.message : String(e)
        if (state) {
            render()          // keep serving the last known board while retries continue
        } else {
            state = { active: false }
            render()
        }
    }
}

async function waitAuth() {
    const arona = window.__arona
    if (arona && arona.ready) {
        await arona.ready
    }
    if (!(window.__arona && window.__arona.auth)) {
        // Not inside Discord (or login failed): explain how to reach the board.
        const gate = $("gate")
        gate.style.display = "grid"
        const why = []
        if (!location.search.includes("frame_id")) why.push("not inside Discord (no frame_id in the URL)")
        if (window.__arona && window.__arona.error) why.push(`login error: ${window.__arona.error.message || window.__arona.error}`)
        if (!window.__arona) why.push("arona-boot.js did not run")
        if (window.__arona && window.__arona.reason) why.push(window.__arona.reason)
        if (window.__arona && !window.__arona.reason && !window.__arona.error && location.search.includes("frame_id")) why.push("login finished without a token (no reason recorded)")
        if (window.__arona && window.__arona.trace) why.push(`boot ${window.__arona.build} run#${window.__arona.run}: ${window.__arona.trace.join(" > ") || "(no steps ran)"}`)
        gate.textContent = "Open this board from the Interactive board button on a chess game in Discord." +
            `\n\n${why.join("\n")}\n[${BUILD}]`
        if (window.__aronaPanel) {window.__aronaPanel.note(`gate: ${why.join(" | ")}`, true)}
        return false
    }
    return true
}

(async () => {
    try {
        if (window.__aronaPanel) {window.__aronaPanel.note(`board.js loaded, auth=${window.__arona && window.__arona.auth ? "ready" : "waiting"}`)}
        if (await waitAuth()) {
            await pollOnce()
            setInterval(pollOnce, POLL_MS)
            setInterval(updateClocks, 100)
            if (window.__aronaPanel) {setTimeout(() => {window.__aronaPanel.close()}, 1200)}      // board chay: an panel, chip "e" mo lai
        }
    } catch (e) {
        // Init bi vang (vi du DOM bi trang khac nghem): gate phai noi thay vi de man xam.
        const gate = $("gate")
        gate.style.display = "grid"
        gate.textContent = `Board failed to start: ${e && e.message || e}\n[${BUILD}]`
        if (window.__aronaPanel) {window.__aronaPanel.note(`board init failed: ${e && e.message || e}`, true)}
    }
})()

window.addEventListener("error", (ev) => { stopPoll = true; })
