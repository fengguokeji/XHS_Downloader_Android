"""Tests for the XiaoHongShu API parser helpers."""

from __future__ import annotations

import unittest

from api.xhs_downloader_api.xhs import XHSDownloaderAPI


def _build_html(payload: str) -> str:
    return f"<html><script>window.__INITIAL_STATE__={payload}</script></html>"


class XHSParserTests(unittest.TestCase):
    """Validate the robustness of the XiaoHongShu HTML parser."""

    def test_normalises_js_tokens_outside_of_object_literals(self) -> None:
        """Ensure JavaScript-only values are converted so JSON parsing succeeds."""

        html = _build_html(
            '{'  # language=JSON5 - intentionally invalid JSON
            '"note":{'
            '"noteDetailMap":{'
            '"abc":{"note":{'
            '"noteId":"abc",'
            '"imageList":[{"urlDefault":"https://sns-img-qc.xhscdn.com/20231111/foo/1040gfoobar!nd_dft_webp"}]'
            '}}}},'
            '"global":{"prefetchId":undefined,"values":[undefined,+Infinity,-Infinity,NaN]}'
            '}'
        )

        api = XHSDownloaderAPI()
        try:
            notes, fallback = api._parse_post_details(html)
        finally:
            api._client.close()

        self.assertFalse(fallback)
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note.metadata["noteId"], "abc")
        self.assertEqual([media.url for media in note.media], ["https://ci.xiaohongshu.com/1040gfoobar"])


if __name__ == "__main__":
    unittest.main()
