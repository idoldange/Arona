/* Click & hold FX: ba-click-fx by CialloKing (MIT) - https://github.com/CialloKing/ba-click-fx */
// Discord CSP chan jsDelivr -> build-web.sh tai ba-click-fx@1.3.15 ve 1 lan va phuc vu tu chinh server.
const CDN = "/ba-click-fx.js?v=12";
const root = document.documentElement;
const touchAction = root.dataset.fxTouch || "none";
const skipSelector = root.dataset.fxSkip || "";
let loading = null;

function init() {
    if (loading) return loading;
    loading = import(CDN).then(({ BAClickFX }) => {
        const options = { touchAction };
        if (skipSelector) {
            options.inputFilter = (event) => {
                const target = event && event.target;
                return !(target && target.closest && target.closest(skipSelector));
            };
        }
        const fx = new BAClickFX(options);
        window.__baFX = fx;
        return fx;
    }).catch((error) => {
        console.warn("[arona] click FX failed", error);
        return null;
    });
    return loading;
}

function schedule() {
    if ("requestIdleCallback" in window) {
        requestIdleCallback(init, { timeout: 1500 });
    } else {
        setTimeout(init, 300);
    }
}

if (document.readyState === "complete") schedule();
else window.addEventListener("load", schedule, { once: true });
window.addEventListener("pointerdown", init, { capture: true, once: true });
window.addEventListener("touchstart", init, { capture: true, once: true, passive: true });
