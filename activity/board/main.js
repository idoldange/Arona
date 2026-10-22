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
let stopPoll = false
let clockSnap = null        // latest clock snapshot + receipt time; the countdown runs locally between polls
let slotColor = { top: "black", bottom: "white" }   // which side's clock each player bar shows
let lastZeroPoll = 0

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
        gate.textContent = state ? "No chess game is running in this channel." : "Connecting to the board..."
        return
    }
    gate.style.display = "none"
    $("root").style.display = ""

    const map = parseFen(state.fen)
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
    // Captured pieces are only shown for your own bar, never for the opponent's.
    const cap = $("captured")
    if (slot === "bottom") {
        const map = parseFen(state.fen)
        const opposite = color === "white" ? "black" : "white"
        const mine = capturedList(map, opposite)      // pieces you captured
        const theirs = capturedList(map, color)       // pieces you lost
        const diff = mine.advantage - theirs.advantage
        cap.innerHTML = ""
        for (const lost of mine.list) {
            const img = document.createElement("img")
            img.src = pieceImg(lost.color, lost.symbol)
            cap.appendChild(img)
        }
        if (diff > 0) {
            const badge = document.createElement("span")
            badge.className = "mat-diff"
            badge.textContent = `+${diff}`
            cap.appendChild(badge)
        }
        cap.style.display = "inline-flex"
    } else {
        cap.style.display = "none"
    }
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
    actions.appendChild(actionBtn("🚩 Resign", "danger", apiResign))
}

function actionBtn(label, style, handler) {
    const btn = document.createElement("button")
    btn.className = "btn"
    btn.textContent = label
    if (style === "danger") btn.style.background = "var(--danger)"
    btn.addEventListener("click", () => handler(btn))
    return btn
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
    const res = await fetch(path, { headers: apiHeaders() })
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
    try {
        state = await apiPost("/api/chess/move", {
            from: squareName(fromIdx),
            to: squareName(toIdx),
            promotion: promotion || "",
        })
        sel = null
        hint = null
        render()
    } catch (e) {
        toast(e.message)
        sel = null
        render()
    } finally {
        busy = false
    }
}

async function apiResign(btn) {
    if (!confirmOnce(btn, "Resign?")) return
    busy = true
    try {
        state = await apiPost("/api/chess/resign")
        render()
    } catch (e) {
        toast(e.message)
    } finally {
        busy = false
    }
}

async function apiNewGame() {
    busy = true
    try {
        state = await apiPost("/api/chess/newgame")
        render()
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
    const dragSq = ev.target && ev.target.dataset ? ev.target.dataset.dragSq : null
    let sq = dragSq !== undefined ? Number(dragSq) : NaN
    if (Number.isNaN(sq)) sq = squareFromEvent(ev)
    if (sq === null || Number.isNaN(sq)) return
    if (!state || !myTurn()) return
    // Clicking a highlighted destination with a piece selected commits the move.
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
    if (!canSelect(sq)) return
    if (sel === sq) {
        sel = null
        render()
        return
    }
    sel = sq
    render()
    if (dragSq !== undefined) startDrag(ev, sq)
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

function startDrag(ev, sq) {
    const pieceEl = $("board").querySelector(`[data-sq="${sq}"] .piece`)
    if (!pieceEl) return
    const rect = $("board").getBoundingClientRect()
    const size = rect.width / 8
    drag = { sq, pieceEl, size }
    pieceEl.classList.add("dragging")
    applyDrag(ev, sq, pieceEl, size)
    const move = (e) => applyDrag(e, sq, pieceEl, size)
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", (up) => {
        window.removeEventListener("pointermove", move)
        drag = null
        const target = squareFromEvent(up)
        if (target !== null && target !== sq) {
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
async function pollOnce() {
    if (busy || stopPoll) return
    try {
        const fresh = await apiGet("/api/chess/state")
        syncClock(fresh)
        if (!state || stateKey(fresh) !== stateKey(state)) {
            state = fresh
            render()
        }
    } catch (e) {
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
        gate.textContent = "Open this board from the Interactive board button on a chess game in Discord."
        return false
    }
    return true
}

(async () => {
    if (await waitAuth()) {
        await pollOnce()
        setInterval(pollOnce, POLL_MS)
        setInterval(updateClocks, 100)
    }
})()

window.addEventListener("error", (ev) => { stopPoll = true; })
