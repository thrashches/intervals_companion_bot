"""Convert Markdown to Telegram-safe HTML for sendMessage parse_mode=HTML."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

import markdown as md_lib

TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Tags Telegram HTML parse_mode accepts (subset).
_ALLOWED_TAGS = frozenset(
    {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a"}
)
_VOID_TAGS = frozenset()  # none of the allowed tags are void


class _TelegramHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "a":
            href = ""
            for name, value in attrs:
                if name.lower() == "href" and value:
                    href = value.strip()
                    break
            if not href or not (
                href.startswith("http://")
                or href.startswith("https://")
                or href.startswith("tg://")
            ):
                return
            self._stack.append("a")
            self._parts.append(f'<a href="{html.escape(href, quote=True)}">')
            return
        # Map markdown-ish tags to Telegram-preferred ones
        mapped = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}.get(
            tag, tag
        )
        self._stack.append(mapped)
        self._parts.append(f"<{mapped}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        mapped = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}.get(
            tag, tag
        )
        if mapped not in _ALLOWED_TAGS and tag not in _ALLOWED_TAGS:
            return
        # Close matching open tag if present
        if self._stack and self._stack[-1] == mapped:
            self._stack.pop()
            self._parts.append(f"</{mapped}>")
        elif mapped == "a" and self._stack and self._stack[-1] == "a":
            self._stack.pop()
            self._parts.append("</a>")

    def handle_data(self, data: str) -> None:
        self._parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._parts.append(f"&#{name};")

    def get_html(self) -> str:
        # Close any leftover open tags
        while self._stack:
            tag = self._stack.pop()
            self._parts.append(f"</{tag}>")
        return "".join(self._parts)


def sanitize_telegram_html(raw_html: str) -> str:
    parser = _TelegramHTMLSanitizer()
    parser.feed(raw_html)
    parser.close()
    return parser.get_html()


def markdown_to_telegram_html(text: str) -> str:
    """Render markdown to a Telegram-safe HTML string."""
    raw = md_lib.markdown(
        text or "",
        extensions=["nl2br", "sane_lists"],
        output_format="html",
    )
    # Drop wrapping <p>…</p> where possible → use newlines between blocks
    raw = re.sub(r"</p>\s*<p>", "\n\n", raw)
    raw = re.sub(r"^<p>", "", raw)
    raw = re.sub(r"</p>$", "", raw)
    # Lists: keep content, flatten to lines with •
    raw = re.sub(r"</?ul>", "", raw)
    raw = re.sub(r"</?ol>", "", raw)
    raw = re.sub(r"<li>", "• ", raw)
    raw = re.sub(r"</li>", "\n", raw)
    # Strip remaining block tags we don't want
    raw = re.sub(r"</?h[1-6]>", "", raw)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"</?blockquote>", "", raw)
    raw = re.sub(r"</?p>", "\n", raw)
    return sanitize_telegram_html(raw).strip()


def format_news_message(title: str, body_md: str) -> str:
    """Build full Telegram HTML message; truncate to 4096 chars if needed."""
    title_html = f"<b>{html.escape(title or '', quote=False)}</b>"
    body_html = markdown_to_telegram_html(body_md)
    if body_html:
        text = f"{title_html}\n\n{body_html}"
    else:
        text = title_html
    if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return text
    # Truncate carefully — avoid cutting mid-tag by stripping tags from tail cut
    limit = TELEGRAM_MAX_MESSAGE_LENGTH - 1  # room for …
    truncated = text[:limit]
    # Drop incomplete trailing tag
    if "<" in truncated and truncated.rfind("<") > truncated.rfind(">"):
        truncated = truncated[: truncated.rfind("<")]
    return truncated.rstrip() + "…"
