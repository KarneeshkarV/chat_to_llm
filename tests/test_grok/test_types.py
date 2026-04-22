from __future__ import annotations


from providers.grok.types import GeneratedImage, GrokResponse, ModelResponse


class TestGeneratedImage:
    def test_full_url_with_relative_path(self):
        img = GeneratedImage(url="/images/test.jpg")
        assert img.full_url == "https://assets.grok.com/images/test.jpg"

    def test_full_url_with_absolute_url(self):
        img = GeneratedImage(url="https://example.com/img.jpg")
        assert img.full_url == "https://example.com/img.jpg"


class TestModelResponse:
    def test_basic_fields(self):
        data = {
            "responseId": "r1",
            "message": "Hello",
            "sender": "assistant",
        }
        mr = ModelResponse(data)
        assert mr.responseId == "r1"
        assert mr.message == "Hello"
        assert mr.sender == "assistant"

    def test_transform_xai_artifacts(self):
        data = {
            "message": '<xaiArtifact contentType="text/python">print(1)</xaiArtifact>',
        }
        mr = ModelResponse(data)
        assert "```python" in mr.message
        assert "print(1)" in mr.message

    def test_transform_x_lang(self):
        data = {
            "message": "```x-pythonsrc\ncode\n```",
        }
        mr = ModelResponse(data)
        assert "```python" in mr.message
        assert "x-pythonsrc" not in mr.message


class TestGrokResponse:
    def test_basic_response(self):
        data = {
            "result": {
                "response": {
                    "modelResponse": {
                        "responseId": "r1",
                        "message": "Hi",
                    },
                    "isThinking": False,
                    "isSoftStop": False,
                    "responseId": "r1",
                },
                "newTitle": "Test Chat",
            }
        }
        gr = GrokResponse(data)
        assert gr.modelResponse.message == "Hi"
        assert gr.title == "Test Chat"

    def test_error_response(self):
        data = {"error": "Something went wrong", "error_code": 500}
        gr = GrokResponse(data)
        assert gr.error == "Something went wrong"
        assert gr.error_code == 500

    def test_generated_images(self):
        data = {
            "result": {
                "response": {
                    "modelResponse": {
                        "responseId": "r1",
                        "message": "Here is an image",
                        "generatedImageUrls": ["/img/1.jpg"],
                    },
                }
            }
        }
        gr = GrokResponse(data)
        assert len(gr.modelResponse.generatedImages) == 1
        assert gr.modelResponse.generatedImages[0].full_url == "https://assets.grok.com/img/1.jpg"
