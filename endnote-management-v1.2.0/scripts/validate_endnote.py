#!/usr/bin/env python3
"""
validate_endnote.py — Standalone EndNote Field Validator
=========================================================
Validates an existing DOCX (with or without EndNote fields) for:
- Well-formed XML
- Field chain balance (begin/separate/end)
- InstrText formatting
- Reference citation consistency
- EN.REFLIST anchor presence
- Payload integrity

Usage:
  python3 validate_endnote.py <file.docx>
  python3 validate_endnote.py <file.docx> --strict
  python3 validate_endnote.py <file.docx> --output report.txt

Part of: endnote-management skill v1.2.0
Requires: endnote_utils.py in same directory
"""

import os
import sys
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from endnote_utils import (
    Logger, DocxPackage, ReferenceParser, CitationFinder,
    CitationValidator, XmlValidator, W
)

# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Validate EndNote field integrity in a DOCX file."
    )
    parser.add_argument("input", help="DOCX file to validate")
    parser.add_argument("-o", "--output", default=None,
                        help="Write validation report to file (default: stdout)")
    parser.add_argument("--strict", action="store_true",
                        help="Also validate citation-to-reference consistency")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"ERROR: File not found: {input_path}")
        return 1

    logger = Logger()
    logger.log(f"Validating: {input_path}")

    # Unpack
    pkg = None
    try:
        pkg = DocxPackage(input_path, logger)
        pkg.unpack()
    except Exception as e:
        logger.log(f"FATAL: Cannot open DOCX — {e}")
        return 1

    root = pkg.document_root
    exit_code = 0
    report_lines = []

    # ── Check 1: Well-formed XML ──
    logger.log("\n[1] Well-formed XML")
    import xml.etree.ElementTree as ET
    try:
        s = ET.tostring(root, encoding='utf-8')
        ET.fromstring(s)
        logger.log("  ✓ PASS")
        report_lines.append("Well-formed XML: ✓ PASS")
    except ET.ParseError as e:
        logger.log(f"  ✗ FAIL — {e}")
        report_lines.append(f"Well-formed XML: ✗ FAIL — {e}")
        exit_code = 1

    # ── Check 2: Field chain balance ──
    logger.log("\n[2] Field chain balance")
    chars = list(root.iter(f"{{{W}}}fldChar"))
    begins = sum(1 for fc in chars if fc.get(f"{{{W}}}fldCharType") == "begin")
    seps = sum(1 for fc in chars if fc.get(f"{{{W}}}fldCharType") == "separate")
    ends = sum(1 for fc in chars if fc.get(f"{{{W}}}fldCharType") == "end")

    if begins == 0:
        logger.log("  ⚠ No field chains found (not an EndNote-enabled document)")
        report_lines.append("Field chains: NONE (plain document)")
    elif begins == ends:
        logger.log(f"  ✓ BALANCED (begin={begins}, separate={seps}, end={ends})")
        report_lines.append(f"Field chains: ✓ BALANCED (begin={begins}, separate={seps}, end={ends})")
    else:
        logger.log(f"  ✗ UNBALANCED (begin={begins}, separate={seps}, end={ends})")
        report_lines.append(f"Field chains: ✗ UNBALANCED (begin={begins}, separate={seps}, end={ends})")
        exit_code = 1

    # ── Check 3: InstrText format ──
    logger.log("\n[3] InstrText format")
    instr_count = 0
    no_leading_space = 0
    for it in root.iter(f"{{{W}}}instrText"):
        if it.text and ("EN.CITE" in it.text or "EN.REFLIST" in it.text):
            instr_count += 1
            if not it.text.startswith(" ADDIN"):
                no_leading_space += 1
                logger.log(f"  ⚠ Missing leading space: '{it.text[:40]}...'")

    if instr_count == 0:
        logger.log("  ⚠ No EndNote instrText found")
        report_lines.append("InstrText: NONE")
    elif no_leading_space == 0:
        logger.log(f"  ✓ PASS ({instr_count} instrText, all have leading space)")
        report_lines.append(f"InstrText: ✓ PASS ({instr_count} all correct)")
    else:
        logger.log(f"  ✗ {no_leading_space}/{instr_count} missing leading space")
        report_lines.append(f"InstrText: ✗ {no_leading_space}/{instr_count} missing leading space")
        exit_code = 1

    # ── Check 4: EN.REFLIST presence ──
    logger.log("\n[4] EN.REFLIST anchor")
    reflist_count = sum(1 for it in root.iter(f"{{{W}}}instrText")
                        if it.text and "EN.REFLIST" in it.text)
    if reflist_count > 0:
        logger.log(f"  ✓ FOUND ({reflist_count} instance(s))")
        report_lines.append(f"EN.REFLIST: ✓ FOUND ({reflist_count})")
    else:
        logger.log("  ⚠ NOT FOUND (bibliography may not be EndNote-managed)")
        report_lines.append("EN.REFLIST: ⚠ NOT FOUND")

    # ── Check 5: fldData integrity ──
    logger.log("\n[5] Payload integrity (fldData)")
    import base64
    fld_data_elems = list(root.iter(f"{{{W}}}fldData"))
    fld_ok = 0
    fld_fail = 0
    for fd in fld_data_elems:
        if fd.text:
            try:
                base64.b64decode(fd.text)
                fld_ok += 1
            except Exception:
                fld_fail += 1

    if fld_data_elems:
        if fld_fail == 0:
            logger.log(f"  ✓ PASS ({fld_ok} payloads valid base64)")
            report_lines.append(f"Payloads: ✓ PASS ({fld_ok} valid base64)")
        else:
            logger.log(f"  ✗ {fld_fail}/{fld_ok+fld_fail} invalid base64")
            report_lines.append(f"Payloads: ✗ {fld_fail}/{fld_ok+fld_fail} invalid base64")
            exit_code = 1
    else:
        logger.log("  ⚠ No fldData elements")
        report_lines.append("Payloads: NONE")

    # ── Check 6 (--strict): citation-reference consistency ──
    if args.strict:
        logger.log("\n[6] Citation-reference consistency (--strict)")
        finder = CitationFinder(logger)
        citations = finder.find_citations(root)

        ref_parser = ReferenceParser(logger)
        mapping = ref_parser.build_mapping(pkg.full_text)

        validator = CitationValidator(logger)
        if validator.validate(citations, mapping):
            report_lines.append("Citation-ref consistency: ✓ PASS")
        else:
            report_lines.append("Citation-ref consistency: ✗ FAIL")
            exit_code = 1

    # ── Summary ──
    logger.log("\n" + "=" * 40)
    summary = "VALIDATION: " + ("✓ PASS" if exit_code == 0 else "✗ ISSUES FOUND")
    logger.log(summary)
    report_lines.insert(0, summary)
    report_lines.insert(0, f"File: {input_path}")
    report_lines.insert(0, "=" * 40)

    full_report = "\n".join(report_lines)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(full_report)
        logger.log(f"Report saved: {args.output}")
    else:
        print("\n" + full_report)

    logger.flush()
    pkg.cleanup()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
