from __future__ import annotations

from providers.tools.web_search import _decode_ddg_redirect, _truncate, parse_results


_SAMPLE_HTML = """
<div class="results">
  <div class="result">
    <h2 class="result__title">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=1">Example A &amp; Friends</a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First result <b>snippet</b> here.</a>
  </div>
  <div class="result">
    <h2 class="result__title">
      <a class="result__a" href="https://direct.example.org/page">Direct Result</a>
    </h2>
    <a class="result__snippet">Second snippet text.</a>
  </div>
  <div class="result">
    <h2 class="result__title">
      <a class="result__a" href="https://third.example.com/">Third</a>
    </h2>
    <a class="result__snippet">Third snippet.</a>
  </div>
</div>
"""


class TestDecodeRedirect:
    def test_decodes_ddg_redirect(self):
        url = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=1"
        assert _decode_ddg_redirect(url) == "https://example.com/a"

    def test_passes_through_direct_url(self):
        url = "https://example.org/page"
        assert _decode_ddg_redirect(url) == "https://example.org/page"


class TestParseResults:
    def test_parses_titles_urls_snippets(self):
        results = parse_results(_SAMPLE_HTML, max_results=5)
        assert len(results) == 3
        assert results[0]["title"] == "Example A & Friends"
        assert results[0]["url"] == "https://example.com/a"
        assert results[0]["snippet"] == "First result snippet here."
        assert results[1]["url"] == "https://direct.example.org/page"
        assert results[1]["snippet"] == "Second snippet text."

    def test_respects_max_results(self):
        results = parse_results(_SAMPLE_HTML, max_results=2)
        assert len(results) == 2

    def test_empty_html(self):
        assert parse_results("", max_results=5) == []


class TestTruncate:
    def test_truncates_when_over_budget(self):
        results = [
            {"title": "t1", "url": "u1", "snippet": "x" * 200},
            {"title": "t2", "url": "u2", "snippet": "y" * 200},
        ]
        out = _truncate(results, max_total_chars=100)
        total = sum(len(r["title"]) + len(r["url"]) + len(r["snippet"]) for r in out)
        assert total <= 110  # small slack for ellipsis char

    def test_does_not_modify_when_under_budget(self):
        results = [{"title": "t", "url": "u", "snippet": "short"}]
        out = _truncate(results, max_total_chars=4000)
        assert out == results
