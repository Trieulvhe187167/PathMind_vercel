"""Sample documents that show R&D how to format a file for the main knowledge graph.

The same content comes in every format the importer reads, each written the
way that format marks headings: Markdown ``#``, Word Heading 1-4 styles, and
numbered lines (``1.``, ``1.1``) in plain text and PDF.
"""

from __future__ import annotations

import io
import re

from .service import TreeError

SAMPLE_TITLE = "Knowledge graph sample"

SAMPLE_MARKDOWN = """# Mathematics

@label Category

Every heading becomes a node of the knowledge graph, and the text under it becomes that node's content, which the tutor reads when it answers. A heading inside another one is PART_OF it. Lines that start with @ add graph data: @id gives the node a readable id that is unique in the domain, @name adds its name in another language, @description a short summary, @source and @confidence say where the facts come from, @label sets the node type, @prop adds a property and @rel adds a relation (to a heading title or to #id).

## Algebra

@label Type

Categories and types are ordinary nodes; other nodes point to them with TYPE_OF.

## Geometry

@label Type

# Concepts and lessons

@label Topic

The concepts, lessons and exercises themselves. A node can belong to several types, like a fruit that is also a fresh food.

## Linear equations

@id linear-equations
@name vi = Phương trình bậc nhất
@description An equation where the unknown appears only to the first power.
@source Grade 8 textbook, chapter 3
@confidence 0.95
@label Concept
@prop difficulty = 1
@rel TYPE_OF -> Algebra
@rel PREREQUISITE_OF -> Coordinate geometry

An equation of the form ax + b = 0. Write the explanation the tutor should rely on: definitions, worked examples, common mistakes.

## Coordinate geometry

@label Concept
@prop difficulty = 2
@rel TYPE_OF -> Algebra
@rel TYPE_OF -> Geometry

Points, lines and shapes described with numbers. This concept is a type of both Algebra and Geometry.

## Solving ax + b = 0

@label Lesson
@rel EXPLAINS -> Linear equations {minutes: 20}

A relation can carry properties in braces, like the quantity on an order line.

## Lines through two points

@label Exercise
@rel USES -> Coordinate geometry {questions: 5}

In Word, use the styles Heading 1 to Heading 4. In plain text and PDF, number the headings instead (1., 1.1, 1.1.1); the number is dropped from the title. Relations point to other headings by their title, so keep titles unique within a domain. Update a domain by editing this document in Co-Writer and choosing "Update main graph": headings that keep their place keep their node, and every update can be reverted from the edit history.
"""

FORMATS = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "txt": ("text/plain; charset=utf-8", "txt"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "pdf": ("application/pdf", "pdf"),
}

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


def _numbered(markdown: str) -> list[tuple[int, str]]:
    """(level, line) pairs where headings are numbered: 1., 1.1, 1.1.1 ..."""
    counters = [0, 0, 0, 0]
    out: list[tuple[int, str]] = []
    for line in markdown.split("\n"):
        m = _HEADING.match(line)
        if not m:
            out.append((0, line))
            continue
        level = len(m.group(1))
        counters[level - 1] += 1
        for i in range(level, 4):
            counters[i] = 0
        number = ".".join(str(c) for c in counters[:level])
        out.append((level, f"{number}{'.' if level == 1 else ''} {m.group(2)}"))
    return out


def sample_text() -> str:
    return "\n".join(line for _, line in _numbered(SAMPLE_MARKDOWN))


def _pdf(markdown: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    heading = {1: styles["Heading1"], 2: styles["Heading2"], 3: styles["Heading3"], 4: styles["Heading4"]}
    story = []
    for level, line in _numbered(markdown):
        if level:
            story.append(Paragraph(line, heading[level]))
        elif line.strip():
            story.append(Paragraph(line, styles["BodyText"]))
            story.append(Spacer(1, 4))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, title=SAMPLE_TITLE).build(story)
    return buf.getvalue()


def sample_file(fmt: str) -> tuple[bytes, str, str]:
    """(bytes, content type, file name) of the sample in one format."""
    if fmt not in FORMATS:
        raise TreeError(400, "Choose md, txt, docx or pdf.")
    ctype, ext = FORMATS[fmt]
    if fmt == "md":
        data = SAMPLE_MARKDOWN.encode("utf-8")
    elif fmt == "txt":
        data = sample_text().encode("utf-8")
    elif fmt == "docx":
        from pathmind.co_writer.docx_converter import markdown_to_docx

        data = markdown_to_docx(SAMPLE_MARKDOWN, SAMPLE_TITLE)
    else:
        data = _pdf(SAMPLE_MARKDOWN)
    return data, ctype, f"knowledge-graph-sample.{ext}"
