# P2P Global Compute Grid

A distributed peer-to-peer rendering farm and data processing engine built with **Rust (WASM)**, **WebRTC**, and **FastAPI**. 
This project turns every browser tab into a compute node, forming a decentralized supercomputer.

## Key Features

- **P2P Communication:** Direct browser-to-browser data transfer via WebRTC (DataChannels), bypassing server bottlenecks.
- **High-Performance Kernels:** Image processing (Sobel, Inversion) and Mandelbrot fractals powered by Rust compiled to WebAssembly.
- **Custom Logic Injection:** Requesters can deploy custom JavaScript logic across the grid with near-native performance.
- **Security Sandboxing:** Worker nodes execute custom code in a secured environment with restricted API access: custom code runs in a dedicated worker locked down by a strict Content-Security-Policy (no fetch/XHR/WebSocket, no script loading, no sub-workers) with a hard timeout.
- **Result Verification:** Built-in redundancy system that cross-verifies results from multiple workers for data integrity.

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
pip install fastapi uvicorn requests
```
```bash
python main.py
```
or
```bash
python3 main.py
```

### 4. Run the Grid

1.  Open http://localhost:8000 to see the Global Dashboard.
2.  Open /worker in multiple tabs (or different devices) to provide compute
    power.
3.  Open /requester to start a rendering or data analysis job. Requesters must
    authenticate: on startup the server prints the link
    `/requester#token=...`. Set `GRID_REQUESTER_TOKEN` for a fixed token,
    otherwise a random one is generated on every start.
4.  Optional: set `GRID_WORKER_TOKEN` to only allow workers that open
    `/worker#token=<GRID_WORKER_TOKEN>`. Without it, anyone can join as worker.

### Security & Design Decisions

  - Transferable Objects: Used for zero-copy memory transfer between Workers and
    the main thread, ensuring 60fps performance even during heavy computation.
  - API Lockdown: Requester code never runs in the compute worker. It is executed
    in a separate sandbox worker served with `Content-Security-Policy:
    default-src 'none'`, so network access and code loading are blocked by the
    browser. If the CSP is not enforced, the sandbox refuses to run custom code.
  - JIT-Optimized Loops: Custom logic is wrapped in a pre-compiled function
    scope to allow the V8 engine to optimize execution paths.

### License
MIT
