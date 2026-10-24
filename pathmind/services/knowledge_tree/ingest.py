"""Raw file → plain text → outline of (level, title, start, end) sections.

Deterministic and free (no LLM): headings are recognised from Markdown ``#``,
Word heading styles, numbered lines ("2.1 Limits") and "Chapter/Chương N".
Documents without headings fall back to paragraph chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
import zipfile

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_NODES = 500
_CHUNK_CHARS = 1500

_NUMBERED = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?\s+(\S.{0,118})$")
_MD = re.compile(r"^(#{1,4})\s+(\S.*)$")
_CHAPTER = re.compile(r"^(chapter|chương|bài|unit|part)\s+\d+\b.{0,100}$", re.I)


class IngestError(ValueError):
    """The file cannot be turned into text (unsupported type, corrupt, too big)."""


@dataclass(frozen=True)
class Section:
    level: int  # 1 = topic, 2.. = nodes below it
    title: str
    start: int
    end: int


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise IngestError("File is larger than 20 MB.")
    name = filename.lower()
    if name.endswith((".txt", ".md", ".markdown")):
        text = data.decode("utf-8", errors="replace")
    elif name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith(".pdf"):
        text = _pdf_text(data)
    else:
        raise IngestError("Supported files: PDF, Word (.docx), Markdown and plain text.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if not text.strip():
        raise IngestError("No text could be read from this file.")
    return text[:MAX_TEXT_CHARS]


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as exc:
        raise IngestError("Not a valid .docx file.") from exc
    lines: list[str] = []
    for para in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        text = "".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", para, flags=re.S))
        text = (
            text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'")
        ).strip()
        if not text:
            lines.append("")
            continue
        style = re.search(r'<w:pStyle w:val="(?:Heading|Title)(\d?)"', para)
        if style:
            level = int(style.group(1) or 1)
            text = "#" * max(1, min(level, 4)) + " " + text
        lines.append(text)
    return "\n".join(lines)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise IngestError("PDF import needs the pypdf package.") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise IngestError("Could not read this PDF.") from exc


def _heading_level(line: str) -> tuple[int, str] | None:
    s = line.strip()
    if not s or len(s) > 120:
        return None
    m = _MD.match(s)
    if m:
        return len(m.group(1)), m.group(2).strip()
    m = _NUMBERED.match(s)
    if m and not s.endswith((".", ",", ";")):
        # The number only marks the level; the title is what nodes and relations refer to.
        return min(m.group(1).count(".") + 1, 4), m.group(2).strip()
    if _CHAPTER.match(s):
        return 1, s
    return None


def build_outline(text: str) -> list[Section]:
    """Sections in document order; ``end`` runs to the next heading."""
    marks: list[tuple[int, int, str, int]] = []  # (level, start, title, line_end)
    pos = 0
    explicit = False  # a real Markdown / Word heading, not a numbered-looking line
    for line in text.split("\n"):
        found = _heading_level(line)
        if found:
            marks.append((found[0], pos, found[1], pos + len(line)))
            explicit = explicit or _MD.match(line.strip()) is not None
        pos += len(line) + 1
    if len(marks) >= 2 or (marks and explicit):
        sections = []
        for i, (level, start, title, line_end) in enumerate(marks):
            end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
            sections.append(Section(level, title[:200], line_end, end))
        return _normalise(sections)[:MAX_NODES]
    return _chunks(text)


def _normalise(sections: list[Section]) -> list[Section]:
    """A level may only be one deeper than its predecessor (no orphans)."""
    out: list[Section] = []
    prev = 0
    for s in sections:
        level = min(s.level, prev + 1) if prev else 1
        out.append(Section(level, s.title, s.start, s.end))
        prev = level
    return out


def _chunks(text: str) -> list[Section]:
    out = [Section(1, "Imported content", 0, len(text))]
    pos = 0
    paras = re.split(r"(?<=\n\n)", text)
    start, buf = 0, 0
    for p in paras:
        buf += len(p)
        pos += len(p)
        if buf >= _CHUNK_CHARS:
            out.append(_chunk_section(text, start, pos))
            start, buf = pos, 0
    if buf and text[start:pos].strip():
        out.append(_chunk_section(text, start, pos))
    return out[:MAX_NODES]


def _chunk_section(text: str, start: int, end: int) -> Section:
    first = text[start:end].strip().split("\n", 1)[0]
    return Section(2, (first[:60] or "Section").strip(), start, end)


def summarise(text: str, section: Section, limit: int = 400) -> str:
    body = re.sub(r"\s+", " ", text[section.start : section.end]).strip()
    return body[:limit]
