from __future__ import annotations

import types
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.background import BackgroundTask

from chatgpt.browser_auth import (
    ensure_browser_auth_request_allowed,
    get_all_browser_sessions,
    get_browser_session,
    is_browser_auth_token,
    parse_browser_token,
)
from chatgpt.service import ChatService


async def _process(request_data: dict, req_token: Optional[str], profile: Optional[str] = None):
    chat_service = ChatService(req_token)
    try:
        await chat_service.set_dynamic_data(request_data, profile=profile)
        await chat_service.get_chat_requirements()
    except HTTPException:
        await chat_service.close_client()
        raise
    except Exception as e:
        await chat_service.close_client()
        raise HTTPException(status_code=500, detail="Server error: %s" % e)

    await chat_service.prepare_send_conversation()
    res = await chat_service.send_conversation()
    return chat_service, res


def register_routes(app) -> None:
    @app.post("/v1/chat/completions")
    async def send_conversation(
        request: Request,
        credentials: HTTPAuthorizationCredentials = Security(app.state.security_scheme),
    ):
        req_token = credentials.credentials
        is_browser, profile_from_token = (
            parse_browser_token(req_token) if is_browser_auth_token(req_token) else (False, None)
        )
        if is_browser:
            ensure_browser_auth_request_allowed(request)

        profile = request.headers.get("chatgpt-profile") or profile_from_token

        try:
            request_data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"error": "Invalid JSON body"})

        chat_service, res = await _process(request_data, req_token, profile=profile)
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

    @app.post("/tokens/browser")
    async def browser_token(request: Request, force_refresh: bool = False):
        ensure_browser_auth_request_allowed(request)
        session = await get_browser_session(force_refresh=force_refresh)
        return {
            "status": "success",
            "access_token_preview": session.get("access_token_preview"),
            "expires_at": session.get("expires_at"),
            "user": session.get("user"),
        }

    @app.get("/tokens/browser/profiles")
    async def browser_profiles(request: Request, force_refresh: bool = False):
        ensure_browser_auth_request_allowed(request)
        all_sessions = await get_all_browser_sessions(force_refresh=force_refresh)
        profiles = []
        for profile_key, session in all_sessions.items():
            entry = {
                "profile": profile_key,
                "access_token_preview": session.get("access_token_preview"),
                "expires_at": session.get("expires_at"),
                "user": session.get("user"),
            }
            if "error" in session:
                entry["error"] = session["error"]
                entry["status"] = "error"
            else:
                entry["status"] = "success"
            profiles.append(entry)
        return {"profiles": profiles}
