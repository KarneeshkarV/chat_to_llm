# Chat-to-LLM

Local API server that reads ChatGPT cookies from your browser, exchanges them for an access token, and exposes an OpenAI-compatible endpoint.

## Quickstart

```bash
uv sync
CHATGPT_BROWSER_AUTH=true python app.py
```

## Usage

### Chat completions

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

### Streaming

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### Check browser token

```bash
curl -X POST http://localhost:8000/tokens/browser
```

Force a refresh:

```bash
curl -X POST "http://localhost:8000/tokens/browser?force_refresh=true"
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `CHATGPT_BROWSER_AUTH` | `false` | Enable browser cookie auth |
| `CHATGPT_BROWSER_AUTH_ALLOW_REMOTE` | `false` | Allow non-localhost callers |
| `CHATGPT_BROWSER` | (auto-detect) | Browser: `arc`, `chrome`, `edge`, `firefox`, `brave` |
| `CHATGPT_CHROME_PROFILE` | (auto-detect) | Chromium profile name, e.g. `Default`, `Profile 1` |
| `CHATGPT_COOKIE` | | Raw cookie string override (`name=value; name2=value2`) |
| `CHATGPT_BASE_URL` | `https://chatgpt.com` | ChatGPT base URL |
| `PROXY_URL` | | HTTP/SOCKS5 proxy URL |
| `HISTORY_DISABLED` | `true` | Disable ChatGPT conversation history |
| `CONVERSATION_ONLY` | `false` | Skip sentinel handshake (dev only) |

## Security

- Browser cookie auth is **disabled by default**. Set `CHATGPT_BROWSER_AUTH=true` to enable.
- When enabled, browser cookie auth is **localhost-only** by default. Set `CHATGPT_BROWSER_AUTH_ALLOW_REMOTE=true` to allow remote callers (use with caution).
- Full cookies and access tokens are **never logged**. Only masked previews are logged.
- Tokens are cached **in memory only** — never written to disk.

## Bearer token

Use `Authorization: Bearer browser` to trigger browser cookie extraction. Aliases: `browser`, `chatgpt-browser`, `cookies`, `chatgpt-cookies`.

You can also pass a direct ChatGPT access token: `Authorization: Bearer eyJhbGci...`.

## Supported models

`gpt-4o`, `gpt-4o-mini`, `gpt-4`, `o1`, `o1-mini`, `o3-mini`, `gpt-3.5-turbo`, `auto`, and gizmo IDs (`g-...`).

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
```
