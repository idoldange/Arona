// Trang chan doan Activity: kiem tra tung buoc tu luc Discord load iframe cho toi khi goi duoc API co session.
import {DiscordSDK} from "@discord/embedded-app-sdk"

const out = document.getElementById("log")
const log = (text, cls = "") => {
    const line = document.createElement("div")
    line.className = cls
    line.textContent = text
    out.appendChild(line)
}
const describe = error => (error && (error.message || JSON.stringify(error))) || String(error)

const step = async (name, fn) => {
    try {
        const value = await fn()
        log(`OK   ${name}`, "ok")
        return value
    } catch (error) {
        log(`FAIL ${name}: ${describe(error)}`, "err")
        throw error
    }
}

const auth = {token: null, channelId: null}
const headers = () => ({"Authorization": `Bearer ${auth.token}`, "X-Channel-Id": String(auth.channelId), "Content-Type": "application/json"})

const demoNotes = () => {
    const notes = []
    for (let phrase = 0; phrase < 2; phrase++) {
        for (let i = 0; i < 4; i++) {
            notes.push({pos: (phrase * 6 + i) * 480, dur: 480, tone: 60 + i + phrase * 2, lyric: "la"})
        }
    }
    return notes
}

const testPlan = async () => {
    const response = await fetch("/api/vocal/plan", {
        method: "POST", headers: headers(),
        body: JSON.stringify({track: "diag", ppq: 480, tempos: [[0, 120]], notes: demoNotes()})
    })
    log(`POST /api/vocal/plan -> ${response.status} ${await response.text()}`, response.ok ? "ok" : "err")
}

const main = async () => {
    log(`page loaded (${location.origin})`, "ok")
    log(`frame_id present: ${new URLSearchParams(location.search).has("frame_id")}`)
    log(`crossOriginIsolated: ${window.crossOriginIsolated} (openDAW needs true)`, window.crossOriginIsolated ? "ok" : "err")
    log(`user agent: ${navigator.userAgent}`)
    const sabType = typeof SharedArrayBuffer
    let sabWorks = false
    try {sabWorks = new SharedArrayBuffer(8).byteLength === 8} catch (error) {log(`new SharedArrayBuffer failed: ${describe(error)}`, "err")}
    log(`SharedArrayBuffer: typeof=${sabType}, usable=${sabWorks}`, sabWorks ? "ok" : "err")
    log(`AudioWorklet: ${typeof AudioWorkletNode !== "undefined"}, WebAssembly: ${typeof WebAssembly !== "undefined"}, Worker: ${typeof Worker !== "undefined"}`)
    try {
        const context = new AudioContext()
        log(`AudioContext created, state=${context.state}, sampleRate=${context.sampleRate}`, "ok")
        context.close()
    } catch (error) {log(`AudioContext failed: ${describe(error)}`, "err")}
    const config = await step("GET /api/config (server reachable through Discord proxy)", async () => {
        const response = await fetch("/api/config")
        if (!response.ok) {throw new Error(`HTTP ${response.status}`)}
        return response.json()
    })
    const sdk = new DiscordSDK(config.client_id)
    await step("SDK ready", () => sdk.ready())
    const {code} = await step("authorize (Discord shows a consent popup the first time)", () => sdk.commands.authorize({
        client_id: config.client_id, response_type: "code", state: "", prompt: "none", scope: ["identify"]
    }))
    auth.token = await step("POST /api/token (code -> access token)", async () => {
        const response = await fetch("/api/token", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code})})
        if (!response.ok) {throw new Error(`HTTP ${response.status} ${await response.text()}`)}
        return (await response.json()).access_token
    })
    await step("authenticate", () => sdk.commands.authenticate({access_token: auth.token}))
    auth.channelId = sdk.channelId
    log(`voice channel id: ${auth.channelId}`)
    await step("GET /api/session (needs the session created by the Open DAW button)", async () => {
        const response = await fetch("/api/session", {headers: headers()})
        const text = await response.text()
        if (!response.ok) {throw new Error(`HTTP ${response.status} ${text}`)}
        log(text)
    })
    const button = document.getElementById("plan")
    button.hidden = false
    button.onclick = () => testPlan().catch(error => log(describe(error), "err"))
}

main().catch(() => log("Stopped at the first failing step above.", "err"))
