from __future__ import annotations

from typing import Any, Dict, Optional

from curl_cffi.requests import AsyncSession


class Client:
    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: int = 15,
        verify: bool = True,
        impersonate: str = "chrome120",
    ) -> None:
        self.proxies = {"http": proxy, "https": proxy}
        self.timeout = timeout
        self.verify = verify
        self.impersonate = impersonate
        self.session = AsyncSession(
            proxies=self.proxies,
            timeout=self.timeout,
            impersonate=self.impersonate,
            verify=self.verify,
        )
        self.session2 = AsyncSession(
            proxies=self.proxies,
            timeout=self.timeout,
            impersonate=self.impersonate,
            verify=self.verify,
        )

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        return await self.session.post(*args, **kwargs)

    async def post_stream(self, *args: Any, headers: Optional[Dict] = None, **kwargs: Any) -> Any:
        if self.session:
            headers = headers or self.session.headers
        return await self.session2.post(*args, headers=headers, **kwargs)

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        return await self.session.get(*args, **kwargs)

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        return await self.session.request(*args, **kwargs)

    async def close(self) -> None:
        for attr in ("session", "session2"):
            if hasattr(self, attr):
                try:
                    sess = getattr(self, attr)
                    await sess.close()
                    delattr(self, attr)
                except Exception:
                    pass
