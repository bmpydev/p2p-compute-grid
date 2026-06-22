# P2P Global Compute Grid

A distributed peer-to-peer rendering farm and data processing engine built with **Rust (WASM)**, **WebRTC**, and **FastAPI**. 
This project turns every browser tab into a compute node, forming a decentralized supercomputer.

## Key Features

- **P2P Communication:** Direct browser-to-browser data transfer via WebRTC (DataChannels), bypassing server bottlenecks.
- **High-Performance Kernels:** Image processing (Sobel, Inversion) and Mandelbrot fractals powered by Rust compiled to WebAssembly.
- **Custom Logic Injection:** Requesters can deploy custom JavaScript logic across the grid with near-native performance.
- **Security Sandboxing:** Worker nodes execute custom code in a secured environment with restricted API access (No fetch/XHR).
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
3.  Open /requester to start a rendering or data analysis job.

### Security & Design Decisions

  - Transferable Objects: Used for zero-copy memory transfer between Workers and
    the main thread, ensuring 60fps performance even during heavy computation.
  - API Lockdown: In the compute worker, global APIs like fetch and WebSocket
    are disabled to prevent data exfiltration by malicious job requesters.
  - JIT-Optimized Loops: Custom logic is wrapped in a pre-compiled function
    scope to allow the V8 engine to optimize execution paths.

### License
MIT
