// Chay som trong Activity: goi sdk.ready() de Discord tat man hinh loading, roi dang nhap de app co token goi API cua bot.
// Ket qua: window.__arona = {auth: {baseUrl, token, channelId} | null, ready: Promise}; sau khi dang nhap xong phat su kien "arona-auth".
import {DiscordSDK} from "@discord/embedded-app-sdk"

const query = new URLSearchParams(location.search)
const state = {auth: null, user: null, error: null, ready: Promise.resolve(null)}
window.__arona = state
const WIKI_URL = "https://bluearchive.wiki/wiki/Main_Page"

// Activity mo truc tiep (App Launcher/Entry Point) khong qua nut Open DAW -> khong co
// session, nen hien trang Blue Archive wiki thay vi mot DAW trong.
function showWiki(sdk) {
    const cover = document.createElement("div")
    cover.style.cssText = "position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;" +
        "align-items:center;justify-content:center;gap:18px;background:#0b1220;color:#e8ecf4;" +
        "font:16px/1.5 system-ui,Segoe UI,sans-serif;text-align:center;padding:24px"
    const title = document.createElement("div")
    title.textContent = "Blue Archive Wiki"
    title.style.cssText = "font-size:28px;font-weight:700;letter-spacing:.5px"
    const hint = document.createElement("div")
    hint.textContent = "Open the DAW from the !arona synth menu to use the editor."
    hint.style.cssText = "opacity:.65;max-width:420px"
    const link = document.createElement("a")
    link.href = WIKI_URL
    link.target = "_blank"
    link.rel = "noreferrer"
    link.textContent = "Open bluearchive.wiki"
    link.style.cssText = "color:#7cc4ff;font-weight:600;text-decoration:none;border:1px solid #2b4a6f;" +
        "border-radius:10px;padding:10px 18px;background:#12233a"
    cover.append(title, hint, link)
    ;(document.body || document.documentElement).appendChild(cover)
    // Thu mo bang trinh duyet cua Discord; neu bi chan thi nut tren van dung duoc.
    if (sdk && sdk.commands && sdk.commands.openExternalLink) {
        sdk.commands.openExternalLink({url: WIKI_URL}).catch(() => {})
    }
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
            showWiki(sdk)
            return null
        }
        // Chi vao DAW khi kenh da co session (bam Open DAW). Mo tu App Launcher/Entry Point
        // thi khong co session -> hien wiki. Loi mang/route cu -> coi nhu binh thuong.
        try {
            const check = await fetch("/api/session/check", {
                headers: {"Authorization": `Bearer ${access_token}`, "X-Channel-Id": sdk.channelId}
            })
            if (check.ok && (await check.json()).exists === false) {
                showWiki(sdk)
                return null
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
