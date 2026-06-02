#!/usr/bin/env python3
"""
build_endnote.py — Universal Build Mode CLI
============================================
Converts any DOCX with plain bracket citations [N], [N,N], [N-N]
into EndNote-manageable format using Golden Pattern B.

Usage:
  python3 build_endnote.py <input.docx> [--output <output.docx>] [--mode synthesis|donor]
  python3 build_endnote.py 心脏病影响因素研究综述.docx
  python3 build_endnote.py 心脏病影响因素研究综述.docx -o result.docx

Part of: endnote-management skill v1.2.0
Requires: endnote_utils.py in same directory
"""

import os
import sys
import json
import argparse

# Ensure we can import from the scripts directory
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from endnote_utils import (
    Logger, DocxPackage, ReferenceParser, CitationValidator,
    CitationConverter, ENReflistManager, BibStyleManager,
    XmlValidator, ReportGenerator
)

# ═══════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Convert plain bracket citations in DOCX to EndNote-manageable fields."
    )
    parser.add_argument("input", help="Input DOCX file path")
    parser.add_argument("-o", "--output", default=None,
                        help="Output DOCX path (default: <input>-SYNTHESIS-MODE.docx)")
    parser.add_argument("--mode", choices=["synthesis", "donor"], default="synthesis",
                        help="Conversion mode (default: synthesis)")
    parser.add_argument("--no-reports", action="store_true",
                        help="Skip generating report files")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"ERROR: Input file not found: {input_path}")
        return 1

    base, ext = os.path.splitext(input_path)
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        suffix = "-SYNTHESIS-MODE" if args.mode == "synthesis" else "-ENDNOTE-LINKED"
        output_path = f"{base}{suffix}{ext}"

    out_dir = os.path.dirname(output_path)
    out_base = os.path.splitext(os.path.basename(output_path))[0]
    log_path = os.path.join(out_dir, f"{out_base}.log")
    refs_json_path = os.path.join(out_dir, f"{output_path}.references.json")
    align_path = os.path.join(out_dir, f"{out_base}.payload-alignment.txt")
    xml_val_path = os.path.join(out_dir, f"{out_base}.xml-validation.txt")

    # ── Init logger ──
    logger = Logger(log_path)
    logger.log("=" * 60)
    logger.log(f"EndNote Build Mode — {args.mode.upper()}")
    logger.log(f"Input:  {input_path}")
    logger.log(f"Output: {output_path}")
    logger.log("=" * 60)

    # ── Phase 1: Extract document ──
    logger.log("\n[Phase 1] Extract document content")
    pkg = None
    try:
        pkg = DocxPackage(input_path, logger)
        pkg.unpack()
    except Exception as e:
        logger.log(f"FATAL: Failed to unpack DOCX — {e}")
        logger.flush()
        return 1

    logger.log(f"Extracted {len(pkg.full_text)} characters")

    # ── Phase 2: Extract references ──
    logger.log("\n[Phase 2] Extract references & metadata")
    ref_parser = ReferenceParser(logger)
    mapping = ref_parser.build_mapping(pkg.full_text)
    if not mapping:
        logger.log("FATAL: Cannot build reference mapping")
        logger.flush()
        return 1

    if not args.no_reports:
        with open(refs_json_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        logger.log(f"Saved: {refs_json_path}")

    # ── Phase 3: Find & validate citations ──
    logger.log("\n[Phase 3] Find & validate body citations")
    converter = CitationConverter(logger)
    citations = converter.finder.find_citations(pkg.document_root)

    validator = CitationValidator(logger)
    if not validator.validate(citations, mapping):
        logger.log("ERROR: Citation validation failed")
        logger.flush()
        return 1

    # ── Phase 4: Convert citations ──
    logger.log("\n[Phase 4] Apply EndNote field chains (Golden Pattern B)")
    total = converter.convert_all(pkg.document_root, mapping)

    # ── Phase 5: Add EN.REFLIST anchor ──
    logger.log("\n[Phase 5] Add EN.REFLIST anchor")
    reflist_mgr = ENReflistManager(logger)
    reflist_ok = reflist_mgr.add(pkg.document_root)

    # ── Phase 6: Apply bibliography style ──
    logger.log("\n[Phase 6] Apply EndNote Bibliography style")
    if pkg.styles_tree is None:
        logger.log("WARNING: No styles.xml found, loading standalone")
        import xml.etree.ElementTree as ET
        styles_path = os.path.join(pkg.tmpdir, "word", "styles.xml")
        pkg.styles_tree = ET.parse(styles_path) if os.path.exists(styles_path) else None

    style_id = "N/A"
    if pkg.styles_tree:
        style_mgr = BibStyleManager(logger)
        style_id = style_mgr.apply(pkg.styles_tree.getroot(), pkg.document_root)

    # ── Phase 7: Validate XML ──
    logger.log("\n[Phase 7] XML validation")
    xml_val = XmlValidator(logger)
    val_report, val_pass = xml_val.validate(pkg.document_root, total)
    logger.log(val_report)

    if not args.no_reports:
        with open(xml_val_path, "w", encoding="utf-8") as f:
            f.write(val_report)
        logger.log(f"Saved: {xml_val_path}")

    # ── Phase 8: Generate alignment report ──
    if not args.no_reports:
        logger.log("\n[Phase 8] Generate reports")
        align_rpt = ReportGenerator.alignment_report(citations, mapping)
        with open(align_path, "w", encoding="utf-8") as f:
            f.write(align_rpt)
        logger.log(f"Saved: {align_path}")

    # ── Phase 9: Repack ──
    logger.log("\n[Phase 9] Repack output DOCX")
    try:
        pkg.repack(output_path)
    except Exception as e:
        logger.log(f"FATAL: Repack failed — {e}")
        logger.flush()
        return 1

    # ── Final summary ──
    logger.log("\n" + "=" * 60)
    logger.log("CONVERSION COMPLETE")
    logger.log(f"Output:          {output_path}")
    logger.log(f"Mode:            {args.mode.upper()}")
    logger.log(f"Citations:       {total}")
    logger.log(f"References:      {len(mapping)}")
    logger.log(f"EN.REFLIST:      {'✓' if reflist_ok else '⚠ NOT ADDED'}")
    logger.log(f"Bib Style ID:    {style_id}")
    logger.log(f"XML Validation:  {'✓ PASS' if val_pass else '⚠ ISSUES'}")
    if args.mode == "synthesis":
        logger.log("")
        logger.log("IMPORTANT: Open in Word+EndNote, click 'Update Citations and Bibliography'")
        logger.log("to sync with your local EndNote library.")
    logger.log("=" * 60)
    logger.flush()

    return 0 if val_pass else 1


if __name__ == "__main__":
    sys.exit(main())
