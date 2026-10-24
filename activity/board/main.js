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
        el.volume = level("sfx")
        el.currentTime = 0
        el.play().catch(() => {})      // autoplay bi chan thi im lang, khong vang loi
    } catch (ignored) { /* khong co Audio */ }
}

// ui-select phat MOI LAN click/cham bat ky dau tren trang (khong can trung nut nao). Dung capture de van chay
// khi phan tu ben trong goi stopPropagation (vd o co). Cac sfx("ui") le o tung nut da go bo de khong bi phat doi.
document.addEventListener("pointerdown", () => sfx("ui"), true)

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
let bgmEntries = []             // playlist tinh tu server (hien trong o chon BGM cua bang Options)
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

// Am luong: 3 kenh Master / BGM / SFX, moi kenh 0-100 + co mute. Muc hieu dung = master x kenh (0 neu mute master hoac mute kenh).
const AUDIO_KEY = "arona-audio"
let audioPrefsCache = null
function audioPrefs() {
    if (audioPrefsCache) return audioPrefsCache
    let saved = {}
    try { saved = JSON.parse(localStorage.getItem(AUDIO_KEY) || "{}") || {} } catch (ignored) { saved = {} }
    const num = (v, d) => (v === undefined || v === null || !Number.isFinite(Number(v))) ? d : Math.max(0, Math.min(100, Math.round(Number(v))))
    const mute = saved.mute || {}
    audioPrefsCache = {
        master: num(saved.master, 100),
        bgm: num(saved.bgm, num(bgmPrefs().vol, 35)),      // truoc day chi co 1 thanh volume chung -> lay lam muc BGM ban dau
        sfx: num(saved.sfx, 100),
        mute: { master: !!mute.master, bgm: !!mute.bgm, sfx: !!mute.sfx },
    }
    return audioPrefsCache
}
function audioSave() { try { localStorage.setItem(AUDIO_KEY, JSON.stringify(audioPrefs())) } catch (ignored) { /* private mode */ } }
function level(ch) {
    const p = audioPrefs()
    if (p.mute.master || p.mute[ch]) return 0
    return (p.master / 100) * (p[ch] / 100)
}
function applyAudio() {
    if (bgmAudio) { bgmAudio.volume = level("bgm") }
    if (jingleAudio) { jingleAudio.volume = level("bgm") }
    for (const el of Object.values(sfxEls)) { el.volume = level("sfx") }
    if (typeof syncOptionsUI === "function") { syncOptionsUI() }
}

function updateBgmStatus(text) { $("bgm-status").textContent = text }

function stopBgm() {
    if (bgmAudio) { try { bgmAudio.pause() } catch (ignored) { /* */ } }
    bgmAudio = null
    bgmPlayingKey = ""
    updateBgmStatus("")
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
            updateBgmStatus("")
            return
        }
        if (bgmAudio) { try { bgmAudio.pause() } catch (ignored) { /* */ } }
        bgmAudio = new Audio(res.src)
        bgmAudio.loop = true
        bgmAudio.volume = level("bgm")
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

function markPlaylist() {
    const list = $("bgm-list")
    if (!list) return
    const key = bgmPrefs().key
    for (const li of list.querySelectorAll(".o-li")) { li.classList.toggle("on", Boolean(key) && li.dataset.id === key) }
}

function fillPlaylist(entries) {
    bgmEntries = entries
    const list = $("bgm-list")
    if (!list) return
    list.innerHTML = ""
    const add = (id, text) => {
        const li = document.createElement("div")
        li.className = "o-li"
        li.dataset.id = id
        li.setAttribute("role", "option")
        li.textContent = text
        list.appendChild(li)
    }
    add("", "music: none")
    for (const entry of entries) { add(entry.id, (entry.title || entry.id).slice(0, 64)) }
    markPlaylist()
}

async function loadBgmList() {
    if (bgmOpening) return
    bgmOpening = true
    updateBgmStatus("loading playlist...")
    try {
        const payload = await apiGet("/api/chess/bgm/list")
        if (payload.error) { updateBgmStatus(payload.error) }
        else { fillPlaylist(payload.entries || []); updateBgmStatus("") }
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
        jingleAudio.volume = level("bgm")
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

// Real match duration and result overlays (name/time/analysis are passed to the iframe pages).
let matchStartedAt = 0
let pendingAnalysis = ""

function noteMatchStart() {
    if (!state) return
    if (!state.active) { matchStartedAt = 0; return }
    if (!state.over && !matchStartedAt) matchStartedAt = Date.now()
}

function gameDurationText() {
    if (!state) return ""
    let secs = null
    const clk = state.clock
    if (clk && clk.started) {
        const base = parseFloat(String(clk.label || "").split("+")[0])
        const inc = Number(clk.increment || 0)
        const added = (state.moves ? state.moves.length : 0) * inc
        const left = (Number(clk.white_ms || 0) + Number(clk.black_ms || 0)) / 1000
        if (isFinite(base)) secs = base * 120 + added - left
    } else if (matchStartedAt) {
        secs = (Date.now() - matchStartedAt) / 1000
    }
    if (secs === null || !isFinite(secs) || secs < 0) return ""
    secs = Math.round(secs)
    return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`
}

function seatName(color) {
    if (!state) return ""
    const seat = color === "white" ? state.white : state.black
    return (seat && seat.name) || ""
}

async function loadAnalysisText() {
    try {
        const data = await apiGet("/api/chess/analysis", 60000)
        if (data && data.error) return `Analysis unavailable: ${data.error}`
        if (data && data.text) return data.text
        const lines = (data && data.lines) || []
        return lines.length ? lines.map((line) => line.text || line).join("\n") : "No major mistakes found."
    } catch (e) {
        return "Analysis unavailable: " + (e && e.message ? e.message : "request failed")
    }
}

function pushAnalysisToOverlay(text) {
    const frame = $("overlay-frame")
    if (!frame) return false
    try {
        const doc = frame.contentDocument
        if (!doc || !doc.body) return false
        const view = doc.defaultView
        if (view && typeof view.setAnalysis === "function") view.setAnalysis(text)
        else {
            const box = doc.getElementById("repBody")
            if (!box) return false
            box.textContent = text
        }
        return true
    } catch (e) {
        return false
    }
}

function showResultOverlay(kind) {
    if (kind === "victory") {
        showOverlay("/overlay/victory/victory.html", {
            name: seatName(state.winner),
            time: gameDurationText(),
            autoHideMs: 12000,
        })
        return
    }
    const loser = state.winner === "white" ? "black" : "white"
    showOverlay("/overlay/defeat/defeat.html", {
        name: seatName(loser),
        time: gameDurationText(),
        autoHideMs: 0,
    })
    loadAnalysisText().then((text) => {
        if (!overlayShown) return
        pendingAnalysis = text
        if (pushAnalysisToOverlay(text)) pendingAnalysis = ""
    })
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
    if (state.winner === myColor()) { showResultOverlay("victory"); playJingle("win") }
    else { showResultOverlay("defeat"); playJingle("defeat") }
}

// BGM UI (phan tu dem trong board.html): listeners dem bam mot lan khi module chay (dom co san roi).
(function initBgm() {
    const field = $("bgm-custom"), list = $("bgm-list")
    const openList = () => { list.hidden = false; if (!bgmEntries.length) { loadBgmList() } else { markPlaylist() } }
    field.addEventListener("focus", openList)
    field.addEventListener("click", openList)
    field.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
            const url = field.value.trim()
            ev.preventDefault()
            if (!url) return
            closeBgmList()
            bgmPlay({ custom: url, label: "custom" })
        } else if (ev.key === "Escape" && !list.hidden) {
            closeBgmList()
            ev.stopPropagation()
        }
    })
    $("bgm-caret").addEventListener("click", () => { if (list.hidden) { openList() } else { closeBgmList() } })
    $("bgm-custom-btn").addEventListener("click", () => {
        const url = field.value.trim()
        if (!url) return
        closeBgmList()
        bgmPlay({ custom: url, label: "custom" })
    })
    $("bgm-stop").addEventListener("click", () => { stopBgm(); closeBgmList() })
    list.addEventListener("click", (ev) => {
        const li = ev.target.closest(".o-li")
        if (!li) return
        closeBgmList()
        if (li.dataset.id === "") { stopBgm() } else { bgmPlay({ key: li.dataset.id }) }
    })
    // Reload trang + da co BGM luu truoc -> bat sau lan tuong tac dau (autoplay chinh sach cua trinh duyet).
    document.addEventListener("pointerdown", () => {
        if (bgmAudio) return
        const p = bgmPrefs()
        if (p.key) { bgmPlay({ key: p.key, label: p.label }) }
        else if (p.custom) { bgmPlay({ custom: p.custom, label: p.label }) }
    }, { once: true })
})()

function closeBgmList() { const list = $("bgm-list"); if (list) { list.hidden = true } }

// ── thanh trượt Elo cho PvE: keo thay do manh cua Arona, gui len server luc nhan New game ──
const ELO_MIN = 400, ELO_MAX = 3190
let eloShown = null          // gia tri slider hien tai (null = chua keo lay, theo state.elo)

function eloSaved() {
    const v = parseInt(localStorage.getItem("arona-elo") || "", 10)
    return Number.isFinite(v) ? Math.max(ELO_MIN, Math.min(ELO_MAX, v)) : null
}

// Keo thanh truot -> dong "Estimated ~N Elo" o thanh ten cua Arona doi theo ngay (khong cho toi lan render sau).
function updateBotEloLabel() {
    if (!state || !eloShown) return
    for (const slot of ["top", "bottom"]) {
        const sub = $(`${slot}-sub`)
        if (sub && sub.textContent.startsWith("Estimated")) sub.textContent = `Estimated ~${eloShown} Elo`
    }
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
    updateBotEloLabel()
}

;(function initElo() {
    const slider = $("elo-slider")
    let lastUIClick = 0
    slider.addEventListener("input", (ev) => {
        // Chi phat am 1 lan luc nhan chuot xuong (listener pointerdown toan cuc), keo thanh truot thi im lang.
        eloShown = Math.max(ELO_MIN, Math.min(ELO_MAX, Number(ev.target.value) || 1500))
        $("elo-label").textContent = `⚡ Arona ~${eloShown} elo`
        updateBotEloLabel()
        localStorage.setItem("arona-elo", String(eloShown))
    })
})()

// ── overlay cinematic (start / victory / defeat) ─────────────────────────
let overlayShown = false
let overlayTimer = null

function showOverlay(src, opts = {}) {
    if (overlayShown) return
    const ov = $("overlay"), frame = $("overlay-frame"), skip = $("overlay-skip")
    if (!ov) return
    const params = new URLSearchParams()
    for (const key of ["name", "time", "analysis"]) {
        if (opts[key]) params.set(key, opts[key])
    }
    if (opts.report) params.set("report", "1")
    params.set("v", "13")      // cache-bust trang overlay (Discord cache ca ban CSS rong cu)
    frame.src = params.toString() ? `${src}?${params.toString()}` : src
    ov.hidden = false
    overlayShown = true
    try {
        frame.onload = () => {
            try {
                const d = frame.contentDocument
                const c = d && d.getElementById("confirm")
                if (c) { c.onclick = () => { hideOverlay() } }
            } catch (e) {}
            if (pendingAnalysis && pushAnalysisToOverlay(pendingAnalysis)) pendingAnalysis = ""
        }
    } catch (e) {}
    skip.style.display = ""
    skip.onclick = () => hideOverlay()
    if (overlayTimer) { clearTimeout(overlayTimer); overlayTimer = null }
    // auto-hide only when a duration is given; result screens with analysis stay until dismissed
    const autoHideMs = opts.autoHideMs === undefined ? 12000 : opts.autoHideMs
    if (autoHideMs > 0) {
        overlayTimer = setTimeout(() => { if (overlayShown) { hideOverlay() } }, autoHideMs)
    }
}

function hideOverlay() {
    const ov = $("overlay"), frame = $("overlay-frame"), skip = $("overlay-skip")
    if (!ov) return
    if (overlayTimer) { clearTimeout(overlayTimer); overlayTimer = null }
    ov.hidden = true
    frame.src = "about:blank"
    overlayShown = false
    skip.style.display = "none"
}

// ── chuyen UI giua Classic va Blue Archive (light/dak con placeholder) ────
const THEMES = ["classic", "ba-light", "ba-dark"]
const THEME_LABELS = { "classic": "Classic", "ba-light": "BA Light", "ba-dark": "BA Dark" }
let theme = localStorage.getItem("arona-theme")
if (!THEMES.includes(theme)) theme = "classic"

function isBA() { return theme !== "classic" }

function applyTheme() {
    const root = $("root")
    if (root) { root.dataset.theme = theme }
    document.documentElement.dataset.theme = theme      // nen trang cua <html> doi theo
    if (typeof syncThemeUI === "function") { syncThemeUI() }
}

function setTheme(next) {
    if (!THEMES.includes(next) || next === theme) return
    theme = next
    try { localStorage.setItem("arona-theme", theme) } catch (ignored) { /* private mode */ }
    applyTheme()
    if (state && state.active) { renderPanel(parseFen(state.fen)) }      // tieu de / nut / lich su nuoc di doi kieu theo theme
}

// ── Bang Options (nut banh rang goc phai tren) — thiet ke trong temp/UI.clip ──
// Stage co dinh 1356x705 (don vi thiet ke); chi scale deu bang --s cho vua man hinh -> ti le khong doi du man hinh ti le nao.
// Hai trang Volume / Theme (tab ben trai) chi mo MOT trang moi luc; phan con lai cua man hinh bi lam toi (scrim).
const OPT_W = 1356, OPT_H = 705
const THUMB_MIN = 545, THUMB_MAX = 957      // toa do trai cua thumb o 0% / 100% (don vi stage)
const TRACK_L = 559, TRACK_R = 971          // tam thumb o 0% / 100%
let optPage = "volume"

function optionsOpen() { const box = $("options"); return Boolean(box) && !box.hidden }

function fitOptions() {
    const box = $("options")
    if (!box || box.hidden) return
    const s = Math.min(innerWidth * 0.96 / OPT_W, innerHeight * 0.94 / OPT_H, 1.5)
    box.style.setProperty("--s", String(s))
}

function showOptPage(page) {
    optPage = page === "theme" ? "theme" : "volume"
    for (const p of ["volume", "theme"]) {
        $(`page-${p}`).hidden = p !== optPage
        const tab = $(`tab-${p}`)
        tab.classList.toggle("sel", p === optPage)
        tab.setAttribute("aria-selected", p === optPage ? "true" : "false")
    }
    closeBgmList()
    if (optPage === "volume" && !bgmEntries.length) { loadBgmList() }
}

function openOptions() {
    const box = $("options")
    if (!box) return
    box.hidden = false
    fitOptions()
    showOptPage(optPage)
    syncOptionsUI()
    syncThemeUI()
}

function closeOptions() {
    const box = $("options")
    if (!box || box.hidden) return
    closeBgmList()
    box.hidden = true
}

function syncOptionsUI() {
    const p = audioPrefs()
    for (const ch of ["master", "bgm", "sfx"]) {
        const gray = p.mute[ch] || (ch !== "master" && p.mute.master)      // mute master = tat tieng ca 2 kenh con
        const thumb = document.querySelector(`.o-thumb[data-ch="${ch}"]`)
        if (thumb) {
            thumb.style.left = `${Math.round(THUMB_MIN + (THUMB_MAX - THUMB_MIN) * p[ch] / 100)}px`
            thumb.classList.toggle("muted", gray)
        }
        const icons = document.querySelector(`.o-icons[data-ch="${ch}"]`)
        if (icons) { icons.classList.toggle("muted", gray) }
        const tick = document.querySelector(`.o-tick[data-ch="${ch}"]`)
        if (tick) { tick.classList.toggle("on", p.mute[ch]) }
        const track = document.querySelector(`.o-track[data-ch="${ch}"]`)
        if (track) { track.setAttribute("aria-valuenow", String(p[ch])) }
        const chk = document.querySelector(`.o-chk[data-ch="${ch}"]`)
        if (chk) { chk.setAttribute("aria-checked", p.mute[ch] ? "true" : "false") }
    }
}

function syncThemeUI() {
    for (const opt of document.querySelectorAll(".o-opt")) {
        const on = opt.dataset.theme === theme
        opt.classList.toggle("on", on)
        opt.setAttribute("aria-checked", on ? "true" : "false")
    }
}

function setLevel(ch, value) {
    const v = Math.max(0, Math.min(100, Math.round(value)))
    const p = audioPrefs()
    if (p[ch] === v) return
    p[ch] = v
    audioSave()
    applyAudio()
}

(function initOptions() {
    const btn = $("options-btn")
    if (btn) { btn.addEventListener("click", openOptions) }
    $("opt-close").addEventListener("click", closeOptions)
    $("opt-scrim").addEventListener("click", closeOptions)
    $("tab-volume").addEventListener("click", () => showOptPage("volume"))
    $("tab-theme").addEventListener("click", () => showOptPage("theme"))
    for (const opt of document.querySelectorAll(".o-opt")) { opt.addEventListener("click", () => setTheme(opt.dataset.theme)) }
    for (const chk of document.querySelectorAll(".o-chk")) {
        chk.addEventListener("click", () => {
            const p = audioPrefs()
            p.mute[chk.dataset.ch] = !p.mute[chk.dataset.ch]
            audioSave()
            applyAudio()
        })
    }
    // Keo thanh truot: doi toa do chuot -> toa do stage (stage dang bi scale), thumb nhay toi ngon tro.
    const stage = $("opt-stage")
    const valueAt = (clientX) => {
        const r = stage.getBoundingClientRect()
        const x = (clientX - r.left) / (r.width / OPT_W)
        return (x - TRACK_L) / (TRACK_R - TRACK_L) * 100
    }
    for (const track of document.querySelectorAll(".o-track")) {
        const ch = track.dataset.ch
        let dragging = false
        track.addEventListener("pointerdown", (ev) => {
            dragging = true
            try { track.setPointerCapture(ev.pointerId) } catch (ignored) { /* */ }
            setLevel(ch, valueAt(ev.clientX))
            ev.preventDefault()
        })
        track.addEventListener("pointermove", (ev) => { if (dragging) { setLevel(ch, valueAt(ev.clientX)) } })
        const end = () => { dragging = false }
        track.addEventListener("pointerup", end)
        track.addEventListener("pointercancel", end)
        track.addEventListener("keydown", (ev) => {
            const cur = audioPrefs()[ch]
            const step = { ArrowLeft: -5, ArrowDown: -5, ArrowRight: 5, ArrowUp: 5 }[ev.key]
            if (step !== undefined) { setLevel(ch, cur + step); ev.preventDefault() }
            else if (ev.key === "Home") { setLevel(ch, 0); ev.preventDefault() }
            else if (ev.key === "End") { setLevel(ch, 100); ev.preventDefault() }
        })
    }
    // Bam ra ngoai o chon BGM thi dong danh sach.
    stage.addEventListener("pointerdown", (ev) => { if (!ev.target.closest(".o-field, .o-list")) { closeBgmList() } })
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && optionsOpen()) { closeOptions() } })
    window.addEventListener("resize", fitOptions)
    window.addEventListener("orientationchange", fitOptions)
    if (window.visualViewport) { window.visualViewport.addEventListener("resize", fitOptions) }
    // Su kien resize cua mot so trinh duyet (WebKit) den tre / khong den khi xoay man hinh -> theo doi them kich thuoc cua chinh lop #options (fixed, luon bang man hinh).
    if (typeof ResizeObserver === "function") { new ResizeObserver(() => fitOptions()).observe($("options")) }
})()

applyTheme()
applyAudio()

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

// Hien loading.html trong #gate. Khong dung lai iframe dang co (tranh reload moi lan render khi poll loi).
function showLoading() {
    const gate = $("gate")
    gate.style.display = "grid"
    gate.style.padding = "0"
    const optBtn = $("options-btn")
    if (optBtn) optBtn.hidden = true      // nut Options chi hien khi ban co da len
    closeOptions()
    if (!gate.querySelector("iframe")) {
        gate.innerHTML = '<iframe src="/loading/loading.html?v=13" style="position:absolute;inset:0;width:100%;height:100%;display:block;border:none;background:#000;" allow="autoplay"></iframe>'
    }
}

function render() {
    const gate = $("gate")
    if (!state || !state.active) {
        $("root").style.display = "none"
        showLoading()
        return
    }
    gate.style.display = "none"
    const optionsBtn = $("options-btn")
    if (optionsBtn) optionsBtn.hidden = false
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
    if (seat.is_bot && seat.elo) sub = `Estimated ~${eloShown || eloSaved() || seat.elo} Elo`      // theo thanh truot Elo
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

// ── Lich su nuoc di: Classic = bang 1. e4 e5 nhu chess.com; Blue Archive = khung chat (doi thu trai, minh phai) ──
function renderMoves() {
    const moveBox = $("moves")
    moveBox.innerHTML = ""
    moveBox.classList.toggle("chat", isBA())
    if (isBA()) { renderMovesChat(moveBox) } else { renderMovesTable(moveBox) }
    moveBox.scrollTop = moveBox.scrollHeight
    // panel con nut/thanh Elo phia duoi chua dung xong luc nay -> o cao that cua danh sach doi, cuon lai sau khi layout xong
    requestAnimationFrame(() => { moveBox.scrollTop = moveBox.scrollHeight })
}

function renderMovesTable(moveBox) {
    // Move list in 1. white black rows; highlight the cell of the last move.
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
}

function moveText(i, san) {
    const n = Math.floor(i / 2) + 1
    return i % 2 === 0 ? `${n}. ${san}` : `${n}\u2026 ${san}`
}

function chatAvatar(seat) {
    const name = seat.name || "?"
    if (seat.avatar_url) {
        const img = document.createElement("img")
        img.className = "msg-avatar"
        img.alt = ""
        img.src = seat.avatar_url
        return img
    }
    const el = document.createElement("span")
    el.className = "msg-avatar letter"
    el.textContent = name.slice(0, 1).toUpperCase()
    el.style.background = avatarBackground(name)
    return el
}

function renderMovesChat(box) {
    // Ben "minh" (duoi ban co) = bubble xanh duong ben phai, khong avatar; doi thu = avatar + ten + bubble xam xanh ben trai.
    const bottomColor = myColor() === "black" ? "black" : "white"
    let cur = null
    state.moves.forEach((san, i) => {
        const color = i % 2 === 0 ? "white" : "black"
        const mine = color === bottomColor
        let fresh = false
        if (!cur || cur.color !== color) {
            const seat = (color === "white" ? state.white : state.black) || {}
            const msg = document.createElement("div")
            msg.className = `msg ${mine ? "right" : "left"}`
            if (!mine) msg.appendChild(chatAvatar(seat))
            const col = document.createElement("div")
            col.className = "msg-col"
            if (!mine) {
                const nm = document.createElement("div")
                nm.className = "msg-name"
                nm.textContent = seat.name || "?"
                col.appendChild(nm)
            }
            msg.appendChild(col)
            box.appendChild(msg)
            cur = { color, col }
            fresh = true
        }
        const bubble = document.createElement("div")
        bubble.className = "bubble"
        if (fresh) bubble.classList.add("first")
        bubble.textContent = moveText(i, san)
        cur.col.appendChild(bubble)
    })
}

function renderPanel(map) {
    const isPvp = state.mode === "pvp"
    $("panel-title").textContent = isBA() ? (isPvp ? "PvP" : "Play with Arona") : (isPvp ? "🚩 PvP Game" : "💻 Play vs Arona")
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

    renderMoves()

    // Action row: no undo/hint in PvP, rematch once the game is over.
    const actions = $("actions")
    actions.innerHTML = ""
    syncEloRow()
    applyTheme()
    if (state.over) {
        if (state.you.role !== "spectator" || state.mode === "engine") {
            actions.appendChild(actionBtn("↻ New game", "secondary", apiNewGame))
        }
        return
    }
    if (state.you.role === "spectator" && isPvp) return
    if (state.mode === "engine") {
        // No take-backs in timed games (the server refuses them too).
        const undoBtn = (state.turn === myColor() && !busy && !state.clock) ? actionBtn("↩ Undo", "secondary", apiUndo) : null
        const hintBtn = (state.turn === myColor() && !busy) ? actionBtn("💡 Hint", "secondary", apiHint) : null
        for (const b of (isBA() ? [hintBtn, undoBtn] : [undoBtn, hintBtn])) { if (b) actions.appendChild(b) }
    }
    actions.appendChild(actionBtn("🚩 Resign", "secondary", apiResign))
}

function actionBtn(label, style, handler) {
    const btn = document.createElement("button")
    btn.className = "btn"
    btn.textContent = isBA() ? label.replace(/^[^\p{L}\p{N}]+/u, "") : label      // BA: nut chu thuong (Hint / Undo / Resign), khong emoji
    if (style === "danger") btn.style.background = "var(--danger)"
    btn.addEventListener("click", () => { handler(btn) })
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

async function apiGet(path, timeoutMs = 15000) {
    const res = await fetch(path, { headers: apiHeaders(), signal: AbortSignal.timeout(timeoutMs) })
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
        noteMatchStart()
        render()
        if (before && before.over) {
            jinglePlayedKey = ""
            stopJingle()          // New gameanime khi van cu da ket thuc -> tat jingle ngay, khong cho poll
        }
        maybeJingle(before)
        if (state && state.active && !state.over) {
            if (overlayShown) hideOverlay()
            showOverlay("/overlay/start/start.html", { autoHideMs: 3200 })      // opening title card on New Game
        }
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
            noteMatchStart()
            render()
            if (state.active && (!before || !before.active)) {
                ensureJingleResolved()
                showOverlay("/overlay/start/start.html", { autoHideMs: 3200 })      // opening title card
            }
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
        gate.style.padding = "24px"
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
        gate.style.padding = "24px"
        gate.textContent = `Board failed to start: ${e && e.message || e}\n[${BUILD}]`
        if (window.__aronaPanel) {window.__aronaPanel.note(`board init failed: ${e && e.message || e}`, true)}
    }
})()

window.addEventListener("error", (ev) => { stopPoll = true; })

