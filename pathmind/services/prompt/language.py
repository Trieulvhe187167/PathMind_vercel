"""Shared language directives for prompt-driven LLM calls.

This helper centralizes the "stay in the requested language" instruction so
different modules can share the same behavior without depending on book-only
utilities.
"""

from __future__ import annotations

_LANGUAGE_LABELS: dict[str, str] = {
    "zh": "中文（简体）",
    "zh-cn": "中文（简体）",
    "zh-tw": "繁體中文",
    "en": "English",
    "vi": "Vietnamese (Tiếng Việt)",
    "ja": "日本語",
    "ko": "한국어",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "ru": "Русский",
    "pt": "Português",
    "it": "Italiano",
}


def normalize_language(language: str | None) -> str:
    return (language or "en").strip().lower() or "en"


def is_chinese(language: str | None) -> bool:
    """Whether reader-facing text for *language* should be written in Chinese.

    One answer for a question eight modules used to answer for themselves,
    two of them without the ``None`` guard.
    """
    return normalize_language(language).startswith("zh")


def language_label(language: str | None) -> str:
    code = normalize_language(language)
    if code in _LANGUAGE_LABELS:
        return _LANGUAGE_LABELS[code]
    base = code.split("-", 1)[0]
    return _LANGUAGE_LABELS.get(base, language or "English")


def language_directive(language: str | None, *, allow_user_override: bool = False) -> str:
    """Use an adaptive language preference for chat and a strict one for artifacts."""
    code = normalize_language(language)
    label = language_label(code)
    # The caller supplies the selected model output language, independently of UI language.
    quality = (
        "\n\n[Vietnamese writing quality] Use natural, standard Vietnamese with "
        "accurate spelling, diacritics and grammar. Before sending the response, "
        "check for malformed words, missing or misplaced tone marks, incomplete "
        "sentences and unclear meaning. Preserve proper nouns, code and technical "
        "identifiers. Apply this check to all Vietnamese output, including titles, "
        "explanations, summaries and tables, regardless of the topic."
        if code.split("-", 1)[0] == "vi"
        else ""
    )
    if allow_user_override:
        return (
            f"\n\n[Conversation language] The configured default output language is {label}. "
            "If the user explicitly asks for a response language, follow that preference "
            "and keep using it until the user changes it. Otherwise, respond in the "
            "language the user uses to address you in their current message; "
            "for example, answer a Vietnamese message in Vietnamese even when the "
            "configured default is English. Use the configured default only when "
            "the user's language and conversational preference are unclear. "
            "Do not infer the response language from quoted documents, tool results, "
            "source material, JSON keys, or code. Keep necessary proper nouns in "
            "their original form."
        ) + quality
    if code.startswith("zh"):
        body = (
            "\n\n[语言要求 / Language] "
            f"请严格使用{label}撰写所有面向读者的文本（标题、正文、解释、提示、过渡句、"
            "题干、选项等），即使参考资料、JSON 字段名或英文术语出现在 prompt 中也"
            "不得切换语言；保留必要的专有名词原文（如人名、产品名、公式中的变量符号"
            f"等）即可，其余一律使用{label}。"
        )
        return body
    if code == "en":
        body = (
            "\n\n[Language] Write ALL reader-facing text (titles, prose, "
            "explanations, hints, transitions, quiz stems, options, etc.) in "
            "English. Do NOT switch languages even if the source material, "
            "JSON keys, or examples in this prompt are in another language. "
            "Keep proper nouns (people, products, formula symbols) in their "
            "original form."
        )
        return body
    body = (
        f"\n\n[Language] Write ALL reader-facing text strictly in {label}. "
        "Do NOT switch languages even if the source material, JSON keys, or "
        "examples in this prompt are in a different language. Keep proper "
        "nouns (people, products, formula symbols) in their original form."
    )
    return body + quality


def append_language_directive(
    system_prompt: str | None,
    language: str | None,
    *,
    allow_user_override: bool = False,
) -> str:
    """Append the language directive to an existing system prompt."""
    base = (system_prompt or "").rstrip()
    directive = language_directive(language, allow_user_override=allow_user_override).strip()
    if not base:
        return directive
    return f"{base}\n\n{directive}"


__all__ = [
    "append_language_directive",
    "is_chinese",
    "language_directive",
    "language_label",
    "normalize_language",
]
