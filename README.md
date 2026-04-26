# Chat-to-LLM

Local API server that exposes OpenAI-compatible endpoints backed by browser-authenticated ChatGPT, Claude Web, Gemini Web, and Grok sessions.

No API keys. No ChatGPT Plus required. Just your existing browser session.

---

## Table of Contents

- [Quickstart](#quickstart)
- [API Reference](#api-reference)
  - [POST /v1/chat/completions](#post-v1chatcompletions)
  - [POST /v1/claude/chat/completions](#post-v1claudechatcompletions)
  - [POST /v1/gemini/chat/completions](#post-v1geminichatcompletions)
  - [POST /tokens/browser](#post-tokensbrowser)
  - [POST /tokens/claude/browser](#post-tokensclaudebrowser)
  - [GET /tokens/claude/browser/profiles](#get-tokensclaudebrowserprofiles)
  - [POST /tokens/gemini/browser](#post-tokensgeminibrowser)
  - [GET /tokens/gemini/browser/profiles](#get-tokensgeminibrowserprofiles)
- [Request Formats](#request-formats)
  - [Non-streaming chat](#non-streaming-chat)
  - [Streaming chat](#streaming-chat)
  - [Multi-turn conversation](#multi-turn-conversation)
  - [System prompts](#system-prompts)
  - [Direct access token](#direct-access-token)
  - [Browser token check](#browser-token-check)
  - [Force token refresh](#force-token-refresh)
  - [Tool calling (enable_tools)](#tool-calling-enable_tools)
- [Response Formats](#response-formats)
  - [Non-streaming response](#non-streaming-response)
  - [Streaming response](#streaming-response)
  - [Browser token response](#browser-token-response)
- [Error Responses](#error-responses)
- [Configuration](#configuration)
- [Security Model](#security-model)
- [Supported Models](#supported-models)
- [Browser Cookie Extraction](#browser-cookie-extraction)
- [Grok Browser Bootstrap](#grok-browser-bootstrap)
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
  -d '{"model":"gpt-5.4","messages":[{"role":"user","content":"Say hello"}],"stream":false}'
```

### Claude Quickstart

Claude uses a separate endpoint and reads your existing `claude.ai` browser cookies. Enable Claude browser auth explicitly:

```bash
CLAUDE_BROWSER=brave CLAUDE_BROWSER_AUTH=true uv run python app.py
```

Test Claude:

```bash
curl http://localhost:8000/v1/claude/chat/completions \
  -H "Authorization: Bearer claude-browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5","messages":[{"role":"user","content":"Say hello"}],"stream":false}'
```

### Gemini Quickstart

Gemini uses your existing Google / Gemini web session. Enable Gemini browser auth explicitly:

```bash
GEMINI_BROWSER=brave GEMINI_BROWSER_AUTH=true uv run python app.py
```

Test Gemini:

```bash
curl http://localhost:8000/v1/gemini/chat/completions \
  -H "Authorization: Bearer gemini-browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3-pro","messages":[{"role":"user","content":"Say hello"}],"stream":false}'
```

### Grok Quickstart

Grok reads cookies from your browser (like ChatGPT), **plus** spawns a short-lived headless Chromium session on the first request to capture the `x-statsig-id` header that Grok requires. See [Grok Browser Bootstrap](#grok-browser-bootstrap) for how it works.

Requirements:

1. Logged into [grok.com](https://grok.com) in Chrome/Brave/Edge/Firefox
2. A Chromium-based browser binary available at `/usr/bin/chromium`, `/usr/bin/google-chrome`, or `/usr/bin/brave` (auto-detected; override with `GROK_BROWSER_EXECUTABLE`)

```bash
# Start the server with Grok enabled (enabled by default)
uv run python app.py
```

Test Grok:

```bash
curl http://localhost:8000/v1/grok/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4.3","messages":[{"role":"user","content":"Say hello"}],"stream":false}'
```

The first request triggers a ~3-5s bootstrap. Subsequent requests reuse the cached session (15 min by default) and complete in under 2s.

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
| `model` | string | `gpt-5.4` | Model name (see [Supported Models](#supported-models)) |
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

### POST /v1/claude/chat/completions

OpenAI-compatible Claude Web endpoint exposed separately from the default ChatGPT route.

| Header | Required | Description |
|---|---|---|
| `Authorization` | Yes | `Bearer claude-browser`, `Bearer browser`, or `Bearer <raw_cookie_header>` |
| `Content-Type` | Yes | `application/json` |
| `claude-profile` | No | Browser profile override for Claude cookie lookup |

| Body field | Type | Default | Description |
|---|---|---|---|
| `model` | string | `claude-sonnet-4-5` | Claude Web model name |
| `messages` | array | required | OpenAI-style message list |
| `stream` | boolean | `false` | Enable SSE streaming |
| `max_tokens` | integer | `4096` | Max tokens in response |

Claude Web v1 on this repo supports text chat and `data:` URL images. Server-side tool calling is available via the [`enable_tools`](#tool-calling-enable_tools) flag.

### POST /tokens/claude/browser

Validate Claude browser cookies and return a masked session preview.

| Param | Type | Default | Description |
|---|---|---|---|
| `force_refresh` | boolean | `false` | Force re-extraction from browser (ignore cache) |

### GET /tokens/claude/browser/profiles

Validate all discoverable Claude browser profiles and return masked previews for each one.

| Param | Type | Default | Description |
|---|---|---|---|
| `force_refresh` | boolean | `false` | Force re-extraction from browser (ignore cache) |

### POST /v1/gemini/chat/completions

OpenAI-compatible Gemini Web endpoint exposed separately from the default ChatGPT route.

| Header | Required | Description |
|---|---|---|
| `Authorization` | Yes | `Bearer gemini-browser`, `Bearer browser`, `Bearer <cookie_header>`, or `Bearer <__Secure-1PSID>|<__Secure-1PSIDTS>` |
| `Content-Type` | Yes | `application/json` |
| `gemini-profile` | No | Browser profile override for Gemini cookie lookup |

| Body field | Type | Default | Description |
|---|---|---|---|
| `model` | string | `gemini-3-pro` | Gemini Web model name |
| `messages` | array | required | OpenAI-style message list |
| `stream` | boolean | `false` | Enable SSE streaming |
| `stream_final_json` | boolean | `false` | Append a final `response.completed` SSE event containing the aggregated JSON response |
| `max_tokens` | integer | `4096` | Approximate max tokens in response |
| `temporary` | boolean | `false` | Use Gemini temporary mode when supported |

Gemini v1 on this repo supports text chat and local-path / `data:` URL files. Server-side tool calling is available via the [`enable_tools`](#tool-calling-enable_tools) flag.

### POST /tokens/gemini/browser

Validate Gemini browser cookies and return a masked session preview based on `__Secure-1PSID`.

| Param | Type | Default | Description |
|---|---|---|---|
| `force_refresh` | boolean | `false` | Force re-extraction from browser (ignore cache) |

### GET /tokens/gemini/browser/profiles

Validate all discoverable Gemini browser profiles and return masked previews for each one.

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
    "model": "gpt-5.4",
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
    "model": "gpt-5.4",
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
    "model": "gpt-5.4",
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
    "model": "gpt-5.4",
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
    "model": "gpt-5.4",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

### Browser token check

```bash
curl -X POST http://localhost:8000/tokens/browser
```

### Claude browser token check

```bash
curl -X POST http://localhost:8000/tokens/claude/browser
```

### Claude browser profiles check

```bash
curl http://localhost:8000/tokens/claude/browser/profiles
```

### Gemini browser token check

```bash
curl -X POST http://localhost:8000/tokens/gemini/browser
```

### Gemini browser profiles check

```bash
curl http://localhost:8000/tokens/gemini/browser/profiles
```

### Force token refresh

Bypass the in-memory cache and re-extract cookies + re-fetch the access token:

```bash
curl -X POST "http://localhost:8000/tokens/browser?force_refresh=true"
```

For Claude:

```bash
curl -X POST "http://localhost:8000/tokens/claude/browser?force_refresh=true"
```

For Gemini:

```bash
curl -X POST "http://localhost:8000/tokens/gemini/browser?force_refresh=true"
```

### Tool calling (enable_tools)

Set `"enable_tools": true` in the request body to give the model access to server-side built-in tools. The server prompt-injects tool instructions into the system message, runs any tool calls itself, feeds results back to the model, and returns only the final answer to the client.

This works uniformly across **all four providers** (ChatGPT, Claude, Gemini, Grok), because tool calling is implemented entirely via prompt injection — the underlying web backends don't need to support tool calling natively.

**Currently registered built-in tools:**

| Tool | Arguments | Description |
|---|---|---|
| `web_search` | `query: str` | DuckDuckGo HTML search; returns top 5 results (title, url, snippet). No API key required. |

**Request shape:**

```json
{
  "model": "claude-sonnet-4-5",
  "messages": [{"role": "user", "content": "What did SpaceX launch this week?"}],
  "enable_tools": true,
  "stream": true
}
```

**One curl per provider** (using canonical model names):

```bash
# ChatGPT
curl -N http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.4","messages":[{"role":"user","content":"What did SpaceX launch this week? Cite sources."}],"enable_tools":true,"stream":true}'

# Claude
curl -N http://localhost:8000/v1/claude/chat/completions \
  -H "Authorization: Bearer claude-browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5","messages":[{"role":"user","content":"What did SpaceX launch this week? Cite sources."}],"enable_tools":true,"stream":true}'

# Gemini
curl -N http://localhost:8000/v1/gemini/chat/completions \
  -H "Authorization: Bearer gemini-browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3-pro","messages":[{"role":"user","content":"What did SpaceX launch this week? Cite sources."}],"enable_tools":true,"stream":true}'

# Grok
curl -N http://localhost:8000/v1/grok/chat/completions \
  -H "Authorization: Bearer browser" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4.3","messages":[{"role":"user","content":"What did SpaceX launch this week? Cite sources."}],"enable_tools":true,"stream":true}'
```

**Notes:**

- Opt-in only. Without `enable_tools: true`, behavior is identical to today.
- The server controls the tool catalog. The OpenAI-style `tools` and `tool_choice` request fields are ignored for this first cut.
- Tool calls use a fenced JSON format the model emits: ` ```tool_call\n{"name":"web_search","arguments":{"query":"..."}}\n``` `. Tool results are fed back as a `tool_result` block in the next user turn.
- Hard cap: 5 tool calls per request (override with `TOOL_LOOP_MAX_ITERS`).
- Streaming responses are emitted as a single chunk once the loop produces the final answer (the loop runs each turn non-streaming internally).

---

## Response Formats

### Non-streaming response

```json
{
  "id": "chatcmpl-t6sCRpYEEWWX1q1tSfJyAnrxlNlWx",
  "object": "chat.completion",
  "created": 1776679436,
  "model": "gpt-5.4",
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
data: {"id":"chatcmpl-WNy...","object":"chat.completion.chunk","created":1776679457,"model":"gpt-5.4","choices":[{"index":0,"delta":{"role":"assistant","content":""},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-WNy...","object":"chat.completion.chunk","created":1776679457,"model":"gpt-5.4","choices":[{"index":0,"delta":{"content":"1  \n2"},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-WNy...","object":"chat.completion.chunk","created":1776679457,"model":"gpt-5.4","choices":[{"index":0,"delta":{"content":"  \n3  \n4  \n5"},"logprobs":null,"finish_reason":"stop"}]}

data: [DONE]
```

For Gemini only, if you send `"stream_final_json": true`, the stream appends one extra SSE event before `[DONE]`:

```text
event: response.completed
data: {"id":"chatcmpl-...","object":"chat.completion","created":1776772341,"model":"gemini-3-pro","choices":[{"index":0,"message":{"role":"assistant","content":"1\n2\n3"},"logprobs":null,"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":1,"total_tokens":13}}
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

### Claude

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_BROWSER_AUTH` | `false` | **Must be `true`** to use Claude browser cookie auth |
| `CLAUDE_BROWSER_AUTH_ALLOW_REMOTE` | `false` | Allow remote callers for Claude endpoint |
| `CLAUDE_BROWSER` | auto-detect | Which browser to try first for Claude cookies: `arc`, `chrome`, `edge`, `firefox`, `brave` |
| `CLAUDE_CHROME_PROFILE` | auto-detect | Chromium profile name for Claude cookies |
| `CLAUDE_COOKIE` | | Raw cookie string override for `claude.ai` |
| `CLAUDE_BASE_URL` | `https://claude.ai` | Claude Web base URL |

### Gemini

| Variable | Default | Description |
|---|---|---|
| `GEMINI_BROWSER_AUTH` | `false` | **Must be `true`** to use Gemini browser cookie auth |
| `GEMINI_BROWSER_AUTH_ALLOW_REMOTE` | `false` | Allow remote callers for Gemini endpoint |
| `GEMINI_BROWSER` | auto-detect | Which browser to try first for Gemini cookies: `arc`, `chrome`, `edge`, `firefox`, `brave`, `zen` |
| `GEMINI_CHROME_PROFILE` | auto-detect | Chromium profile name for Gemini cookies |
| `GEMINI_COOKIE` | | Raw cookie string override for `google.com` / `gemini.google.com` |
| `GEMINI_SECURE_1PSID` | | Direct `__Secure-1PSID` cookie value |
| `GEMINI_SECURE_1PSIDTS` | | Direct `__Secure-1PSIDTS` cookie value |
| `GEMINI_PROXY` | | Proxy override for Gemini requests |

### Behavior

| Variable | Default | Description |
|---|---|---|
| `HISTORY_DISABLED` | `true` | Don't save conversations in ChatGPT history |
| `CONVERSATION_ONLY` | `false` | Skip sentinel/chat-requirements handshake (dev only) |
| `TOOL_LOOP_MAX_ITERS` | `5` | Max tool-call iterations per request when `enable_tools` is set |

### Grok

| Variable | Default | Description |
|---|---|---|
| `GROK_BROWSER_AUTH` | `true` | Enable Grok cookie auth |
| `GROK_BROWSER_AUTH_ALLOW_REMOTE` | `false` | Allow remote callers for Grok endpoint |
| `GROK_BROWSER` | auto-detect | Which browser to try first for Grok cookies: `arc`, `chrome`, `edge`, `firefox`, `brave` |
| `GROK_CHROME_PROFILE` | auto-detect | Chromium profile name for Grok cookies |
| `GROK_COOKIE` | | Raw cookie string override — skips browser extraction (`name=value; name2=value2`) |
| `GROK_PROXY` | | Proxy for Grok (e.g. `socks4://host:port`) — useful for region blocks |
| `GROK_X_STATSIG_ID` | auto-captured | Override the x-statsig-id header. If unset, a headless Chromium session captures it from grok.com on first request. |
| `GROK_BROWSER_EXECUTABLE` | auto-detect | Path to chrome/chromium/brave binary for the bootstrap browser. Auto-detects `/usr/bin/chromium`, `/usr/bin/google-chrome`, `/usr/bin/brave`. |
| `GROK_BROWSER_HEADLESS` | `true` | Run the bootstrap browser headless. Set `false` to debug visually. |
| `GROK_BOOTSTRAP_TIMEOUT` | `30` | Seconds to wait for the browser to capture `x-statsig-id`. |
| `GROK_SESSION_TTL` | `900` | Cached session lifetime (seconds) before forcing a re-bootstrap. |

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

# Claude
CLAUDE_BROWSER_AUTH=true
CLAUDE_BROWSER=brave
CLAUDE_CHROME_PROFILE=Default
CLAUDE_COOKIE=
CLAUDE_BASE_URL=https://claude.ai

# Gemini
GEMINI_BROWSER_AUTH=true
GEMINI_BROWSER=brave
GEMINI_CHROME_PROFILE=Default
GEMINI_COOKIE=
GEMINI_SECURE_1PSID=
GEMINI_SECURE_1PSIDTS=
GEMINI_PROXY=

# Grok
GROK_BROWSER_AUTH=true
GROK_BROWSER=brave
GROK_CHROME_PROFILE=Default
GROK_COOKIE=
GROK_PROXY=
GROK_X_STATSIG_ID=
GROK_BROWSER_EXECUTABLE=
GROK_BROWSER_HEADLESS=true
GROK_BOOTSTRAP_TIMEOUT=30
GROK_SESSION_TTL=900

# Behavior
HISTORY_DISABLED=true
CONVERSATION_ONLY=false
```

---

## Security Model

### Browser cookie auth is disabled by default

You must explicitly set `CHATGPT_BROWSER_AUTH=true`. Without it, every request returns `403`.

Claude behaves the same way on its separate endpoint: set `CLAUDE_BROWSER_AUTH=true` before using `Bearer claude-browser` or `Bearer browser` on `/v1/claude/chat/completions`.

Gemini behaves the same way on `/v1/gemini/chat/completions`: set `GEMINI_BROWSER_AUTH=true` before using `Bearer gemini-browser` or `Bearer browser`.

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
- Claude browser-session validation is also cached in memory by cookie hash for a short TTL

---

## Supported Models

The `model` field in the request body is matched by substring. The first match wins.

| Request model | ChatGPT backend model |
|---|---|
| `gpt-5.4` | `gpt-5.4` |
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

### Grok Models

Grok model names are passed through directly to the Grok backend.

| Request model | Grok backend model |
|---|---|
| `grok-4.3` | `grok-4.3` |
| `grok-4.2` | `grok-4.2` |
| `grok-3` | `grok-3` |
| `grok-2` | `grok-2` |
| `grok-beta` | `grok-beta` |
| Anything else | `grok-3` (fallback) |

Model availability depends on your xAI account tier.

---

### Claude Models

Claude model names are passed through directly to Claude Web.

| Request model | Claude backend model |
|---|---|
| `claude-sonnet-4-5` | `claude-sonnet-4-5` |
| `claude-opus-4-5` | `claude-opus-4-5` |
| `claude-sonnet-4-0` | `claude-sonnet-4-0` |
| `claude-opus-4-0` | `claude-opus-4-0` |
| `claude-3-7-sonnet-latest` | `claude-3-7-sonnet-latest` |
| `claude-3-5-sonnet-latest` | `claude-3-5-sonnet-latest` |
| Anything else | Passed through as-is |

Availability depends on your Claude account tier and whatever models Claude Web currently exposes to that account.

---

### Gemini Models

Gemini model names are passed through directly to `gemini-webapi`, which discovers what your account can use at init time.

| Request model | Gemini backend model |
|---|---|
| `gemini-3-pro` | `gemini-3-pro` |
| `gemini-3-flash` | `gemini-3-flash` |
| `gemini-3-flash-thinking` | `gemini-3-flash-thinking` |
| `gemini-2.5-pro` | `gemini-2.5-pro` |
| `gemini-2.5-flash` | `gemini-2.5-flash` |
| Anything else | Passed through as-is |

Availability depends on your Google account tier and the Gemini Web models currently exposed to that account.

---

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

## Grok Browser Bootstrap

Grok's backend requires an `x-statsig-id` header — a signed Statsig stable ID that the grok.com frontend generates at runtime. Without a valid value, every `POST /rest/app-chat/conversations/new` returns **401**, regardless of cookie quality. There's no public way to compute this header from scratch.

Chat-to-LLM solves this by briefly driving a real browser with Playwright.

### How it works

1. Extract the user's grok.com cookies from their installed browser (via `browser-cookie3`).
2. Launch headless Chromium (system binary — no bundled download).
3. Seed the new browser context with the extracted cookies, then navigate to `https://grok.com/`.
4. Listen for outbound network requests. When the grok.com frontend calls its own REST API (triggered by the page load and a nudge `fetch('/rest/app-chat/conversations?pageSize=1')`), grab the `x-statsig-id` header it attaches.
5. Harvest the full cookie jar — this includes Cloudflare's `cf_clearance` cookie that the browser just obtained.
6. Close the browser. Subsequent HTTP requests use `curl_cffi` with the captured `x-statsig-id` + refreshed cookies.

### Caching

- The captured `(cookie_header, statsig_id)` pair is cached in memory, keyed by the SHA-256 hash of the original cookie header.
- TTL is 15 minutes by default (`GROK_SESSION_TTL`). Inside that window, requests skip the browser entirely.
- On `401`/`403` from Grok the service force-refreshes the session once before giving up.
- Force refresh manually: `POST /tokens/grok/browser?force_refresh=true`

### Cost

- First request: ~3-5 seconds (browser spawn + navigate + statsig capture).
- Cached requests: under 2 seconds, no browser activity.
- One short-lived Chromium process per bootstrap — closed before the HTTP response returns.

### Requirements

- A Chromium-compatible browser binary on the host: `/usr/bin/chromium`, `/usr/bin/google-chrome`, or `/usr/bin/brave`. Override the path with `GROK_BROWSER_EXECUTABLE` if your install is elsewhere.
- Playwright installed (`uv sync` handles this). Playwright uses the system binary via `executable_path`, so you **do not** need to run `playwright install`.

### Skipping the bootstrap

If you already have a fresh `x-statsig-id` (e.g., copied from DevTools) and want to avoid the browser spawn entirely:

```bash
GROK_X_STATSIG_ID=<paste-from-devtools> uv run python app.py
```

The server will skip the bootstrap and use the override directly. IDs rotate, so this is best for short-lived debugging sessions.

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

## References

- ChatGPT reference: [chat2api](https://github.com/lanqian528/chat2api)
- Claude reference: [clewdr](https://github.com/Xerxes-2/clewdr)
- Grok reference: [Grok3API](https://github.com/boykopovar/Grok3API)

### Project structure

```
chat_to_llm/
├── app.py                  # FastAPI app + uvicorn entrypoint
├── pyproject.toml          # uv-managed project
├── .env.example            # Example environment variables
├── providers/
│   ├── chatgpt/            # ChatGPT provider
│   │   ├── auth.py         # Cookie extraction + security gates + session exchange
│   │   ├── client.py       # curl_cffi async HTTP client wrapper
│   │   ├── fp.py           # Browser fingerprint generation
│   │   ├── pow.py          # Proof-of-Work solver + DPL hash fetch
│   │   ├── formatting.py   # ChatGPT SSE → OpenAI format conversion
│   │   └── service.py      # ChatGPT backend request builder/sender
│   ├── claude/             # Claude Web provider
│   └── grok/               # Grok provider (cookie + headless-browser bootstrap)
│       ├── auth.py                # Cookie extraction + session cache
│       ├── browser_bootstrap.py   # Playwright-driven x-statsig-id capture
│       ├── formatting.py          # OpenAI response formatting
│       ├── service.py             # Grok HTTP request builder/sender
│       └── types.py               # GrokResponse / ModelResponse dataclasses
├── api/
│   └── chat.py             # Route handlers for all providers
└── tests/
    ├── test_browser_auth.py
    ├── test_chatgpt/
    ├── test_claude/
    └── test_grok/
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
    model="gpt-5.4",
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
    model="gpt-5.4",
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
    "model": "gpt-5.4",
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

### Grok — "No Grok cookies found"

1. Make sure you're logged into [grok.com](https://grok.com) in your browser
2. Try specifying your browser: `GROK_BROWSER=chrome`
3. Try a specific profile: `GROK_CHROME_PROFILE="Profile 1"`
4. On Linux, Chrome cookies may be in `~/.config/google-chrome/Default/Cookies`
5. Or provide raw cookies directly: `GROK_COOKIE="name=value; name2=value2"`

### Grok — "Grok cookies are expired or invalid"

Log back into [grok.com](https://grok.com) in your browser, then retry. The server will automatically pick up the new cookies.

### Grok — "Failed to bootstrap Grok browser session"

The headless Chromium session failed to capture an `x-statsig-id`. Common causes:

- **No browser binary found.** Install Chromium, Chrome, or Brave, or point `GROK_BROWSER_EXECUTABLE` at the binary.
- **Bootstrap timed out.** Cold starts on slow disks can exceed 30s; raise the budget: `GROK_BOOTSTRAP_TIMEOUT=60`.
- **Cookies are stale.** Re-login to grok.com in your browser so `browser-cookie3` can extract a valid session.
- **Debug visually.** Set `GROK_BROWSER_HEADLESS=false` to watch the browser load grok.com and see what happens.

### Grok — "Grok auth failed after refresh: HTTP 401"

The bootstrap succeeded but Grok still rejected the request. Your cookies are most likely revoked or expired. Re-login to grok.com and retry. You can also skip the bootstrap with a hand-copied header: `GROK_X_STATSIG_ID=<value from DevTools>`.

### Grok — "This service is not available in your region"

Grok is region-blocked. Set a proxy (applied to both the bootstrap browser and the HTTP client):
```bash
GROK_PROXY=socks4://98.178.72.21:10919 uv run python app.py
```

### Grok — bootstrap is slow on every request

If every request takes 3+ seconds, the session cache isn't being hit. Check:
- `GROK_SESSION_TTL` — defaults to 900s; lower values force more frequent re-bootstraps.
- Your extracted cookie header is stable across calls (changing cookies invalidate the cache key).
