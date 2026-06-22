import init, { process_image_chunk, process_sobel } from '/pkg/rust_worker.js';

let wasmInit = init();
let customFn = null;
let lastCode = "";

const safeGlobals = {
    fetch: undefined, XMLHttpRequest: undefined, WebSocket: undefined,
    Worker: undefined, indexedDB: undefined
};
Object.assign(self, safeGlobals);

self.onmessage = async (e) => {
    await wasmInit;
    const { header, pixels } = e.data;
    let result = null;
    let resultType = "IMAGE_RESULT";

    try {
        if (header.type === "CUSTOM_LOGIC") {
            if (header.customCode !== lastCode) {
                customFn = new Function('p', `
                "use strict";
    for (let i = 0; i < p.length; i += 4) {
        let r = p[i], g = p[i+1], b = p[i+2], a = p[i+3];
        // Hier wird der User-Code ausgeführt
        const res = (() => { ${header.customCode} })();
        p[i] = res[0]; p[i+1] = res[1]; p[i+2] = res[2]; p[i+3] = res[3];
    }
    `);
                lastCode = header.customCode;
            }
            const input = new Uint8Array(pixels);
            customFn(input);
            result = input;
        }
        else if (header.type === "BIG_DATA") {
            resultType = "DATA_RESULT";
            if (header.customCode !== lastCode) {
                customFn = new Function('data', `"use strict"; ${header.customCode}`);
                lastCode = header.customCode;
            }
            const data = new Uint8Array(pixels);
            const output = customFn(data);
            result = (output instanceof Uint8Array) ? output : new Uint8Array(output);
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
