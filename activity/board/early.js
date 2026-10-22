// Chay TRUOC moi module script: tao panel debug ngay lap tuc nen bat ky loi nao
// (JS, rejection, CSP, module khong chay duoc) cung hien len man hinh thay vi
// con lai man xam roi. Panel tu dong do lai sau khi board chay thanh cong; chip
// "e" o goc phai duoi (click) mo lai panel bat cu luc nao.
(function () {
    const TAG = "v6"
    let panel = null
    let chip = null
    let userClosed = false
    let lines = 0

    function open() {
        if (panel) { panel.hidden = false; if (chip) { chip.hidden = true } return }
        panel = document.createElement("pre")
        panel.id = "arona-debug"
        panel.style.cssText = "position:fixed;left:0;right:0;bottom:0;max-height:45vh;overflow:auto;margin:0;padding:6px 8px;" +
            "background:rgba(20,21,26,.94);color:#d8dae0;font:11px/1.4 Consolas,monospace;z-index:2147483647;white-space:pre-wrap"
        panel.textContent = "arona debug — click to hide (chip at bottom-right reopens)"
        panel.onclick = function () {
            panel.hidden = true
            userClosed = true
            if (chip) { chip.hidden = false }
        }
        ;(document.body || document.documentElement).appendChild(panel)
    }

    function note(text, bad) {
        if (bad) { open(); userClosed = false }
        else if (!panel || userClosed) {
            // Tuc cam: log tich luy trong nho, chi hien khi co loi hoac mo lai.
            if (!panel) { open(); panel.hidden = true }
        }
        const row = document.createElement("div")
        row.style.color = bad ? "#ff7b72" : "#b8bcc4"
        row.textContent = text
        panel.appendChild(row)
        panel.scrollTop = panel.scrollHeight
        lines += 1
        if (lines > 300) { panel.removeChild(panel.children[1]) }   // cap the log
    }

    function close() {
        if (!panel) { return }
        panel.hidden = true
        userClosed = true
        if (!chip) {
            chip = document.createElement("div")
            chip.id = "arona-debug-chip"
            chip.style.cssText = "position:fixed;right:10px;bottom:10px;width:18px;height:18px;border-radius:4px;z-index:2147483646;" +
                "background:rgba(20,21,26,.9);color:#9c9a97;font:11px/18px Consolas,monospace;text-align:center;cursor:pointer"
            chip.textContent = "e"
            chip.title = "show arona debug log"
            chip.onclick = open
            ;(document.body || document.documentElement).appendChild(chip)
        }
        chip.hidden = false
    }

    window.addEventListener("error", (ev) => {
        note(`error: ${ev && ev.message} (${ev && ev.filename ? ev.filename.split("/").pop() : ""}:${ev && ev.lineno})`, true)
    })
    window.addEventListener("unhandledrejection", (ev) => {
        const reason = ev && ev.reason
        note(`rejection: ${(reason && reason.message) || reason}`, true)
    })
    document.addEventListener("securitypolicyviolation", (ev) => {
        note(`CSP blocked ${ev.violatedDirective}: ${ev.blockedURI}`, true)
    })
    window.addEventListener("DOMContentLoaded", () => note(`dom ready (scripts: ${Array.from(document.scripts).map(s => (s.src || "").split("/").pop()).filter(Boolean).join(", ")})`))

    window.__aronaPanel = { note, close, open, tag: TAG }
    note(`early ${TAG} ok — URL ${location.pathname}${location.search ? "?" + location.search : ""}`)
})()
