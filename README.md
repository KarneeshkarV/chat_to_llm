# Chat-to-LLM

Local API server that reads ChatGPT cookies from your browser, exchanges them for an access token via `chatgpt.com/api/auth/session`, and exposes an OpenAI-compatible endpoint.

No API keys. No ChatGPT Plus required. Just your existing browser session.

---

## Table of Contents

- [Quickstart](#quickstart)
- [API Reference](#api-reference)
  - [POST /v1/chat/completions](#post-v1chatcompletions)
  - [POST /tokens/browser](#post-tokensbrowser)
- [Request Formats](#request-formats)
  - [Non-streaming chat](#non-streaming-chat)
  - [Streaming chat](#streaming-chat)
  - [Multi-turn conversation](#multi-turn-conversation)
  - [System prompts](#system-prompts)
  - [Direct access token](#direct-access-token)
  - [Browser token check](#browser-token-check)
  - [Force token refresh](#force-token-refresh)
- [Response Formats](#response-formats)
  - [Non-streaming response](#non-streaming-response)
  - [Streaming response](#streaming-response)
  - [Browser token response](#browser-token-response)
- [Error Responses](#error-responses)
- [Configuration](#configuration)
- [Security Model](#security-model)
- [Supported Models](#supported-models)
- [Browser Cookie Extraction](#browser-cookie-extraction)
- [Architecture](#architecture)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## Quickstart

```bash
# Install dependencies
uv sync

# Start the server (browser auth must be explicitly enabled)
CHATGPT_BROWSER=brave CHATGPT_BROWSER_AUTH=true uv run python app.py
```

Server starts on `http://localhost:8000`.

Test it:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hello"}],"stream":false}'
```

---

## API Reference

### POST /v1/chat/completions

OpenAI-compatible chat completions endpoint.

| Header | Required | Description |
|---|---|---|
| `Authorization` | Yes | `Bearer browser` (cookie auth) or `Bearer <access_token>` |
| `Content-Type` | Yes | `application/json` |

| Body field | Type | Default | Description |
|---|---|---|---|
| `model` | string | `gpt-4o` | Model name (see [Supported Models](#supported-models)) |
| `messages` | array | required | OpenAI-style message list |
| `stream` | boolean | `false` | Enable SSE streaming |
| `max_tokens` | integer | `2147483647` | Max tokens in response |
| `history_disabled` | boolean | `true` | Disable ChatGPT conversation history |
| `conversation_id` | string | | Continue an existing conversation |
| `parent_message_id` | string | | Set parent message for continuation |
| `Chatgpt-Account-Id` | string | | Team account ID |

### POST /tokens/browser

Check whether browser cookies can produce a ChatGPT access token. Returns a masked preview — **never the full token**.

| Param | Type | Default | Description |
|---|---|---|---|
| `force_refresh` | boolean | `false` | Force re-extraction from browser (ignore cache) |

---

## Request Formats

### Non-streaming chat

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "What is 2+2?"}
    ],
    "stream": false
  }'
```

### Streaming chat

```bash
curl -sN http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {"role": "user", "content": "Explain quantum computing in 3 sentences"}
    ],
    "stream": true
  }'
```

The `-sN` flags disable curl's progress bar and buffering so SSE chunks appear as they arrive.

### Multi-turn conversation

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "My name is Alice"},
      {"role": "assistant", "content": "Nice to meet you, Alice!"},
      {"role": "user", "content": "What is my name?"}
    ],
    "stream": false
  }'
```

### System prompts

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "system", "content": "You are a pirate. Always respond in pirate speak."},
      {"role": "user", "content": "Hello!"}
    ],
    "stream": false
  }'
```

### Direct access token

If you have a ChatGPT access token (JWT starting with `eyJhbGci`), you can use it directly instead of browser cookies:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

### Browser token check

```bash
curl -X POST http://localhost:8000/tokens/browser
```

### Force token refresh

Bypass the in-memory cache and re-extract cookies + re-fetch the access token:

```bash
curl -X POST "http://localhost:8000/tokens/browser?force_refresh=true"
```

---

## Response Formats

### Non-streaming response

```json
{
  "id": "chatcmpl-t6sCRpYEEWWX1q1tSfJyAnrxlNlWx",
  "object": "chat.completion",
  "created": 1776679436,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello"
      },
      "logprobs": null,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 1,
    "total_tokens": 1
  }
}
```

### Streaming response

Each chunk is a `data:` line followed by two newlines. The final line is `data: [DONE]`.

```
data: {"id":"chatcmpl-WNy...","object":"chat.completion.chunk","created":1776679457,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"role":"assistant","content":""},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-WNy...","object":"chat.completion.chunk","created":1776679457,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"1  \n2"},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-WNy...","object":"chat.completion.chunk","created":1776679457,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"  \n3  \n4  \n5"},"logprobs":null,"finish_reason":"stop"}]}

data: [DONE]
```

| Chunk type | `delta` content | `finish_reason` |
|---|---|---|
| Role announcement | `{"role":"assistant","content":""}` | `null` |
| Content delta | `{"content":"new text"}` | `null` |
| Final chunk | `{}` | `"stop"` or `"length"` |

### Browser token response

```json
{
  "status": "success",
  "access_token_preview": "eyJhbGci...muuqaw",
  "expires_at": 1776781921,
  "user": {
    "id": "user-abc123",
    "name": "John Doe",
    "email": "john@example.com",
    "idp": "google-oauth2",
    "iat": 1775053698,
    "amr": ["otp", "mfa", "urn:openai:amr:otp_email"],
    "mfa": true
  }
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | `"success"` or error description |
| `access_token_preview` | string | First 8 + last 6 chars of token (masked) |
| `expires_at` | integer | JWT `exp` claim (unix timestamp), or `current_time + 600` if not decodable |
| `user` | object | ChatGPT user info from `/api/auth/session` |

---

## Error Responses

All errors follow the OpenAI error format:

```json
{
  "detail": "Human-readable error message"
}
```

| Status | Cause | Detail pattern |
|---|---|---|
| `400` | Invalid JSON body | `"Invalid JSON body"` |
| `400` | Message formatting failed | `"Failed to format messages."` |
| `401` | No cookies found | `"No ChatGPT cookies found..."` |
| `401` | Expired/invalid cookies | `"ChatGPT browser cookies are expired or invalid."` |
| `401` | No accessToken in session | `"ChatGPT session did not include an accessToken."` |
| `402` | Session response not JSON | `"ChatGPT session response was not JSON."` |
| `403` | Browser auth disabled | `"ChatGPT browser cookie auth is disabled..."` |
| `403` | Remote caller blocked | `"ChatGPT browser cookie auth is local-only..."` |
| `403` | Unusual activity detected | `"Our systems have detected unusual activity..."` |
| `403` | Proof-of-work failed | `"Failed to solve proof of work"` |
| `429` | Rate limited | `"rate-limit"` |
| `500` | Server error | `"Server error: ..."` |

---

## Configuration

All configuration is via environment variables or `.env` file.

### Security

| Variable | Default | Description |
|---|---|---|
| `CHATGPT_BROWSER_AUTH` | `false` | **Must be `true`** to use browser cookie auth |
| `CHATGPT_BROWSER_AUTH_ALLOW_REMOTE` | `false` | Allow requests from non-localhost IPs |

### Browser cookie extraction

| Variable | Default | Description |
|---|---|---|
| `CHATGPT_BROWSER` | auto-detect | Which browser to try first: `arc`, `chrome`, `edge`, `firefox`, `brave` |
| `CHATGPT_CHROME_PROFILE` | auto-detect | Chromium profile name (e.g. `Default`, `Profile 1`, `Profile 2`) |
| `CHATGPT_COOKIE` | | Raw cookie string override — skips browser extraction entirely (`name=value; name2=value2`) |
| `CHATGPT_BROWSER_AUTH_BASE_URL` | `https://chatgpt.com` | Base URL for `/api/auth/session` exchange |

### ChatGPT backend

| Variable | Default | Description |
|---|---|---|
| `CHATGPT_BASE_URL` | `https://chatgpt.com` | ChatGPT backend base URL |
| `PROXY_URL` | | HTTP/SOCKS5 proxy for outbound requests |
| `TURNSTILE_SOLVER_URL` | | External Turnstile solver URL (optional) |
| `ARKOSE_TOKEN_URL` | | External Arkose solver URL (optional) |

### Behavior

| Variable | Default | Description |
|---|---|---|
| `HISTORY_DISABLED` | `true` | Don't save conversations in ChatGPT history |
| `CONVERSATION_ONLY` | `false` | Skip sentinel/chat-requirements handshake (dev only) |

### `.env` file

Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

```ini
# Security — MUST set this to true
CHATGPT_BROWSER_AUTH=true

# Use Brave browser cookies
CHATGPT_BROWSER=brave
CHATGPT_CHROME_PROFILE=Default

# Or provide raw cookies (skips browser extraction)
CHATGPT_COOKIE=

# Allow remote callers (use with caution)
CHATGPT_BROWSER_AUTH_ALLOW_REMOTE=false

# ChatGPT backend
CHATGPT_BASE_URL=https://chatgpt.com
PROXY_URL=

# Behavior
HISTORY_DISABLED=true
CONVERSATION_ONLY=false
```

---

## Security Model

### Browser cookie auth is disabled by default

You must explicitly set `CHATGPT_BROWSER_AUTH=true`. Without it, every request returns `403`.

### Localhost-only by default

When browser auth is enabled, only requests from `127.0.0.1`, `::1`, and `localhost` are accepted. To allow remote callers:

```bash
CHATGPT_BROWSER_AUTH_ALLOW_REMOTE=true
```

**Warning**: This exposes your ChatGPT session to the network. Only use on trusted networks.

### No secrets logged

- Full cookies are never logged
- Full access tokens are never logged
- Only masked previews are logged (first 8 + last 6 characters)

### No disk persistence

- Access tokens are cached in memory only
- Cookies are never saved to disk
- Cache is lost on server restart

### Token caching

- Derived access tokens are cached in memory until near JWT expiry
- Cache is keyed on SHA-256 of the cookie string
- Cache validity is checked with a 60-second safety margin before JWT `exp`
- Force refresh via `POST /tokens/browser?force_refresh=true`

---

## Supported Models

The `model` field in the request body is matched by substring. The first match wins.

| Request model | ChatGPT backend model |
|---|---|
| `o3-mini-high` | `o3-mini-high` |
| `o3-mini-medium` | `o3-mini-medium` |
| `o3-mini-low` | `o3-mini-low` |
| `o3-mini` | `o3-mini` |
| `o3` | `o3` |
| `o1-preview` | `o1-preview` |
| `o1-pro` | `o1-pro` |
| `o1-mini` | `o1-mini` |
| `o1` | `o1` |
| `gpt-4.5o` | `gpt-4.5o` |
| `gpt-4o-canmore` | `gpt-4o-canmore` |
| `gpt-4o-mini` | `gpt-4o-mini` |
| `gpt-4o` | `gpt-4o` |
| `gpt-4-mobile` | `gpt-4-mobile` |
| `gpt-4` | `gpt-4` |
| `gpt-3.5-turbo` | `text-davinci-002-render-sha` |
| `auto` | `auto` |
| Any `g-*` or `gizmo*` | Gizmo interaction mode |
| Anything else | `gpt-4o` (fallback) |

Model availability depends on your ChatGPT account tier. Free accounts can use `gpt-4o-mini`, `gpt-3.5-turbo`, and `auto`. Plus/Team accounts can use `gpt-4o`, `gpt-4`, `o1`, `o3-mini`, etc.

---

## Browser Cookie Extraction

### Supported browsers

| Browser | Type | Platform support |
|---|---|---|
| **Arc** | Chromium | macOS, Windows |
| **Chrome** | Chromium | macOS, Windows, Linux |
| **Edge** | Chromium | macOS, Windows, Linux |
| **Brave** | Chromium | macOS, Windows, Linux |
| **Firefox** | Gecko | all platforms |

### Default browser order

`Arc → Chrome → Edge → Firefox → Brave`

The first browser that yields ChatGPT cookies wins. Override with `CHATGPT_BROWSER`:

```bash
CHATGPT_BROWSER=brave  # Try Brave first, then the rest
```

### Chromium profiles

By default, all Chromium profiles are scanned in order: `Default`, then `Profile 1`, `Profile 2`, etc.

Override with `CHATGPT_CHROME_PROFILE`:

```bash
CHATGPT_CHROME_PROFILE="Profile 2"
```

### Cookie override

Provide raw cookies via environment variable — skips all browser extraction:

```bash
CHATGPT_COOKIE="__Secure-next-auth.session-token=abc123; _cfuvid=xyz789"
```

### How it works

1. Read cookies from browser profile directories using `browser-cookie3`
2. Filter to `chatgpt.com` / `.chatgpt.com` / `chat.openai.com` domains
3. Send `GET /api/auth/session` with the cookies to ChatGPT
4. Extract `accessToken` from the JSON response
5. Decode JWT to get expiry timestamp
6. Cache in memory until near expiry

---

## Architecture

```
                    ┌──────────────────────────────────┐
                    │         Your Application          │
                    │  (curl, Python SDK, LangChain,    │
                    │   any OpenAI-compatible client)    │
                    └──────────────┬───────────────────┘
                                   │
                          POST /v1/chat/completions
                          Authorization: Bearer browser
                                   │
                    ┌──────────────▼───────────────────┐
                    │           app.py                  │
                    │         (FastAPI)                │
                    └──────────────┬───────────────────┘
                                   │
                    ┌──────────────▼───────────────────┐
                    │         api/chat.py               │
                    │   route handler + security        │
                    └──────┬───────────────────┬───────┘
                           │                   │
                ┌──────────▼──────┐  ┌─────────▼────────┐
                │ chatgpt/         │  │ chatgpt/          │
                │ browser_auth.py │  │ service.py        │
                │                 │  │                    │
                │ • cookie extract│  │ • model mapping    │
                │ • /auth/session │  │ • sentinel/PoW     │
                │ • token cache   │  │ • build request    │
                │ • security gates│  │ • send to backend  │
                └────────────────┘  └────────┬──────────┘
                                            │
                              ┌──────────────▼───────────┐
                              │ chatgpt/                  │
                              │ formatting.py + pow.py    │
                              │                           │
                              │ • SSE → OpenAI format     │
                              │ • SHA3-512 PoW solver     │
                              │ • DPL hash fetch          │
                              └──────────────────────────┘
```

### Project structure

```
chat_to_llm/
├── app.py                  # FastAPI app + uvicorn entrypoint
├── pyproject.toml          # uv-managed project
├── .env.example            # Example environment variables
├── chatgpt/
│   ├── browser_auth.py     # Cookie extraction + security gates + session exchange
│   ├── client.py           # curl_cffi async HTTP client wrapper
│   ├── fp.py               # Browser fingerprint generation
│   ├── pow.py              # Proof-of-Work solver + DPL hash fetch
│   ├── formatting.py       # ChatGPT SSE → OpenAI format conversion
│   └── service.py         # ChatGPT backend request builder/sender
├── api/
│   └── chat.py             # POST /v1/chat/completions + POST /tokens/browser
└── tests/
    └── test_browser_auth.py # 46 smoke tests
```

---

## Development

### Install

```bash
uv sync
```

### Run

```bash
CHATGPT_BROWSER=brave CHATGPT_BROWSER_AUTH=true uv run python app.py
```

With auto-reload:

```bash
CHATGPT_BROWSER=brave CHATGPT_BROWSER_AUTH=true uv run python -m uvicorn app:app --reload
```

### Test

```bash
uv run pytest tests/ -v
```

### Lint

```bash
uv run ruff check .
uv run ruff check . --fix   # auto-fix
```

### Use with Python OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="browser",  # triggers cookie extraction
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=False,
)

print(response.choices[0].message.content)
```

### Use with LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="browser",
    model="gpt-4o-mini",
)

response = llm.invoke("What is the capital of France?")
print(response.content)
```

### Use with curl (streaming)

```bash
curl -sN http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Tell me a story"}],
    "stream": true
  }'
```

---

## Troubleshooting

### "ChatGPT browser cookie auth is disabled"

Set `CHATGPT_BROWSER_AUTH=true` in your environment or `.env` file.

### "No ChatGPT cookies found"

1. Make sure you're logged into `chatgpt.com` in your browser
2. Try specifying your browser: `CHATGPT_BROWSER=chrome`
3. Try a specific profile: `CHATGPT_CHROME_PROFILE="Profile 1"`
4. On Linux, Chrome cookies may be in `~/.config/google-chrome/Default/Cookies`

### "ChatGPT browser cookies are expired or invalid"

Log back into `chatgpt.com` in your browser, then retry. The server will automatically pick up the new cookies.

### "ChatGPT browser cookie auth is local-only"

You're calling the API from a non-localhost IP. Either:
- Call from the same machine (`localhost`/`127.0.0.1`), or
- Set `CHATGPT_BROWSER_AUTH_ALLOW_REMOTE=true`

### "Our systems have detected unusual activity"

ChatGPT's anti-bot system flagged the request. Try:
- Wait a few minutes and retry
- Use a different `CHATGPT_BROWSER` value
- Ensure your browser session is active (visit `chatgpt.com` first)

### "Turnstile challenge required" / "Arkose challenge required"

ChatGPT is requesting a CAPTCHA challenge. The server logs this and proceeds without the token. If the request fails:
- Configure an external solver: `TURNSTILE_SOLVER_URL` and/or `ARKOSE_TOKEN_URL`
- Or try again later (challenges are intermittent)

### "rate-limit"

You're sending too many requests. ChatGPT enforces rate limits per account.

### Server fails to start — "address already in use"

Another process is using port 8000. Either:
- Kill it: `fuser -k 8000/tcp`
- Or use a different port: `uvicorn app:app --port 8001`

### macOS — "Unable to get key for cookie decryption"

Chrome on macOS stores encryption keys in the Keychain. If you're in an SSH session:

```bash
security unlock-keychain -p "your-password" ~/Library/Keychains/login.keychain-db
```

Or use the `CHATGPT_COOKIE` environment variable as a workaround.
