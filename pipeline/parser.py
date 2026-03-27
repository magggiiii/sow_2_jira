# pipeline/parser.py

import json
from pathlib import Path
import opendataloader_pdf
from rich import print as rprint
from pipeline.observability import logger, tracer, trace_span

class PDFParser:
    """
    Wraps OpenDataLoader PDF.
    Converts SOW PDF → structured JSON with elements, then to a flat list
    of sections (text + tables) in reading order.
    """

    @trace_span("PDF_PARSE", agent="PDFParser")
    def parse(self, pdf_path: str) -> dict:
        """
        Returns raw OpenDataLoader JSON output.
        """
        logger.info(f"Starting PDF parsing: {pdf_path}")
        rprint(f"[cyan]Parsing PDF:[/cyan] {pdf_path}")
        output_dir = Path("data/parser_output")
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            opendataloader_pdf.convert(
                input_path=pdf_path,
                output_dir=str(output_dir),
                format="json",
            )
        except Exception as e:
            logger.error(f"PDF parsing failed: {e}")
            raise

        # OpenDataLoader writes <filename>.json in output_dir
        pdf_stem = Path(pdf_path).stem
        json_path = output_dir / f"{pdf_stem}.json"

        with open(json_path, "r", encoding="utf-8") as f:
            parsed = json.load(f)

        # Recursively flatten elements from the 'kids' hierarchy and normalize keys
        elements = []
        def extract_and_normalize(item):
            # Normalize keys: replace spaces with underscores for internal consistency
            normalized = {}
            for k, v in item.items():
                normalized[k.replace(" ", "_")] = v
            
            # Check if this item is an element we want to track
            etype = normalized.get("type")
            if etype in ("heading", "paragraph", "table", "list", "caption"):
                elements.append(normalized)
            
            # Recurse into kids
            for kid in item.get("kids", []):
                extract_and_normalize(kid)
        
        extract_and_normalize(parsed)
        parsed["elements"] = elements

        count = len(elements)
        logger.success(f"Parsed and normalized {count} elements from PDF")
        rprint(f"[green]Parsed {count} elements[/green]")
        return parsed

    @trace_span("CONVERT_TO_MARKDOWN", agent="PDFParser")
    def to_markdown_sections(self, parsed: dict, skip_sections: list[str]) -> list[dict]:
        """
        Converts normalized elements into a list of section dicts.
        """
        elements = parsed.get("elements", [])
        logger.info(f"Converting {len(elements)} elements to markdown sections")
        sections = []
        current_section = None

        for elem in elements:
            elem_type = elem.get("type", "")
            content = elem.get("content", "")
            page = elem.get("page_number", 1)
            elem_id = elem.get("id")

            # Detect section boundary (Headings up to level 3)
            if elem_type == "heading" and elem.get("heading_level", 99) <= 3:
                heading_text = content.strip()
                logger.debug(f"Detected heading (L{elem.get('heading_level')}): {heading_text}")
                
                # Save previous section
                if current_section and current_section["text"].strip():
                    if not _should_skip(current_section["heading"], skip_sections):
                        sections.append(current_section)
                    else:
                        logger.debug(f"Skipping section: {current_section['heading']}")

                # Start new section
                current_section = {
                    "heading": heading_text,
                    "page_start": page,
                    "page_end": page,
                    "text": "",
                    "element_ids": [],
                }
            else:
                if current_section is None:
                    # Content before first heading — create a default section
                    current_section = {
                        "heading": "Preamble",
                        "page_start": page,
                        "page_end": page,
                        "text": "",
                        "element_ids": [],
                    }

                # Append content
                if elem_type == "table":
                    current_section["text"] += _table_to_markdown(elem) + "\n\n"
                elif elem_type in ("paragraph", "list", "caption"):
                    current_section["text"] += content + "\n\n"

                current_section["page_end"] = page
                if elem_id is not None:
                    current_section["element_ids"].append(elem_id)

        # Save last section
        if current_section and current_section["text"].strip():
            if not _should_skip(current_section["heading"], skip_sections):
                sections.append(current_section)

        logger.success(f"Produced {len(sections)} sections after filtering")
        rprint(f"[green]Produced {len(sections)} sections after filtering[/green]")
        return sections


def _should_skip(heading: str, skip_list: list[str]) -> bool:
    heading_lower = heading.lower()
    return any(skip.lower() in heading_lower for skip in skip_list)


def _table_to_markdown(elem: dict) -> str:
    """Convert OpenDataLoader table element to markdown pipe table."""
    # Note: elem here has already been normalized by extract_and_normalize
    rows = elem.get("rows", [])
    if not rows:
        return elem.get("content", "")

    lines = []
    for i, row_obj in enumerate(rows):
        # Handle both normalized and raw key access just in case
        cells_list = row_obj.get("cells", [])
        cells = []
        for cell_obj in cells_list:
            # A cell might have "content" or "kids" containing paragraphs
            content = cell_obj.get("content", "")
            if not content and "kids" in cell_obj:
                content = " ".join([k.get("content", "") for k in cell_obj["kids"] if isinstance(k, dict)])
            cells.append(str(content).replace("|", "\\|").strip())
        
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("|" + "|".join(["---"] * len(cells)) + "|")

    return "\n".join(lines)
