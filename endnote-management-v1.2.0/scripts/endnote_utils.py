#!/usr/bin/env python3
"""
EndNote Management Utilities (endnote_utils.py)
Shared library for all EndNote DOCX operations.
Provides: OOXML field builders, reference parsers, validators, DOCX I/O.

Version: 1.2.0
Part of: endnote-management skill scripts/
"""

import os
import re
import json
import base64
import shutil
import zipfile
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Set, Any

# ═══════════════════════════════════════════════════════════
# OOXML Constants
# ═══════════════════════════════════════════════════════════
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "{http://www.w3.org/XML/1998/namespace}"

# Register namespace so ET.tostring() outputs clean "w:" prefixes
ET.register_namespace("w", W)

# ═══════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════
class Logger:
    """Simple timestamped logger that collects lines for file output."""

    def __init__(self, log_path: Optional[str] = None):
        self.lines: List[str] = []
        self.log_path = log_path

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        self.lines.append(line)

    def flush(self):
        if self.log_path and self.lines:
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.lines))

    def set_path(self, path: str):
        self.log_path = path


# ═══════════════════════════════════════════════════════════
# DOCX Package I/O
# ═══════════════════════════════════════════════════════════
class DocxPackage:
    """
    Unpacks a DOCX, parses word/document.xml and word/styles.xml,
    provides repack capability.
    """

    def __init__(self, docx_path: str, logger: Logger):
        self.docx_path = docx_path
        self.log = logger
        self.tmpdir: Optional[str] = None
        self.document_root: Optional[ET.Element] = None
        self.document_tree: Optional[ET.ElementTree] = None
        self.styles_tree: Optional[ET.ElementTree] = None
        self.full_text: str = ""

    def __enter__(self):
        self.unpack()
        return self

    def __exit__(self, *args):
        self.cleanup()

    def unpack(self):
        """Unzip DOCX to tempdir and parse XMLs."""
        self.tmpdir = tempfile.mkdtemp()
        with zipfile.ZipFile(self.docx_path, 'r') as z:
            z.extractall(self.tmpdir)

        # Parse document.xml
        doc_xml = os.path.join(self.tmpdir, "word", "document.xml")
        self.document_tree = ET.parse(doc_xml)
        self.document_root = self.document_tree.getroot()

        # Parse styles.xml
        styles_xml = os.path.join(self.tmpdir, "word", "styles.xml")
        if os.path.exists(styles_xml):
            self.styles_tree = ET.parse(styles_xml)

        # Extract full plain text
        texts = []
        for wt in self.document_root.iter(f"{{{W}}}t"):
            if wt.text:
                texts.append(wt.text)
        self.full_text = "".join(texts)

        self.log.log(f"Unpacked DOCX: {len(self.full_text)} chars of text")

    def cleanup(self):
        if self.tmpdir and os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def repack(self, output_path: str):
        """Write modified XMLs back and repack as DOCX."""
        doc_xml_path = os.path.join(self.tmpdir, "word", "document.xml")
        xml_str = ET.tostring(self.document_root, encoding='utf-8', xml_declaration=True)
        with open(doc_xml_path, "wb") as f:
            f.write(xml_str)

        if self.styles_tree is not None:
            styles_path = os.path.join(self.tmpdir, "word", "styles.xml")
            styles_str = ET.tostring(self.styles_tree.getroot(), encoding='utf-8', xml_declaration=True)
            with open(styles_path, "wb") as f:
                f.write(styles_str)

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root_dir, dirs, files in os.walk(self.tmpdir):
                for file in files:
                    fp = os.path.join(root_dir, file)
                    arcname = os.path.relpath(fp, self.tmpdir)
                    zout.write(fp, arcname)

        self.log.log(f"Packed output DOCX: {output_path}")


# ═══════════════════════════════════════════════════════════
# Reference Parser
# ═══════════════════════════════════════════════════════════
class ReferenceParser:
    """
    Extract and parse bibliography references from plain text.
    Supports Chinese (参考文献) and English (References/Bibliography) headers.
    """

    BIB_HEADERS = ["参考文献", "References", "Bibliography"]

    def __init__(self, logger: Logger):
        self.log = logger

    def locate_bibliography(self, full_text: str) -> int:
        """Return start index of bibliography section, or -1."""
        for h in self.BIB_HEADERS:
            idx = full_text.find(h)
            if idx >= 0:
                return idx + len(h)
        return -1

    def extract_raw(self, full_text: str) -> Dict[str, str]:
        """
        Extract reference entries as {recnum: full_text}.
        Pattern: [N] content_text until next [N+1] or end.
        """
        bib_start = self.locate_bibliography(full_text)
        if bib_start < 0:
            self.log.log("ERROR: Cannot find bibliography section")
            return {}

        bib_text = full_text[bib_start:]
        pattern = r'\[(\d+)\]\s*(.*?)(?=\[\d+\]|$)'
        refs = {}
        for m in re.finditer(pattern, bib_text, re.DOTALL):
            num = m.group(1)
            text = m.group(2).strip()
            if text:
                refs[num] = text

        self.log.log(f"Extracted {len(refs)} reference entries")
        return refs

    @staticmethod
    def parse_metadata(ref_text: str) -> Dict[str, str]:
        """
        Extract Author, Year, Title, Journal, Volume, Pages from a reference string.
        Handles the standard academic format: Author. Title. Journal, Year; Vol(Issue): Pages.
        Uses ". " (dot-space) as the primary segment delimiter, anchored on the Year.
        """
        meta: Dict[str, str] = {
            "full_text": ref_text,
            "author": "",
            "year": "",
            "title": "",
            "journal": "",
            "volume": "",
            "pages": ""
        }

        # Year: find the publication year in pattern ", Year;" — this is the canonical
        # publication date, avoiding years that appear inside titles (e.g., "1990-2017").
        ym = re.search(r',\s*((?:19|20)\d{2});', ref_text)
        if not ym:
            # fallback: last 4-digit year in the text
            ym = re.search(r'((?:19|20)\d{2})(?:[;.\s]|$)', ref_text)
        if ym:
            meta["year"] = ym.group(1)

        # Split into segments by ". " (dot + space) — safe because journal
        # abbreviations in these datasets do not contain internal periods
        segments = ref_text.split('. ')

        # Author: always the first segment  (handles "et al." correctly:
        # the trailing period is consumed by the split)
        if segments:
            meta["author"] = segments[0].strip()

        # Journal: find the segment that contains ", Year;" (semicolon after year
        # is the markup for publication date). Prefer "; " over bare ", Year"
        # to avoid matching years inside titles (e.g., "…burden of disease, 1990-2017…").
        journal_idx = -1
        for i, seg in enumerate(segments):
            if i == 0:
                continue
            if re.search(r',\s*(?:19|20)\d{2};', seg):
                jm = re.match(r'^([A-Z].+?),\s*(?:19|20)\d{2}', seg)
                if jm:
                    meta["journal"] = jm.group(1).strip()
                journal_idx = i
                break
        # Fallback: ", Year" without semicolon
        if journal_idx < 0:
            for i, seg in enumerate(segments):
                if i == 0:
                    continue
                if re.search(r',\s*(?:19|20)\d{2}', seg):
                    jm = re.match(r'^([A-Z].+?),\s*(?:19|20)\d{2}', seg)
                    if jm:
                        meta["journal"] = jm.group(1).strip()
                    journal_idx = i
                    break

        # Title: all segments between Author (index 0) and Journal
        if journal_idx > 1:
            title_parts = segments[1:journal_idx]
            meta["title"] = '. '.join(title_parts).strip()
        elif journal_idx == 1 and len(segments) > 1:
            meta["title"] = segments[1].strip()

        # Volume/Pages: Year; Vol(Issue): Pages
        vm = re.search(r'(?:19|20)\d{2};\s*(\d+)(?:\(([^)]+)\))?[:\s]+(.+?)(?:\.)?$', ref_text)
        if vm:
            meta["volume"] = vm.group(1)
            meta["pages"] = vm.group(3).strip() if vm.group(3) else ""

        return meta

    def build_mapping(self, full_text: str) -> Dict[str, Dict[str, str]]:
        """Build complete reference mapping with metadata."""
        raw = self.extract_raw(full_text)
        mapping = {}
        for num, text in raw.items():
            mapping[num] = self.parse_metadata(text)
        self.log.log(f"Built reference mapping: {len(mapping)} entries with metadata")
        return mapping


# ═══════════════════════════════════════════════════════════
# Citation Finder (DOM-based, no regex on XML)
# ═══════════════════════════════════════════════════════════
class CitationFinder:
    """
    Find bracket citation tokens [N], [N,N], [N-N] in the body XML
    using DOM traversal on text runs.
    """

    CITE_RE = re.compile(r'\[([0-9,\-\s]+)\]')

    def __init__(self, logger: Logger):
        self.log = logger

    def find_bibliography_paragraph(self, root: ET.Element) -> int:
        """Return index of first bibliography paragraph, or len(paras)."""
        body = root.find(f"{{{W}}}body")
        if body is None:
            return 0
        paras = body.findall(f"{{{W}}}p")
        for i, p in enumerate(paras):
            p_text = ""
            for wt in p.iter(f"{{{W}}}t"):
                if wt.text:
                    p_text += wt.text
            if "参考文献" in p_text or "References" in p_text:
                return i
        return len(paras)

    def find_citations(self, root: ET.Element) -> List[Dict[str, Any]]:
        """
        Scan body paragraphs for citation tokens. Returns list of:
        {para_idx, run_idx, full_match, match_start, match_end, ref_nums, run, wt_elem}
        """
        body = root.find(f"{{{W}}}body")
        if body is None:
            return []

        paras = body.findall(f"{{{W}}}p")
        bib_idx = self.find_bibliography_paragraph(root)
        body_paras = paras[:bib_idx]

        self.log.log(f"Body paragraphs: {len(body_paras)}, Bibliography at: {bib_idx}")

        citations = []
        for p_idx, para in enumerate(body_paras):
            runs = para.findall(f"{{{W}}}r")
            for r_idx, run in enumerate(runs):
                wt_elem = run.find(f"{{{W}}}t")
                if wt_elem is None or not wt_elem.text:
                    continue

                text = wt_elem.text
                for m in self.CITE_RE.finditer(text):
                    inner = m.group(1)
                    nums = self._expand_numbers(inner)
                    citations.append({
                        "para_idx": p_idx,
                        "run_idx": r_idx,
                        "full_match": m.group(0),
                        "match_start": m.start(),
                        "match_end": m.end(),
                        "ref_nums": sorted(nums, key=int),
                        "run": run,
                        "wt_elem": wt_elem,
                    })

        self.log.log(f"Found {len(citations)} citation tokens in body")
        return citations

    @staticmethod
    def _expand_numbers(inner: str) -> Set[str]:
        """Expand '1-3,5' into {'1','2','3','5'}."""
        nums: Set[str] = set()
        ranges = re.findall(r'(\d+)-(\d+)', inner)
        covered = set()
        for start, end in ranges:
            for i in range(int(start), int(end) + 1):
                s = str(i)
                nums.add(s)
                covered.add(s)
        singles = re.findall(r'(\d+)', inner)
        for n in singles:
            if n not in covered:
                nums.add(n)
        return nums


# ═══════════════════════════════════════════════════════════
# Citation Validator
# ═══════════════════════════════════════════════════════════
class CitationValidator:
    """Validate citation numbers against reference mapping."""

    def __init__(self, logger: Logger):
        self.log = logger

    def validate(self, citations: List[Dict], mapping: Dict[str, Any]) -> bool:
        all_nums: Set[str] = set()
        for c in citations:
            all_nums.update(c["ref_nums"])

        mapping_nums = set(mapping.keys())
        out_of_range = [n for n in all_nums if n not in mapping_nums]
        unreferenced = [n for n in mapping_nums if n not in all_nums]

        if out_of_range:
            self.log.log(f"ERROR: Citation numbers out of range: {out_of_range}")
        if unreferenced:
            self.log.log(f"WARNING: {len(unreferenced)} reference items not cited in body")

        self.log.log(f"Validation: {len(all_nums)} unique cites, {len(mapping_nums)} refs, "
                      f"out_of_range={len(out_of_range)}, unreferenced={len(unreferenced)}")
        return len(out_of_range) == 0


# ═══════════════════════════════════════════════════════════
# Field Builder — Golden Pattern B (EN.CITE + EN.CITE.DATA)
# ═══════════════════════════════════════════════════════════
class FieldBuilder:
    """
    Build EndNote Golden Pattern B field chains using DOM Element construction.
    Supports Synthesis Mode (minimal payload) and can extend to Donor-Clone.
    """

    def __init__(self, logger: Logger):
        self.log = logger

    @staticmethod
    def make_run(tag: str, text: Optional[str] = None) -> ET.Element:
        """Create a w:r element, optionally with a child {tag} containing text."""
        r = ET.Element(f"{{{W}}}r")
        if tag and text is not None:
            child = ET.SubElement(r, f"{{{W}}}{tag}")
            if tag in ("instrText", "fldData"):
                child.set(XML_NS + "space", "preserve")
            child.text = text
        return r

    @staticmethod
    def make_fldchar(fld_char_type: str, fld_data_b64: Optional[str] = None) -> ET.Element:
        """Create a w:r containing w:fldChar with given type."""
        r = ET.Element(f"{{{W}}}r")
        fc = ET.SubElement(r, f"{{{W}}}fldChar")
        fc.set(f"{{{W}}}fldCharType", fld_char_type)
        if fld_data_b64 is not None:
            fd = ET.SubElement(fc, f"{{{W}}}fldData")
            fd.set(XML_NS + "space", "preserve")
            fd.text = fld_data_b64
        return r

    def build_synthesis_payload(self, ref_nums: List[str],
                                 mapping: Dict[str, Dict[str, str]]) -> Tuple[str, str]:
        """
        Build base64 payload for EN.CITE.DATA (Synthesis Mode).
        Returns (payload_b64, display_text).
        """
        cites_xml_parts = []
        for num in ref_nums:
            meta = mapping.get(num, {})
            author = meta.get("author", "")
            year = meta.get("year", "")
            title = meta.get("title", "")
            display = f"[{num}]"

            cites_xml_parts.append(
                f'<Cite>'
                f'<Author>{author}</Author>'
                f'<Year>{year}</Year>'
                f'<RecNum>{num}</RecNum>'
                f'<DisplayText>{display}</DisplayText>'
                f'<record>'
                f'<titles><title>{title}</title></titles>'
                f'<contributors><authors><author>{author}</author></authors></contributors>'
                f'<dates><year>{year}</year></dates>'
                f'</record>'
                f'</Cite>'
            )

        endnote_xml = f'<EndNote>{"".join(cites_xml_parts)}</EndNote>'
        payload_b64 = base64.b64encode(endnote_xml.encode('utf-8')).decode('ascii')
        display_text = "[" + ", ".join(ref_nums) + "]"
        return payload_b64, display_text

    def build_field_chain_runs(self, payload_b64: str,
                                display_text: str) -> List[ET.Element]:
        """
        Return list of w:r elements forming a complete Golden Pattern B field chain:
        EN.CITE begin → instrText → EN.CITE.DATA begin(fldData) → instrText →
        EN.CITE.DATA separate → EN.CITE.DATA end → EN.CITE separate →
        display text → EN.CITE end
        """
        return [
            self.make_fldchar("begin"),                                          # EN.CITE begin
            self.make_run("instrText", " ADDIN EN.CITE "),                       # EN.CITE instrText
            self.make_fldchar("begin", payload_b64),                             # EN.CITE.DATA begin + fldData
            self.make_run("instrText", " ADDIN EN.CITE.DATA "),                  # EN.CITE.DATA instrText
            self.make_fldchar("separate"),                                       # EN.CITE.DATA separate
            self.make_fldchar("end"),                                            # EN.CITE.DATA end
            self.make_fldchar("separate"),                                       # EN.CITE separate
            self.make_run("t", display_text),                                    # Display text
            self.make_fldchar("end"),                                            # EN.CITE end
        ]

    def replace_citation_in_text(self, para: ET.Element, cite: Dict,
                                  mapping: Dict[str, Dict[str, str]]):
        """
        Split a text run at the citation token position and insert the field chain.
        Modifies the paragraph DOM in-place.
        """
        run = cite["run"]
        wt_elem = cite["wt_elem"]
        text = wt_elem.text or ""
        start = cite["match_start"]
        end = cite["match_end"]
        ref_nums = cite["ref_nums"]

        payload_b64, display_text = self.build_synthesis_payload(ref_nums, mapping)

        before_text = text[:start]
        after_text = text[end:]

        # Update original run to before_text
        wt_elem.text = before_text if before_text else None

        # Find insertion position (after current run)
        children = list(para)
        try:
            insert_idx = children.index(run) + 1
        except ValueError:
            return

        # Build field chain runs
        field_runs = self.build_field_chain_runs(payload_b64, display_text)

        # Add after-text run if needed
        if after_text:
            field_runs.append(self.make_run("t", after_text))

        # Insert
        for i, fr in enumerate(field_runs):
            para.insert(insert_idx + i, fr)


# ═══════════════════════════════════════════════════════════
# Citation Converter (orchestrates finder + builder)
# ═══════════════════════════════════════════════════════════
class CitationConverter:
    """Orchestrate finding and converting all bracket citations to EndNote fields."""

    def __init__(self, logger: Logger):
        self.log = logger
        self.finder = CitationFinder(logger)
        self.builder = FieldBuilder(logger)

    def convert_all(self, root: ET.Element, mapping: Dict[str, Dict[str, str]]) -> int:
        citations = self.finder.find_citations(root)

        # Group by paragraph, sort reverse within para to preserve indices
        by_para: Dict[int, List[Dict]] = {}
        for c in citations:
            by_para.setdefault(c["para_idx"], []).append(c)

        body = root.find(f"{{{W}}}body")
        paras = body.findall(f"{{{W}}}p") if body is not None else []

        converted = 0
        for para_idx, para_cites in by_para.items():
            if para_idx >= len(paras):
                continue
            para = paras[para_idx]
            para_cites.sort(key=lambda x: (x["run_idx"], x["match_start"]), reverse=True)

            for cite in para_cites:
                self.builder.replace_citation_in_text(para, cite, mapping)
                converted += 1

        self.log.log(f"Converted {converted} citation tokens to EndNote field chains")
        return converted


# ═══════════════════════════════════════════════════════════
# EN.REFLIST Anchor Manager
# ═══════════════════════════════════════════════════════════
class ENReflistManager:
    """Add / validate EN.REFLIST field anchor in bibliography section."""

    def __init__(self, logger: Logger):
        self.log = logger

    def find_bib_start(self, root: ET.Element) -> int:
        """Find paragraph index where bibliography begins."""
        body = root.find(f"{{{W}}}body")
        if body is None:
            return -1
        for i, p in enumerate(body.findall(f"{{{W}}}p")):
            p_text = ""
            for wt in p.iter(f"{{{W}}}t"):
                if wt.text:
                    p_text += wt.text
            if "参考文献" in p_text or "References" in p_text:
                return i
        return -1

    def add(self, root: ET.Element) -> bool:
        """Add EN.REFLIST begin/separate at first ref paragraph, end at last."""
        body = root.find(f"{{{W}}}body")
        if body is None:
            return False
        paras = body.findall(f"{{{W}}}p")

        bib_start = self.find_bib_start(root)
        if bib_start < 0:
            self.log.log("ERROR: Cannot find bibliography for EN.REFLIST")
            return False

        # Find first actual reference paragraph (starts with [1])
        first_ref = bib_start + 1
        for i in range(bib_start, len(paras)):
            p_text = "".join(wt.text or "" for wt in paras[i].iter(f"{{{W}}}t"))
            if re.match(r'^\[1\]', p_text.strip()):
                first_ref = i
                break

        ref_para = paras[first_ref]

        # Insert EN.REFLIST begin + instrText + separate at start
        fb = FieldBuilder(self.log)
        ref_para.insert(0, fb.make_fldchar("separate"))
        ref_para.insert(0, fb.make_run("instrText", " ADDIN EN.REFLIST "))
        ref_para.insert(0, fb.make_fldchar("begin"))

        # EN.REFLIST end at last paragraph
        paras[-1].append(fb.make_fldchar("end"))

        self.log.log(f"EN.REFLIST anchor: begin@{first_ref}, end@{len(paras)-1}")
        return True


# ═══════════════════════════════════════════════════════════
# Bibliography Style Manager
# ═══════════════════════════════════════════════════════════
class BibStyleManager:
    """Find, create, or apply EndNote Bibliography paragraph style."""

    def __init__(self, logger: Logger):
        self.log = logger

    def resolve_style_id(self, styles_root: ET.Element) -> Optional[str]:
        """Find existing EndNote Bibliography style ID."""
        for style in styles_root.findall(f"{{{W}}}style"):
            name_el = style.find(f"{{{W}}}name")
            if name_el is not None:
                val = name_el.get(f"{{{W}}}val", "")
                if "EndNote Bibliography" in val or "endnote bibliography" in val.lower():
                    return style.get(f"{{{W}}}styleId")
        return None

    def create_style(self, styles_root: ET.Element) -> str:
        """Create a new EndNote Bibliography style, return its ID."""
        used_ids = {s.get(f"{{{W}}}styleId", "") for s in styles_root}
        new_id = "100"
        while new_id in used_ids:
            new_id = str(int(new_id) + 1)

        style_el = ET.SubElement(styles_root, f"{{{W}}}style")
        style_el.set(f"{{{W}}}type", "paragraph")
        style_el.set(f"{{{W}}}styleId", new_id)

        name_el = ET.SubElement(style_el, f"{{{W}}}name")
        name_el.set(f"{{{W}}}val", "EndNote Bibliography")

        ppr = ET.SubElement(style_el, f"{{{W}}}pPr")
        sp = ET.SubElement(ppr, f"{{{W}}}spacing")
        sp.set(f"{{{W}}}line", "276")
        sp.set(f"{{{W}}}lineRule", "auto")

        self.log.log(f"Created EndNote Bibliography style with ID: {new_id}")
        return new_id

    def apply(self, styles_root: ET.Element, document_root: ET.Element) -> str:
        """Ensure style exists and apply to all bibliography paragraphs. Returns style_id."""
        style_id = self.resolve_style_id(styles_root)
        if style_id is None:
            style_id = self.create_style(styles_root)

        body = document_root.find(f"{{{W}}}body")
        if body is None:
            return style_id

        paras = body.findall(f"{{{W}}}p")
        bib_start = -1
        for i, p in enumerate(paras):
            p_text = "".join(wt.text or "" for wt in p.iter(f"{{{W}}}t"))
            if "参考文献" in p_text or "References" in p_text:
                bib_start = i
                break

        if bib_start >= 0:
            applied = 0
            for i in range(bib_start + 1, len(paras)):
                p = paras[i]
                ppr = p.find(f"{{{W}}}pPr")
                if ppr is None:
                    ppr = ET.Element(f"{{{W}}}pPr")
                    p.insert(0, ppr)
                pstyle = ppr.find(f"{{{W}}}pStyle")
                if pstyle is not None:
                    pstyle.set(f"{{{W}}}val", style_id)
                else:
                    pstyle = ET.SubElement(ppr, f"{{{W}}}pStyle")
                    pstyle.set(f"{{{W}}}val", style_id)
                applied += 1
            self.log.log(f"Applied style '{style_id}' to {applied} paragraphs")

        return style_id


# ═══════════════════════════════════════════════════════════
# XML Validator
# ═══════════════════════════════════════════════════════════
class XmlValidator:
    """Validate EndNote field structure in document XML."""

    def __init__(self, logger: Logger):
        self.log = logger

    def validate(self, root: ET.Element, expected_citations: int) -> Tuple[str, bool]:
        """Run all checks, return (report_text, passed)."""
        lines = []

        # 1. Well-formed XML
        try:
            s = ET.tostring(root, encoding='utf-8')
            ET.fromstring(s)
            lines.append("Well-formed XML Check: ✓ PASS")
        except ET.ParseError as e:
            lines.append(f"Well-formed XML Check: ✗ FAIL - {e}")
            return "\n".join(lines), False

        # 2. Field chain balance
        chars = list(root.iter(f"{{{W}}}fldChar"))
        begins = sum(1 for fc in chars if fc.get(f"{{{W}}}fldCharType") == "begin")
        seps = sum(1 for fc in chars if fc.get(f"{{{W}}}fldCharType") == "separate")
        ends = sum(1 for fc in chars if fc.get(f"{{{W}}}fldCharType") == "end")
        balanced = (begins == ends)
        exp = expected_citations * 2 + 1

        lines.append("\nField Chain Balance Check:")
        lines.append(f"  - <w:fldChar begin>: {begins}")
        lines.append(f"  - <w:fldChar separate>: {seps}")
        lines.append(f"  - <w:fldChar end>: {ends}")
        lines.append(f"  Expected: {exp} each ({expected_citations} citations + REFLIST)")
        lines.append(f"  → Result: {'✓ BALANCED' if balanced else '⚠ UNBALANCED'}")

        # 3. InstrText leading space
        instr_count = 0
        space_ok = 0
        for it in root.iter(f"{{{W}}}instrText"):
            if it.text and ("EN.CITE" in it.text or "EN.REFLIST" in it.text):
                instr_count += 1
                if it.text.startswith(" ADDIN"):
                    space_ok += 1
        lines.append("\nInstrText Format Check:")
        lines.append(f"  - Total instrText: {instr_count}")
        lines.append(f"  - Leading space OK: {space_ok}")
        lines.append(f"  → Result: {'✓ PASS' if instr_count == space_ok else '⚠ ISSUE'}")

        # 4. fldData count
        fld_count = sum(1 for _ in root.iter(f"{{{W}}}fldData"))
        lines.append("\nPayload Check:")
        lines.append(f"  - EN.CITE.DATA fldData elements: {fld_count}")
        lines.append(f"  - Expected: {expected_citations}")
        lines.append(f"  → Result: {'✓ PASS' if fld_count == expected_citations else '⚠ MISMATCH'}")

        all_pass = balanced and (instr_count == space_ok) and (fld_count == expected_citations)
        lines.append(f"\nOVERALL XML VALIDATION: {'✓ PASS' if all_pass else '⚠ ISSUES FOUND'}")
        return "\n".join(lines), all_pass


# ═══════════════════════════════════════════════════════════
# Report Generator
# ═══════════════════════════════════════════════════════════
class ReportGenerator:
    """Generate alignment reports and other output files."""

    @staticmethod
    def alignment_report(citations: List[Dict],
                          mapping: Dict[str, Dict[str, str]],
                          max_items: int = 50) -> str:
        lines = ["Payload Alignment Report", "=" * 60, ""]
        shown = 0
        for i, cite in enumerate(citations, 1):
            if shown >= max_items:
                break
            for num in cite["ref_nums"]:
                if shown >= max_items:
                    break
                meta = mapping.get(num, {})
                lines.append(f"[{num}] (citation #{i})")
                lines.append(f"  Author: {meta.get('author', 'N/A')[:80]}")
                lines.append(f"  Year: {meta.get('year', 'N/A')}")
                lines.append(f"  Title: {meta.get('title', 'N/A')[:80]}")
                lines.append(f"  Journal: {meta.get('journal', 'N/A')[:60]}")
                lines.append("")
                shown += 1

        if len(citations) > max_items:
            lines.append(f"... (truncated, {len(citations) - max_items} more citations)")
        lines.append("")
        lines.append("Mode: Synthesis Mode")
        lines.append("Note: Full metadata sync requires Word + EndNote 'Update Citations and Bibliography'.")
        return "\n".join(lines)
