// Chay som trong Activity: goi sdk.ready() de Discord tat man hinh loading, roi dang nhap de app co token goi API cua bot.
// Ket qua: window.__arona = {auth: {baseUrl, token, channelId} | null, ready: Promise}; sau khi dang nhap xong phat su kien "arona-auth".
import {DiscordSDK} from "@discord/embedded-app-sdk"

const query = new URLSearchParams(location.search)
const state = {auth: null, user: null, error: null, ready: Promise.resolve(null)}
window.__arona = state
const WIKI_URL = "https://bluearchive.wiki/wiki/Main_Page"

// Activity mo truc tiep (App Launcher/Entry Point) khong qua nut Open DAW -> khong co
// session, nen hien trang Blue Archive wiki thay vi mot DAW trong. Nen den chu trang,
// it nhat css: wiki khong nhung duoc iframe (X-Frame-Options: SAMEORIGIN) nen chi de link.
function showWiki() {
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

// Trong Discord: neu app bi trang man hinh thi hien loi ngay tren man hinh (loi JS, bi CSP chan, luu tru khong dung duoc).
if (query.has("frame_id")) {
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
    state.ready = (async () => {
        const config = await fetch("/api/config").then(response => response.json())
        const sdk = new DiscordSDK(config.client_id)
        await sdk.ready()
        const {code} = await sdk.commands.authorize({
            client_id: config.client_id, response_type: "code", state: "", prompt: "none", scope: ["identify"]
        })
        const tokenResponse = await fetch("/api/token", {
            method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code})
        })
        if (!tokenResponse.ok) {throw new Error(`Token exchange failed (${tokenResponse.status})`)}
        const {access_token} = await tokenResponse.json()
        const authenticated = await sdk.commands.authenticate({access_token})
        if (sdk.channelId === null) {
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
                if (session.exists === false) {
                    showWiki()
                    return null
                }
                // Chess session (Interactive board button on a chess game) -> route to
                // the chess board page instead of the DAW. The query string is kept so
                // the Discord Embedded App SDK login still works on the new page.
                if (session.kind === "chess") {
                    location.replace("/board.html" + location.search)
                    return null
                }
            }
        } catch (error) {
            console.warn("[arona] session check failed", error)
        }
        state.user = authenticated.user ?? null      // {id, username, global_name, avatar}: tai khoan Discord thay cho he thong tai khoan cua Signal
        state.auth = {baseUrl: "", token: access_token, channelId: sdk.channelId}
        window.dispatchEvent(new CustomEvent("arona-auth", {detail: state.auth}))
        return state.auth
    })().catch(error => {
        state.error = error
        console.warn("[arona] Discord login failed", error)
        window.dispatchEvent(new CustomEvent("arona-auth-error", {detail: error}))
        return null
    })
}
