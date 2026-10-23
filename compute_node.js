import init, { process_image_chunk, process_sobel } from '/pkg/rust_worker.js';

let wasmInit = init();

// Fremdcode läuft nie in diesem Worker, sondern in sandbox_worker.js (CSP-isoliert).
const SANDBOX_TIMEOUT_MS = 10000;
let sandbox = null;
let nextJobId = 0;
const pending = new Map(); // id: {resolve, reject, timer}

function getSandbox() {
    if (!sandbox) {
        sandbox = new Worker('/sandbox_worker.js');
        sandbox.onmessage = (e) => {
            const job = pending.get(e.data.id);
            if (!job) return;
            pending.delete(e.data.id);
            clearTimeout(job.timer);
            if (e.data.ok) job.resolve(e.data.result);
            else job.reject(new Error(e.data.error));
        };
        sandbox.onerror = (e) => {
            e.preventDefault();
            resetSandbox(new Error("Sandbox crashed"));
        };
    }
    return sandbox;
}

// Endlosschleifen o. Ä.: Sandbox hart beenden, beim nächsten Job neu starten
function resetSandbox(reason) {
    if (sandbox) sandbox.terminate();
    sandbox = null;
    for (const job of pending.values()) {
        clearTimeout(job.timer);
        job.reject(reason);
    }
    pending.clear();
}

function runInSandbox(mode, code, buffer) {
    return new Promise((resolve, reject) => {
        const id = nextJobId++;
        const timer = setTimeout(() => resetSandbox(new Error("Sandbox timeout")), SANDBOX_TIMEOUT_MS);
        pending.set(id, { resolve, reject, timer });
        getSandbox().postMessage({ id, mode, code: String(code), buffer }, [buffer]);
    });
}

self.onmessage = async (e) => {
    await wasmInit;
    const { header, pixels } = e.data;
    let result = null;
    let resultType = "IMAGE_RESULT";

    try {
        if (header.type === "CUSTOM_LOGIC") {
            result = await runInSandbox("CUSTOM_LOGIC", header.customCode, pixels);
        }
        else if (header.type === "BIG_DATA") {
            resultType = "DATA_RESULT";
            result = await runInSandbox("BIG_DATA", header.customCode, pixels);
        }
        else if (header.type === "IMAGE_SOBEL") {
            result = process_sobel(new Uint8Array(pixels), header.w, header.h);
        }
        else if (header.type === "IMAGE_PROC") {
            result = process_image_chunk(new Uint8Array(pixels));
        }

        if (result) {
            self.postMessage({
                metadata: { ...header, type: resultType },
                pixels: result
            }, [result.buffer]);
        }
    } catch (err) {
        console.error("Compute Error:", err);
        const empty = new Uint8Array(0);
        self.postMessage({ metadata: { ...header, type: resultType }, pixels: empty }, [empty.buffer]);
    }
};
