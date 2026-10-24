# Security

## Trust model

Requesters are authenticated, workers are open by default. Read
[Trust model](README.md#trust-model) in the README before exposing the grid
beyond your own machines. In short: without `GRID_WORKER_TOKEN`, any worker
sees your data and custom code, results can be forged by someone controlling
enough workers, and peers learn each other's IP addresses.

## Known limitations

- **Unauthenticated connections** do not count towards the connection limits;
  they are only closed after a 5 s timeout. For public deployments, limit
  connections per IP in a reverse proxy.
- **Tokens in the URL fragment** (`/requester#token=...`) end up in the
  browser history. Do not use requester links on shared devices.
- **The page CSP allows `style-src 'unsafe-inline'`** (inline styles have not
  been moved out yet). Scripts are not affected.
- Tested in Firefox only.

## Security review (October 2026)

An internal review of the server, the browser clients and the Rust kernels
found the issues below. All of them are fixed on `main`.

| Issue | Fix | Commit |
|---|---|---|
| Custom requester code ran unsandboxed in the worker tab | Runs in `sandbox_worker.js` under CSP `default-src 'none'`, fail-closed, 10 s timeout | `2a9f92f` |
| Anyone could connect as requester and deploy code | Requester tokens (per person, revocable at runtime), optional worker token | `a3d5a0c`, `665c5e2` |
| A new requester could take over an existing session | Multiple requesters, `requester_id` assigned by the server | `ed35af4` |
| SSRF via server-side geo lookup | Geo lookup removed | `be9c27d` |
| Missing message validation, server DoS | JSON/type/score checks, message whitelist, rate limit, connection and size limits | `fdfabd6` |
| XSS via worker-supplied values | `textContent` instead of `innerHTML`, numeric values validated | `687bdb0` |
| Result verification had no effect | Tiles accepted only on a majority of byte-identical results | `c44a536` |
| Plain-text WebSocket, no origin check | Host and origin checks, `wss://` over HTTPS | `2767727` |
| Server listened on `0.0.0.0` by default | Defaults to `127.0.0.1`; network bind requires TLS unless `GRID_ALLOW_INSECURE=1` | `2767727` |
| Rust kernels could panic on bad dimensions | Dimension and length checks | `c7ef85e` |

Additionally, the pages are served with a CSP without inline scripts,
`frame-ancestors 'none'` and `X-Content-Type-Options: nosniff` (`afc320c`).
Regression tests for these fixes live in `tests/server` and `tests/browser`.
