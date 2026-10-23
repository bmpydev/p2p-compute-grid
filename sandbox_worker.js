// Isolierte Ausführungsumgebung für Requester-Code (CUSTOM_LOGIC / BIG_DATA).
// Die eigentliche Isolierung ist der CSP-Header, den main.py für diese Datei setzt
// (default-src 'none'): kein fetch/XHR/WebSocket/EventSource, kein import()/importScripts,
// keine weiteren Worker. Kein Zugriff auf WASM, DataChannel oder andere Sub-Worker.

// Fail closed: Wird die CSP nicht durchgesetzt, läuft kein Fremdcode.
const cspActive = fetch('/sandbox-probe', { cache: 'no-store' }).then(() => false, () => true);

let compiledMode = null;
let compiledCode = null;
let compiledFn = null;

function compile(mode, code) {
    if (mode === "CUSTOM_LOGIC") {
        return new Function('p', `
    "use strict";
    for (let i = 0; i < p.length; i += 4) {
        let r = p[i], g = p[i+1], b = p[i+2], a = p[i+3];
        // Hier wird der User-Code ausgeführt
        const res = (() => { ${code} })();
        p[i] = res[0]; p[i+1] = res[1]; p[i+2] = res[2]; p[i+3] = res[3];
    }
    return p;
    `);
    }
    if (mode === "BIG_DATA") {
        return new Function('data', `"use strict"; ${code}`);
    }
    throw new Error("Unknown sandbox mode: " + mode);
}

self.onmessage = async (e) => {
    const { id, mode, code, buffer } = e.data;
    try {
        if (!(await cspActive)) throw new Error("Sandbox CSP not enforced - refusing to run custom code");
        if (mode !== compiledMode || code !== compiledCode) {
            compiledFn = compile(mode, code);
            compiledMode = mode;
            compiledCode = code;
        }
        const output = compiledFn(new Uint8Array(buffer));
        const result = (output instanceof Uint8Array) ? output : new Uint8Array(output);
        self.postMessage({ id, ok: true, result }, [result.buffer]);
    } catch (err) {
        self.postMessage({ id, ok: false, error: String(err) });
    }
};
