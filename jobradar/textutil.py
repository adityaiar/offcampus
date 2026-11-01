from __future__ import annotations

import re

from bs4 import BeautifulSoup


def strip_html(s: str) -> str:
    if not s:
        return ""
    text = BeautifulSoup(s, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def word_match(keyword: str, text: str) -> bool:
    """Case-insensitive whole-word/phrase match (so 'tax' doesn't match 'taxonomy')."""
    kw = keyword.strip().lower()
    if not kw:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None
