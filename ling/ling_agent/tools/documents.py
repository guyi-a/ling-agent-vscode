"""Read text from PDF, DOCX, and PPTX documents."""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext

from ..config import HarnessConfig
from ..workspace import Workspace, WorkspaceFileError

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SLIDE_PATTERN = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_HEADING_PATTERN = re.compile(r"^Heading\s*([1-6])$", re.IGNORECASE)
_SUPPORTED = {".pdf", ".docx", ".pptx"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
_LEGACY = {
    ".doc": "docx",
    ".ppt": "pptx",
}


@dataclass(slots=True)
class DocumentExtraction:
    path: str
    format: str
    content: str
    truncated: bool = False
    metadata: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "path": self.path,
                "format": self.format,
                "content": self.content,
                "truncated": self.truncated,
                "metadata": self.metadata,
                "warnings": self.warnings,
            },
            ensure_ascii=False,
        )


def create_document_tools(
    workspace: Workspace,
    config: HarnessConfig | None = None,
) -> list[AgentTool]:
    resolved_config = config or workspace.config

    async def extract_document_text(
        call: ToolCall,
        context: ToolContext,
    ) -> str:
        return await asyncio.to_thread(
            _extract_document,
            workspace,
            resolved_config,
            call,
        )

    return [
        AgentTool(
            definition=Tool(
                name="extract_document_text",
                description=(
                    "Extract text from a PDF, DOCX, or PPTX file. Accepts "
                    "workspace-relative and absolute paths. Images, scanned "
                    "PDFs, .doc, .ppt, and spreadsheets are not supported."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "page_from": {"type": "integer", "minimum": 1},
                        "page_to": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            handler=extract_document_text,
        )
    ]


def _extract_document(
    workspace: Workspace,
    config: HarnessConfig,
    call: ToolCall,
) -> str:
    raw_path = call.arguments.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise TypeError("path must be a non-empty string")

    page_from = _optional_page(call, "page_from")
    page_to = _optional_page(call, "page_to")
    if page_from is not None and page_to is not None and page_to < page_from:
        raise ValueError("page_to must be greater than or equal to page_from")

    resolved = _resolve_path(workspace, raw_path)
    if not resolved.exists():
        raise WorkspaceFileError(f"File does not exist: {raw_path}")
    if not resolved.is_file():
        raise WorkspaceFileError(f"Path is not a file: {raw_path}")
    if resolved.stat().st_size > config.max_document_bytes:
        raise WorkspaceFileError(
            f"Document exceeds {config.max_document_bytes} bytes: {raw_path}"
        )

    suffix = resolved.suffix.lower()
    if suffix not in _SUPPORTED:
        raise ValueError(_unsupported_message(suffix, raw_path))

    try:
        extraction = _extract_by_suffix(
            resolved,
            suffix,
            page_from,
            page_to,
            config,
        )
    except zipfile.BadZipFile as exc:
        raise ValueError(
            f"Invalid Office document (not a valid zip): {raw_path}"
        ) from exc
    except ET.ParseError as exc:
        raise ValueError(
            f"Invalid Office document XML: {raw_path} ({exc})"
        ) from exc

    return extraction.to_json()


def _extract_by_suffix(
    resolved: Path,
    suffix: str,
    page_from: int | None,
    page_to: int | None,
    config: HarnessConfig,
) -> DocumentExtraction:
    if suffix == ".pdf":
        return _extract_pdf(
            resolved,
            page_from or 1,
            page_to,
            max_chars=config.max_document_text_chars,
        )
    if suffix == ".docx":
        extraction = _extract_docx(
            resolved,
            max_chars=config.max_document_text_chars,
            max_xml_bytes=config.max_document_xml_bytes,
        )
        if page_from is not None or page_to is not None:
            extraction.warnings.append(
                "page_from/page_to are ignored for DOCX files"
            )
        return extraction
    return _extract_pptx(
        resolved,
        page_from or 1,
        page_to,
        max_chars=config.max_document_text_chars,
        max_xml_bytes=config.max_document_xml_bytes,
    )


def _resolve_path(workspace: Workspace, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return workspace.resolve(raw_path)


def _optional_page(call: ToolCall, name: str) -> int | None:
    value = call.arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _unsupported_message(suffix: str, path: str) -> str:
    if suffix in _LEGACY:
        target = _LEGACY[suffix]
        return (
            f"Legacy format '{suffix}' is not supported: {path}. "
            f"Convert it first with: "
            f"soffice --headless --convert-to {target} <file>"
        )
    if suffix in _IMAGE_SUFFIXES:
        return (
            "Image OCR is not supported. Attach the image to the message "
            "for vision input, or use an OCR command if one is installed."
        )
    if suffix in (".xlsx", ".ipynb"):
        return f"Unsupported document format '{suffix}': {path}"
    supported = ", ".join(sorted(_SUPPORTED))
    return f"Unsupported document format '{suffix}': {path}. Supported: {supported}"


def _extract_pdf(
    path: Path,
    page_from: int,
    page_to: int | None,
    *,
    max_chars: int,
) -> DocumentExtraction:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF extraction requires pypdf. Install it with: "
            "pip install -r requirements.txt"
        ) from exc

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise ValueError(
                    f"PDF is encrypted and cannot be opened: {path}"
                )
        except Exception as exc:
            raise ValueError(
                f"PDF is encrypted and cannot be opened: {path}"
            ) from exc

    total = len(reader.pages)
    if total == 0:
        raise ValueError(f"PDF has no pages: {path}")
    if page_from > total:
        raise ValueError(
            f"page_from ({page_from}) exceeds the page count ({total})"
        )
    last_page = min(page_to or total, total)

    warnings: list[str] = []
    sections: list[str] = []
    extracted_any = False
    for page_number in range(page_from, last_page + 1):
        page = reader.pages[page_number - 1]
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            warnings.append(f"page {page_number}: {exc}")
            text = ""
        if text.strip():
            extracted_any = True
        sections.append(f"--- Page {page_number} ---\n{text.rstrip()}")

    if not extracted_any:
        warnings.append(
            "No text layer found; this PDF is likely scanned and OCR is "
            "not supported."
        )

    extraction = DocumentExtraction(
        path=str(path),
        format="pdf",
        content="\n\n".join(sections),
        metadata={
            "pages": total,
            "page_from": page_from,
            "page_to": last_page,
        },
        warnings=warnings,
    )
    return _truncate(extraction, max_chars)


def _extract_docx(
    path: Path,
    *,
    max_chars: int,
    max_xml_bytes: int,
) -> DocumentExtraction:
    document_xml = _read_zip_member(
        path,
        "word/document.xml",
        max_xml_bytes=max_xml_bytes,
    )
    root = ET.fromstring(document_xml)
    body = root.find(f"{{{_W_NS}}}body")
    if body is None:
        raise ValueError(f"DOCX has no document body: {path}")

    blocks: list[str] = []
    for child in body:
        tag = _local_name(child.tag)
        if tag == "p":
            blocks.append(_docx_paragraph(child))
        elif tag == "tbl":
            table = _docx_table(child)
            if table:
                blocks.append(table)

    content = "\n\n".join(block for block in blocks if block)
    warnings: list[str] = []
    if not content.strip():
        warnings.append("No readable text found in the DOCX document")
    extraction = DocumentExtraction(
        path=str(path),
        format="docx",
        content=content,
        metadata={"pages": 1},
        warnings=warnings,
    )
    return _truncate(extraction, max_chars)


def _extract_pptx(
    path: Path,
    page_from: int,
    page_to: int | None,
    *,
    max_chars: int,
    max_xml_bytes: int,
) -> DocumentExtraction:
    with zipfile.ZipFile(path) as archive:
        slides: list[tuple[int, str]] = []
        for name in archive.namelist():
            match = _SLIDE_PATTERN.match(name)
            if match is not None:
                slides.append((int(match.group(1)), name))
        slides.sort(key=lambda item: item[0])

        total = len(slides)
        if total == 0:
            raise ValueError(f"PPTX has no slides: {path}")
        if page_from > total:
            raise ValueError(
                f"page_from ({page_from}) exceeds the slide count ({total})"
            )
        last_page = min(page_to or total, total)
        selected = slides[page_from - 1 : last_page]

        sections: list[str] = []
        warnings: list[str] = []
        extracted_any = False
        for index, (_, name) in enumerate(selected, start=page_from):
            info = archive.getinfo(name)
            if info.file_size > max_xml_bytes:
                warnings.append(f"slide {index}: XML is too large")
                sections.append(f"--- Slide {index} ---")
                continue
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError as exc:
                warnings.append(f"slide {index}: invalid XML ({exc})")
                sections.append(f"--- Slide {index} ---")
                continue
            texts = _pptx_paragraphs(root)
            if texts:
                extracted_any = True
            sections.append(
                f"--- Slide {index} ---\n" + "\n".join(texts)
            )

    if not extracted_any:
        warnings.append("No readable text found in the selected slides")

    extraction = DocumentExtraction(
        path=str(path),
        format="pptx",
        content="\n\n".join(sections),
        metadata={
            "pages": total,
            "page_from": page_from,
            "page_to": last_page,
        },
        warnings=warnings,
    )
    return _truncate(extraction, max_chars)


def _read_zip_member(
    path: Path,
    name: str,
    *,
    max_xml_bytes: int,
) -> bytes:
    with zipfile.ZipFile(path) as archive:
        try:
            info = archive.getinfo(name)
        except KeyError as exc:
            raise ValueError(f"Missing {name} in {path}") from exc
        if info.file_size > max_xml_bytes:
            raise ValueError(
                f"{name} exceeds {max_xml_bytes} bytes and is too large "
                "to extract"
            )
        return archive.read(name)


def _docx_paragraph(paragraph: ET.Element) -> str:
    text = _docx_text(paragraph)
    heading = _docx_heading_level(paragraph)
    if heading and text.strip():
        return f"{'#' * heading} {text.strip()}"
    return text


def _docx_heading_level(paragraph: ET.Element) -> int | None:
    style = paragraph.find(f"{{{_W_NS}}}pPr/{{{_W_NS}}}pStyle")
    if style is None:
        return None
    value = style.get(f"{{{_W_NS}}}val", "")
    match = _HEADING_PATTERN.match(value)
    return int(match.group(1)) if match is not None else None


def _docx_text(element: ET.Element) -> str:
    parts: list[str] = []
    for node in element.iter():
        tag = _local_name(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag == "br":
            parts.append("\n")
    return "".join(parts)


def _docx_table(table: ET.Element) -> str:
    rows: list[str] = []
    for row in table.findall(f"{{{_W_NS}}}tr"):
        cells = []
        for cell in row.findall(f"{{{_W_NS}}}tc"):
            text = " ".join(
                _docx_text(paragraph).strip()
                for paragraph in cell.findall(f"{{{_W_NS}}}p")
            ).strip()
            cells.append(text)
        if cells:
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _pptx_paragraphs(root: ET.Element) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{{{_A_NS}}}p"):
        text = "".join(
            element.text
            for element in paragraph.iter(f"{{{_A_NS}}}t")
            if element.text
        ).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _truncate(
    extraction: DocumentExtraction,
    max_chars: int,
) -> DocumentExtraction:
    if len(extraction.content) <= max_chars:
        return extraction
    extraction.content = (
        extraction.content[:max_chars] + "\n\n[truncated]"
    )
    extraction.truncated = True
    extraction.warnings.append(
        f"Content truncated to {max_chars} characters"
    )
    return extraction


__all__ = [
    "DocumentExtraction",
    "create_document_tools",
]
