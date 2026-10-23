# P2P Global Compute Grid

A distributed peer-to-peer rendering farm and data processing engine built with **Rust (WASM)**, **WebRTC**, and **FastAPI**. 
This project turns every browser tab into a compute node, forming a decentralized supercomputer.

## Key Features

- **P2P Communication:** Direct browser-to-browser data transfer via WebRTC (DataChannels), bypassing server bottlenecks.
- **High-Performance Kernels:** Image processing (Sobel, Inversion) powered by Rust compiled to WebAssembly.
- **Custom Logic Injection:** Requesters can deploy custom JavaScript logic across the grid with near-native performance.
- **Security Sandboxing:** Worker nodes execute custom code in a secured environment with restricted API access: custom code runs in a dedicated worker locked down by a strict Content-Security-Policy (no fetch/XHR/WebSocket, no script loading, no sub-workers) with a hard timeout.
- **Result Verification:** A tile is only accepted once the configured number of distinct workers returned byte-identical results; mismatching results trigger recomputation by other workers, and tiles without consensus are discarded and reported.

## Tech Stack

- **Backend:** Python (FastAPI, WebSockets for signaling)
- **High-Perf:** Rust (WebAssembly / wasm-bindgen)
- **Networking:** WebRTC (Peer-to-Peer), WebSockets
- **Frontend:** Vanilla JS, HTML5 Canvas, CSS3

## Getting Started

### 1. Prerequisites
- Rust & `wasm-pack`
- Python 3.8+

### 2. Build the WASM Worker
```bash
cd rust_worker
wasm-pack build --target web
```
### 3. Start the Signaling Server
```bash
pip install fastapi uvicorn
```
```bash
python3 main.py
```

By default the server only listens on `127.0.0.1:8000`. Configuration via
environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GRID_HOST` / `GRID_PORT` | `127.0.0.1` / `8000` | Bind address. A non-loopback address requires TLS. |
| `GRID_SSL_CERTFILE` / `GRID_SSL_KEYFILE` | – | Serve HTTPS/WSS directly. |
| `GRID_ALLOWED_HOSTS` | – | Extra host names/IPs (comma-separated) the grid may be reached under, e.g. `grid.example.com,192.168.1.10`. `localhost`, `127.0.0.1` and `::1` are always allowed. |
| `GRID_ALLOW_INSECURE` | – | `1` allows a non-loopback bind without TLS (tokens travel in plain text). |
| `GRID_REQUESTER_TOKEN` / `GRID_WORKER_TOKEN` | random / – | See below. |
| `GRID_REQUESTER_TOKENS_FILE` | `requester_tokens.txt` | Per-person requester tokens, see below. |

Example for a LAN with TLS:
```bash
GRID_HOST=0.0.0.0 GRID_ALLOWED_HOSTS=192.168.1.10 \
GRID_SSL_CERTFILE=cert.pem GRID_SSL_KEYFILE=key.pem python3 main.py
```
Behind a TLS reverse proxy, keep `GRID_HOST=127.0.0.1`, add the public host
name to `GRID_ALLOWED_HOSTS` and forward the original `Host` header.

### 4. Run the Grid

1.  Open http://localhost:8000 to see the Global Dashboard. WebSocket
    connections only accept pages from the same origin.
2.  Open /worker in multiple tabs (or different devices) to provide compute
    power.
3.  Open /requester to start a rendering or data analysis job. Requesters must
    authenticate: on startup the server prints the link
    `/requester#token=...`. Set `GRID_REQUESTER_TOKEN` for a fixed token,
    otherwise a random one is generated on every start. For one token per
    person, see [Requester tokens](#requester-tokens).
4.  Optional: set `GRID_WORKER_TOKEN` to only allow workers that open
    `/worker#token=<GRID_WORKER_TOKEN>`. Without it, anyone who can reach the
    server can join as worker – see [Trust model](#trust-model) before exposing
    the grid beyond your own machines.

### Requester tokens

Instead of one shared token, every requester can get their own. Tokens are
managed while the server is running, no restart needed:

```bash
python3 main.py add-requester alice     # prints alice's link once
python3 main.py remove-requester alice  # alice is disconnected within 2 s
python3 main.py list-requesters
```

The tokens are stored in `requester_tokens.txt` (only their SHA-256 hashes,
file mode `0600`, git-ignored). If a link gets lost, remove the person and add
them again. As soon as this file exists, only its tokens are accepted – the
shared `GRID_REQUESTER_TOKEN` stops working and open sessions using it are
closed. The server log shows which requester connected.

### Tests

```bash
# Rust kernels
(cd rust_worker && cargo test)
# Signaling server (standard library unittest + websockets, starts its own server)
python3 -m unittest discover -s tests/server -v
# Browser end-to-end (Firefox + Node.js; requires the built rust_worker/pkg)
(cd tests/browser && npm install && npm test)
```

The browser tests drive headless Firefox (path via `FIREFOX`, default
`/usr/bin/firefox`) and cover the sandbox (no network access, timeout), two
requesters, result verification against a malicious worker, XSS via worker
data and file names, WASM robustness and seamless Sobel tiles. Run a subset
with e.g. `npm test -- sobel`.

### Security & Design Decisions

  - Transferable Objects: Used for zero-copy memory transfer between Workers and
    the main thread, ensuring 60fps performance even during heavy computation.
  - API Lockdown: Requester code never runs in the compute worker. It is executed
    in a separate sandbox worker served with `Content-Security-Policy:
    default-src 'none'`, so network access and code loading are blocked by the
    browser. If the CSP is not enforced, the sandbox refuses to run custom code.
  - JIT-Optimized Loops: Custom logic is wrapped in a pre-compiled function
    scope to allow the V8 engine to optimize execution paths.

### Trust model

Requesters are authenticated, workers are open by default. A worker is an
untrusted browser tab, so with an open grid assume that strangers process your
jobs:

  - **No confidentiality:** workers receive the tiles of your image or data set
    and the custom JavaScript you deploy. Do not process anything private on a
    grid with untrusted workers.
  - **Results can be forged:** verification only accepts a tile when
    *redundancy* different workers return identical bytes. One person can open
    several worker tabs, so this protects against single faulty or malicious
    workers, not against someone controlling enough of the grid. Redundancy 1
    disables verification entirely.
  - **IP addresses are visible:** WebRTC connects requester and workers
    directly, so each side learns the other's IP address. ICE candidates are
    gathered via Google's public STUN servers.
  - **Statistics are not trustworthy:** worker count, capacity and benchmark
    scores are self-reported.

For anything beyond a test setup on your own devices, set `GRID_WORKER_TOKEN`
and only hand the worker link to machines you trust.

### License
MIT
