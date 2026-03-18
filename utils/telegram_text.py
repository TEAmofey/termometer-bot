from __future__ import annotations

from html import escape


TELEGRAM_TEXT_LIMIT = 4096


def fits_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> bool:
    return len(text) <= limit


def split_text_for_html(text: str, max_escaped_len: int) -> list[str]:
    if max_escaped_len <= 0:
        raise ValueError("max_escaped_len must be positive")

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(escape(remaining)) <= max_escaped_len:
            chunks.append(remaining)
            break

        current: list[str] = []
        last_break_index = -1
        index = 0

        while index < len(remaining):
            candidate = "".join(current) + remaining[index]
            if len(escape(candidate)) > max_escaped_len:
                break
            current.append(remaining[index])
            if remaining[index].isspace():
                last_break_index = len(current)
            index += 1

        if not current:
            raise ValueError("Could not split text into Telegram-safe HTML chunks")

        if last_break_index > 0:
            # Preserve whitespace exactly as the user sent it.
            chunk = "".join(current[:last_break_index])
            remaining = remaining[last_break_index:]
        else:
            chunk = "".join(current)
            remaining = remaining[len(chunk):]

        chunks.append(chunk)

    return chunks


def shorten_text_for_html_preview(text: str, max_escaped_len: int, suffix: str) -> str:
    escaped_suffix_len = len(escape(suffix))
    if escaped_suffix_len >= max_escaped_len:
        raise ValueError("Suffix is too long for the requested preview length")

    if len(escape(text)) <= max_escaped_len:
        return text

    preview_limit = max_escaped_len - escaped_suffix_len
    preview = split_text_for_html(text, preview_limit)[0].rstrip()
    return f"{preview}{suffix}"
