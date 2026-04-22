from __future__ import annotations

import types
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.background import BackgroundTask

from providers import get_provider


def _profile_from_headers(request: Request, provider_name: str) -> Optional[str]:
    return request.headers.get(f"{provider_name}-profile") or request.headers.get(
        "provider-profile"
    )


def _get_profile_from_request(
    request: Request, provider_name: str, auth, req_token: str
) -> Optional[str]:
    is_browser, profile_from_token = (
        auth.parse_browser_token(req_token) if auth.is_browser_token(req_token) else (False, None)
    )
    if is_browser:
        auth.ensure_browser_auth_request_allowed(request)
    return _profile_from_headers(request, provider_name) or profile_from_token


async def _process_chat(
    provider_name: str, request_data: dict, req_token: Optional[str], profile: Optional[str] = None
):
    entry = get_provider(provider_name)
    service = entry.service(req_token)
    try:
        await service.set_dynamic_data(request_data, profile=profile)
        await service.get_chat_requirements()
        await service.prepare_send_conversation()
        res = await service.send_conversation()
        return service, res
    except HTTPException:
        await service.close_client()
        raise
    except Exception as e:
        await service.close_client()
        raise HTTPException(status_code=500, detail="Server error: %s" % e)


def _make_chat_route(provider_name: str, security_scheme):
    entry = get_provider(provider_name)

    async def send_conversation(
        request: Request,
        credentials: HTTPAuthorizationCredentials = Security(security_scheme),
    ):
        req_token = credentials.credentials
        profile = _get_profile_from_request(request, provider_name, entry.auth(), req_token)

        try:
            request_data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"error": "Invalid JSON body"})

        chat_service, res = await _process_chat(
            provider_name, request_data, req_token, profile=profile
        )
        try:
            if isinstance(res, types.AsyncGeneratorType):
                background = BackgroundTask(chat_service.close_client)
                return StreamingResponse(res, media_type="text/event-stream", background=background)
            else:
                background = BackgroundTask(chat_service.close_client)
                return JSONResponse(res, media_type="application/json", background=background)
        except HTTPException:
            await chat_service.close_client()
            raise
        except Exception:
            await chat_service.close_client()
            raise HTTPException(status_code=500, detail="Server error")

    return send_conversation


def _make_browser_token_route(provider_name: str):
    entry = get_provider(provider_name)

    async def browser_token(request: Request, force_refresh: bool = False):
        auth = entry.auth()
        auth.ensure_browser_auth_request_allowed(request)
        session = await auth.get_browser_session(
            force_refresh=force_refresh,
            profile=_profile_from_headers(request, provider_name),
        )
        return {
            "status": "success",
            "access_token_preview": session.get("access_token_preview"),
            "expires_at": session.get("expires_at"),
            "user": session.get("user"),
        }

    return browser_token


def _make_browser_profiles_route(provider_name: str):
    entry = get_provider(provider_name)

    async def browser_profiles(request: Request, force_refresh: bool = False):
        auth = entry.auth()
        auth.ensure_browser_auth_request_allowed(request)
        all_sessions = await auth.get_all_sessions(force_refresh=force_refresh)
        profiles = []
        for profile_key, session in all_sessions.items():
            entry_data = {
                "profile": profile_key,
                "access_token_preview": session.get("access_token_preview"),
                "expires_at": session.get("expires_at"),
                "user": session.get("user"),
            }
            if "error" in session:
                entry_data["error"] = session["error"]
                entry_data["status"] = "error"
            else:
                entry_data["status"] = "success"
            profiles.append(entry_data)
        return {"profiles": profiles}

    return browser_profiles


def register_routes(app) -> None:
    security_scheme = app.state.security_scheme

    # ChatGPT (legacy / default endpoints)
    app.post("/v1/chat/completions")(_make_chat_route("chatgpt", security_scheme))
    app.post("/tokens/browser")(_make_browser_token_route("chatgpt"))
    app.get("/tokens/browser/profiles")(_make_browser_profiles_route("chatgpt"))

    # Claude
    app.post("/v1/claude/chat/completions")(_make_chat_route("claude", security_scheme))
    app.post("/tokens/claude/browser")(_make_browser_token_route("claude"))
    app.get("/tokens/claude/browser/profiles")(_make_browser_profiles_route("claude"))

    # Gemini
    app.post("/v1/gemini/chat/completions")(_make_chat_route("gemini", security_scheme))
    app.post("/tokens/gemini/browser")(_make_browser_token_route("gemini"))
    app.get("/tokens/gemini/browser/profiles")(_make_browser_profiles_route("gemini"))

    # Grok
    app.post("/v1/grok/chat/completions")(_make_chat_route("grok", security_scheme))
    app.post("/tokens/grok/browser")(_make_browser_token_route("grok"))
    app.get("/tokens/grok/browser/profiles")(_make_browser_profiles_route("grok"))
