// Chay som trong Activity: goi sdk.ready() de Discord tat man hinh loading, roi dang nhap de app co token goi API cua bot.
// Ket qua: window.__arona = {auth: {baseUrl, token, channelId} | null, ready: Promise}; sau khi dang nhap xong phat su kien "arona-auth".
import {DiscordSDK} from "@discord/embedded-app-sdk"

const query = new URLSearchParams(location.search)
const state = {auth: null, error: null, ready: Promise.resolve(null)}
window.__arona = state

// Trong Discord: neu app bi trang man hinh thi hien loi ngay tren man hinh (loi JS, bi CSP chan, luu tru khong dung duoc).
if (query.has("frame_id")) {
    const panel = document.createElement("pre")
    panel.id = "arona-debug"
    panel.style.cssText = "position:fixed;left:0;right:0;bottom:0;max-height:45vh;overflow:auto;margin:0;padding:6px 8px;" +
        "background:rgba(20,21,26,.94);color:#d8dae0;font:11px/1.4 Consolas,monospace;z-index:2147483647;white-space:pre-wrap"
    panel.hidden = true
    panel.onclick = () => {panel.hidden = true}
    const add = (text, bad) => {
        const line = document.createElement("div")
        line.style.color = bad ? "#ff7b72" : "#6fcf97"
        line.textContent = text
        panel.appendChild(line)
        if (bad) {panel.hidden = false}
        if (!panel.isConnected) {(document.body || document.documentElement).appendChild(panel)}
    }
    const probe = (name, fn) => {
        try {fn(); add(`ok   ${name}`, false)} catch (error) {add(`FAIL ${name}: ${error && error.message || error}`, true)}
    }
    probe("localStorage", () => {localStorage.setItem("arona-probe", "1"); localStorage.removeItem("arona-probe")})
    probe("sessionStorage", () => {sessionStorage.setItem("arona-probe", "1")})
    probe("indexedDB", () => {if (!indexedDB) {throw new Error("missing")}; indexedDB.open("arona-probe")})
    probe("navigator.storage", () => {if (!navigator.storage) {throw new Error("missing")}})
    window.addEventListener("error", event => add(`error: ${event.message} (${(event.filename || "").split("/").pop()}:${event.lineno})`, true))
    window.addEventListener("unhandledrejection", event => add(`rejection: ${event.reason && event.reason.message || event.reason}`, true))
    document.addEventListener("securitypolicyviolation", event => add(`CSP blocked ${event.violatedDirective}: ${event.blockedURI}`, true))
    window.addEventListener("DOMContentLoaded", () => add("DOMContentLoaded", false))
    window.addEventListener("load", () => {
        const root = document.getElementById("root")
        add(`load; #root children=${root ? root.childElementCount : "no #root"}`, !root || root.childElementCount === 0)
    })
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
        await sdk.commands.authenticate({access_token})
        if (sdk.channelId === null) {throw new Error("Activity has no voice channel")}
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
