from __future__ import annotations


from providers.grok.service import GrokService


class TestGrokService:
    def test_init(self):
        svc = GrokService(token="browser")
        assert svc.req_token == "browser"
        assert svc._auth.is_browser_token("browser") is True

    def test_parse_messages_text_only(self):
        svc = GrokService()
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        text, images = svc._parse_messages(messages)
        assert "system: You are helpful" in text
        assert "Hello" in text
        assert images == []

    def test_parse_messages_with_image_data_url(self):
        svc = GrokService()
        b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ]
        text, images = svc._parse_messages(messages)
        assert text == "Describe this"
        assert len(images) == 1

    def test_build_payload(self):
        svc = GrokService()
        svc.origin_model = "grok-3"
        svc._message_text = "Hello"
        svc._file_attachments = ["file123"]
        svc._parent_response_id = "parent1"
        payload = svc._build_payload()
        assert payload["modelName"] == "grok-3"
        assert payload["message"] == "Hello"
        assert payload["fileAttachments"] == ["file123"]
        assert payload["parentResponseId"] == "parent1"

    def test_to_openai_dict(self):
        svc = GrokService()
        svc.origin_model = "grok-3"
        svc.resp_model = "grok-3"
        svc._message_text = "Hello"
        from providers.grok.types import GrokResponse

        gr = GrokResponse(
            {
                "result": {
                    "response": {
                        "modelResponse": {"responseId": "r1", "message": "Hi there"},
                    }
                }
            }
        )
        result = svc._to_openai_dict(gr)
        assert result["object"] == "chat.completion"
        assert result["model"] == "grok-3"
        assert result["choices"][0]["message"]["content"] == "Hi there"
        assert "usage" in result
