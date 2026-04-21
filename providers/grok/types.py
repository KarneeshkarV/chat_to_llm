from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GeneratedImage:
    """Represents a generated image from Grok."""

    url: str
    _base_url: str = "https://assets.grok.com"

    @property
    def full_url(self) -> str:
        if self.url.startswith("http"):
            return self.url
        return self._base_url + (self.url if self.url.startswith("/") else "/" + self.url)


@dataclass
class ModelResponse:
    responseId: str = ""
    message: str = ""
    sender: str = ""
    createTime: str = ""
    parentResponseId: str = ""
    manual: bool = False
    partial: bool = False
    shared: bool = False
    query: str = ""
    queryType: str = ""
    webSearchResults: List[Any] = field(default_factory=list)
    xpostIds: List[Any] = field(default_factory=list)
    xposts: List[Any] = field(default_factory=list)
    generatedImages: List[GeneratedImage] = field(default_factory=list)
    imageAttachments: List[Any] = field(default_factory=list)
    fileAttachments: List[Any] = field(default_factory=list)
    cardAttachmentsJson: List[Any] = field(default_factory=list)
    fileUris: List[Any] = field(default_factory=list)
    fileAttachmentsMetadata: List[Any] = field(default_factory=list)
    isControl: bool = False
    steps: List[Any] = field(default_factory=list)
    mediaTypes: List[Any] = field(default_factory=list)

    def __init__(self, data: Dict[str, Any], enable_artifact_files: bool = False) -> None:
        try:
            self.responseId = data.get("responseId", "")
            self.sender = data.get("sender", "")
            self.createTime = data.get("createTime", "")
            self.parentResponseId = data.get("parentResponseId", "")
            self.manual = data.get("manual", False)
            self.partial = data.get("partial", False)
            self.shared = data.get("shared", False)
            self.query = data.get("query", "")
            self.queryType = data.get("queryType", "")
            self.webSearchResults = data.get("webSearchResults", [])
            self.xpostIds = data.get("xpostIds", [])
            self.xposts = data.get("xposts", [])

            raw_message = data.get("message", "")
            self.message = (
                raw_message if enable_artifact_files else self._transform_xai_artifacts(raw_message)
            )

            self.generatedImages = []
            for url in data.get("generatedImageUrls", []):
                self.generatedImages.append(GeneratedImage(url=url))

            self.imageAttachments = data.get("imageAttachments", [])
            self.fileAttachments = data.get("fileAttachments", [])
            self.cardAttachmentsJson = data.get("cardAttachmentsJson", [])
            self.fileUris = data.get("fileUris", [])
            self.fileAttachmentsMetadata = data.get("fileAttachmentsMetadata", [])
            self.isControl = data.get("isControl", False)
            self.steps = data.get("steps", [])
            self.mediaTypes = data.get("mediaTypes", [])
        except Exception as exc:
            logger.error("Error in ModelResponse.__init__: %s", exc)

    @staticmethod
    def _transform_xai_artifacts(text: str) -> str:
        """Convert xaiArtifact blocks and x-lang markers to standard Markdown code fences."""

        def replace_artifact(match: re.Match) -> str:
            lang = match.group(1).strip()
            code = match.group(2).strip()
            return f"```{lang}\n{code}\n```"

        text = re.sub(
            r'<xaiArtifact[^>]*?contentType="text/([^"]+)"[^>]*>(.*?)</xaiArtifact>',
            replace_artifact,
            text,
            flags=re.DOTALL,
        )

        text = re.sub(
            r"```x-([a-zA-Z0-9_+-]+)src\b",
            lambda m: f"```{m.group(1)}",
            text,
        )

        text = re.sub(
            r"```x-([a-zA-Z0-9_+-]+)\b(?![a-zA-Z0-9_-]*src)",
            lambda m: f"```{m.group(1)}",
            text,
        )

        return text


@dataclass
class GrokResponse:
    modelResponse: ModelResponse
    isThinking: bool = False
    isSoftStop: bool = False
    responseId: str = ""
    conversationId: Optional[str] = None
    title: Optional[str] = None
    conversationCreateTime: Optional[str] = None
    conversationModifyTime: Optional[str] = None
    temporary: Optional[bool] = None
    error: Optional[str] = None
    error_code: Optional[Any] = None

    def __init__(self, data: Dict[str, Any], enable_artifact_files: bool = False) -> None:
        try:
            self.error = data.get("error", None)
            self.error_code = data.get("error_code", None)
            result = data.get("result", {})
            response_data = result.get("response", {})

            self.modelResponse = ModelResponse(
                response_data.get("modelResponse", {}), enable_artifact_files
            )
            self.isThinking = response_data.get("isThinking", False)
            self.isSoftStop = response_data.get("isSoftStop", False)
            self.responseId = response_data.get("responseId", "")

            self.conversationId = response_data.get("conversationId")
            self.title = result.get("newTitle") or result.get("title")
            self.conversationCreateTime = response_data.get("createTime")
            self.conversationModifyTime = response_data.get("modifyTime")
            self.temporary = response_data.get("temporary")
        except Exception as exc:
            self.error = str(exc) if self.error is None else f"{self.error} {exc}"
            logger.error("Error in GrokResponse.__init__: %s", exc)
