"""Tests for extract_document_text."""

from __future__ import annotations

import asyncio
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ling_agent import (
    HarnessConfig,
    Workspace,
    approval_reason,
    create_document_tools,
    derive_effect,
)
from ling_ai.types import ToolCall
from ling_core import ToolContext

CONTEXT = ToolContext(step=1)

_DOCX_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Title</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
    <w:p>
      <w:r><w:t>Line</w:t><w:br/><w:t>break</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""


def _slide_xml(text: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p>
  </p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>
"""


def _write_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _DOCX_XML)


def _write_pptx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide10.xml", _slide_xml("Ten"))
        archive.writestr("ppt/slides/slide2.xml", _slide_xml("Two"))
        archive.writestr("ppt/slides/slide1.xml", _slide_xml("One"))


def _write_pdf(path: Path, text: str = "Hello PDF") -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 24 Tf 50 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(str(path))


def _call(path: str, **extra: object) -> ToolCall:
    return ToolCall(
        id="call-1",
        name="extract_document_text",
        arguments={"path": path, **extra},
    )


def _extract(
    root: Path,
    path: str,
    *,
    config: HarnessConfig | None = None,
    **extra: object,
) -> dict[str, object]:
    tool = create_document_tools(Workspace(root), config)[0]
    output = asyncio.run(tool.handler(_call(path, **extra), CONTEXT))
    return json.loads(output)


def test_extract_docx_text_headings_and_tables() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_docx(root / "sample.docx")

        payload = _extract(root, "sample.docx")
        assert payload["format"] == "docx"
        content = payload["content"]
        assert "# Title" in content
        assert "First paragraph" in content
        assert "Line\nbreak" in content
        assert "| A | B |" in content
        assert payload["truncated"] is False


def test_extract_pptx_orders_slides_numerically() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_pptx(root / "sample.pptx")

        payload = _extract(root, "sample.pptx")
        content = str(payload["content"])
        assert content.index("One") < content.index("Two")
        assert content.index("Two") < content.index("Ten")
        assert "--- Slide 3 ---" in content
        assert payload["metadata"] == {
            "pages": 3,
            "page_from": 1,
            "page_to": 3,
        }


def test_extract_pptx_page_range() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_pptx(root / "sample.pptx")

        payload = _extract(root, "sample.pptx", page_from=2, page_to=2)
        content = str(payload["content"])
        assert "Two" in content
        assert "One" not in content
        assert "Ten" not in content


def test_extract_pdf_text_and_page_range() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_pdf(root / "sample.pdf")

        payload = _extract(root, "sample.pdf")
        assert payload["format"] == "pdf"
        assert "--- Page 1 ---" in str(payload["content"])
        assert "Hello PDF" in str(payload["content"])


def test_blank_pdf_warns_about_scanned_documents() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(str(root / "blank.pdf"))

        payload = _extract(root, "blank.pdf")
        warnings = payload["warnings"]
        assert any("scanned" in warning for warning in warnings)


def test_absolute_path_outside_workspace_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as workspace_temp:
        with tempfile.TemporaryDirectory() as document_temp:
            root = Path(workspace_temp)
            document = Path(document_temp) / "outside.docx"
            _write_docx(document)

            payload = _extract(root, str(document))
            assert "First paragraph" in str(payload["content"])


def test_absolute_document_effect_is_read_without_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workspace = Workspace(root)
        path = str(root / "sample.docx")

        effect = derive_effect(_call(path), workspace)
        assert effect.kind == "filesystem_read"
        assert effect.scope == "absolute"
        assert approval_reason(effect) is None


def test_document_content_is_truncated() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_docx(root / "sample.docx")
        config = HarnessConfig(max_document_text_chars=20)

        payload = _extract(root, "sample.docx", config=config)
        assert payload["truncated"] is True
        assert str(payload["content"]).endswith("[truncated]")


def test_legacy_formats_and_images_have_actionable_errors() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "old.doc").write_bytes(b"not a doc")
        (root / "image.png").write_bytes(b"not an image")

        with pytest.raises(ValueError, match="soffice"):
            _extract(root, "old.doc")
        with pytest.raises(ValueError, match="OCR"):
            _extract(root, "image.png")


def test_missing_and_oversized_documents_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with pytest.raises(Exception, match="does not exist"):
            _extract(root, "missing.docx")

        _write_docx(root / "sample.docx")
        config = HarnessConfig(max_document_bytes=10)
        with pytest.raises(Exception, match="exceeds"):
            _extract(root, "sample.docx", config=config)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
