// Chay som trong Activity: goi sdk.ready() de Discord tat man hinh loading, roi dang nhap de app co token goi API cua bot.
// Ket qua: window.__arona = {auth: {baseUrl, token, channelId} | null, ready: Promise}; sau khi dang nhap xong phat su kien "arona-auth".
import {DiscordSDK} from "@discord/embedded-app-sdk"

const query = new URLSearchParams(location.search)
const state = {auth: null, user: null, error: null, ready: Promise.resolve(null), trace: [], build: "boot-5"}
window.__aronaRuns = (window.__aronaRuns || 0) + 1      // >1 means this script executed twice on the page
state.run = window.__aronaRuns
window.__arona = state

// Ghi tung buoc login len panel debug cua early.js: luc dang "Loading..." tuong
// khong con roi vao trang xam hoac treo o buoc nao, man hinh luon ke lai story.
function push(msg) {
    state.trace.push(msg)
    const panel = window.__aronaPanel
    if (panel) {panel.note(`boot-5 run#${state.run}: ${msg}`)}
}
push("boot init")
const WIKI_URL = "https://bluearchive.wiki/wiki/Main_Page"

// Activity mo truc tiep (App Launcher/Entry Point) khong qua nut Open DAW -> khong co
// session, nen hien trang Blue Archive wiki thay vi mot DAW trong. Nen den chu trang,
// it nhat css: wiki khong nhung duoc iframe (X-Frame-Options: SAMEORIGIN) nen chi de link.
function showWiki() {
    if (document.getElementById("board")) {return}      // trang ban co tu hien ly do tren gate, khong de wiki che
    const cover = document.createElement("div")
    cover.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:#000;color:#fff;" +
        "display:grid;place-items:center"
    const link = document.createElement("a")
    link.href = WIKI_URL
    link.target = "_blank"
    link.rel = "noreferrer"
    link.textContent = WIKI_URL
    link.style.color = "#fff"
    cover.appendChild(link)
    ;(document.body || document.documentElement).appendChild(cover)
}

// Lop phu "Loading..." (cung mau nen ban co) tu luc mo Activity den khi biet se vao DAW / ban co / wiki,
// de DAW khong lo ra trong luc dang nhap + kiem tra session. Ban co: giu nguyen cho den khi chuyen trang.
function showLoading() {
    const cover = document.createElement("div")
    cover.id = "arona-loading"
    cover.style.cssText = "position:fixed;inset:0;z-index:2147483646;background:#262421;color:#9c9a97;" +
        "display:grid;place-items:center;font:14px 'Segoe UI',system-ui,sans-serif"
    cover.textContent = "Loading..."
    ;(document.body || document.documentElement).appendChild(cover)
}

function hideLoading() {
    const cover = document.getElementById("arona-loading")
    if (cover) {cover.remove()}
}

// Trong Discord: neu app bi trang man hinh thi hien loi ngay tren man hinh (loi JS, bi CSP chan, luu tru khong dung duoc).
// early.js da tao panel (window.__aronaPanel) neu tag board.html nam moi -> bo qua; block nay chi phuc trang cu.
if (query.has("frame_id") && !window.__aronaPanel) {
    const panel = document.createElement("pre")
    panel.id = "arona-debug"
    panel.style.cssText = "position:fixed;left:0;right:0;bottom:0;max-height:45vh;overflow:auto;margin:0;padding:6px 8px;" +
        "background:rgba(20,21,26,.94);color:#d8dae0;font:11px/1.4 Consolas,monospace;z-index:2147483647;white-space:pre-wrap"
    panel.hidden = true
    panel.style.display = "none"                  // [hidden] mot so CSS cua app bi override -> an han
    panel.onclick = () => {panel.hidden = true; panel.style.display = "none"}
    // Chi hien khi co loi that su; khong hien cac dong "ok ..." luc khoi dong.
    const add = (text, bad) => {
        const line = document.createElement("div")
        line.style.color = "#ff7b72"
        line.textContent = text
        panel.appendChild(line)
        if (bad) {panel.hidden = false; panel.style.display = "block"}
        if (!panel.isConnected) {(document.body || document.documentElement).appendChild(panel)}
    }
    window.addEventListener("error", event => add(`error: ${event.message} (${(event.filename || "").split("/").pop()}:${event.lineno})`, true))
    window.addEventListener("unhandledrejection", event => add(`rejection: ${event.reason && event.reason.message || event.reason}`, true))
    document.addEventListener("securitypolicyviolation", event => add(`CSP blocked ${event.violatedDirective}: ${event.blockedURI}`, true))
}

if (query.has("frame_id")) {
    showLoading()
    state.ready = (async () => {
        const config = await fetch("/api/config").then(response => response.json())
        const sdk = new DiscordSDK(config.client_id)
        await sdk.ready()
        push("sdk.ready")
        const {code} = await sdk.commands.authorize({
            client_id: config.client_id, response_type: "code", state: "", prompt: "none", scope: ["identify"]
        })
        push("authorized")
        const tokenResponse = await fetch("/api/token", {
            method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code})
        })
        if (!tokenResponse.ok) {
            const detail = await tokenResponse.json().then(body => body && body.error || "", () => "")
            throw new Error(`Token exchange failed (${tokenResponse.status})${detail ? `: ${detail}` : ""}`)
        }
        const {access_token} = await tokenResponse.json()
        push("token ok")
        const authenticated = await sdk.commands.authenticate({access_token})
        push(`authenticated, channel=${sdk.channelId}`)
        if (sdk.channelId === null) {
            state.reason = "sdk.channelId is null (activity not started from a channel)"
            hideLoading()
            showWiki()
            return null
        }
        // Chi vao DAW khi kenh da co session (bam Open DAW). Mo tu App Launcher/Entry Point
        // thi khong co session -> hien wiki. Loi mang/route cu -> coi nhu binh thuong.
        try {
            const check = await fetch("/api/session/check", {
                headers: {"X-Channel-Id": sdk.channelId},
                signal: AbortSignal.timeout(2000)
            })
            if (check.ok) {
                const session = await check.json()
                push(`session=${JSON.stringify(session)}`)
                if (session.exists === false) {
                    state.reason = `no active session for channel ${sdk.channelId} (press the Interactive board button again)`
                    hideLoading()
                    showWiki()
                    return null
                }
                // Chess session (Interactive board button on a chess game) -> route to
                // the chess board page instead of the DAW. The query string is kept so
                // the Discord Embedded App SDK login still works on the new page.
                // (Already on the board page, e.g. served straight for "/" or after the redirect: stay, or it reloads forever.)
                if (session.kind === "chess" && !document.getElementById("board")) {
                    location.replace("/board.html" + location.search)
                    return null
                }
            }
        } catch (error) {
            console.warn("[arona] session check failed", error)
        }
        hideLoading()
        push("auth set")
        state.user = authenticated.user ?? null      // {id, username, global_name, avatar}: tai khoan Discord thay cho he thong tai khoan cua Signal
        state.auth = {baseUrl: "", token: access_token, channelId: sdk.channelId}
        if (window.__aronaPanel) {setTimeout(() => {window.__aronaPanel.close()}, 1500)}      // thanh cong -> an panel (chip de mo lai)
        window.dispatchEvent(new CustomEvent("arona-auth", {detail: state.auth}))
        return state.auth
    })().catch(error => {
        push(`failed: ${error && error.message || error}`)
        if (window.__aronaPanel) {window.__aronaPanel.note(`boot-5 error: ${error && error.message || error}`, true)}
        hideLoading()
        state.error = error
        console.warn("[arona] Discord login failed", error)
        window.dispatchEvent(new CustomEvent("arona-auth-error", {detail: error}))
        return null
    })
}
