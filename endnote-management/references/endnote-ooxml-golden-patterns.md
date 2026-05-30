# EndNote OOXML Golden Patterns

This file stores known-good OOXML patterns for EndNote-manageable citations.

Use these patterns when Build mode output is not recognized by EndNote UI.

## Why this matters

A document can pass structural checks (`EN.CITE` exists, `EN.REFLIST` exists) and still be rejected by EndNote as "no editable citations".

The main reason is usually not visible text; it is field payload compatibility.

## Golden pattern A: single citation (inline EN.CITE payload)

Observed in known-good manuscript.

```xml
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r>
  <w:instrText xml:space="preserve"> ADDIN EN.CITE <EndNote><Cite><Author>Liang</Author><Year>2026</Year><RecNum>796</RecNum><DisplayText><style face="superscript">[1]</style></DisplayText><record>...</record></Cite></EndNote></w:instrText>
</w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>[1]</w:t></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

Required details:

- `instrText` starts with a leading space before `ADDIN`.
- `DisplayText` is inside `<Cite>`, not only under top-level `<EndNote>`.
- Keep original `RecNum`/`db-id` semantics if available.

## Golden pattern B: multi-citation (EN.CITE + EN.CITE.DATA)

Observed in known-good manuscript.

```xml
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE </w:instrText></w:r>
<w:r>
  <w:fldChar w:fldCharType="begin">
    <w:fldData xml:space="preserve">BASE64_OF_<EndNote><Cite>...</Cite><Cite>...</Cite></EndNote></w:fldData>
  </w:fldChar>
</w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>[1, 2]</w:t></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

Required details:

- Outer `EN.CITE` field wraps the display text.
- Inner `EN.CITE.DATA` stores base64 payload in `w:fldData`.
- Display text in payload and visible text should match.

## Golden pattern C: bibliography anchor (EN.REFLIST)

```xml
<!-- At first bibliography entry paragraph -->
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN EN.REFLIST </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>

<!-- At last bibliography entry paragraph -->
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

Also apply paragraph style name `EndNote Bibliography` (resolve real styleId in file).

## Compatibility rule

When converting plain bracket citations to EndNote:

1. First choice: clone field blocks from a trusted EndNote-enabled donor document in same topic/library.
2. Second choice: synthesize fields only if no donor exists, then treat as provisional and verify in EndNote UI.

## Quick diagnosis checklist

- EndNote says no editable citations:
  - Check leading space in `instrText` (`" ADDIN EN.CITE"`).
  - Check whether multi-cite uses `EN.CITE.DATA` and `w:fldData`.
  - Check `DisplayText` placement and consistency.
  - Check `RecNum`/`db-id` validity against library context.
- EndNote says INVALID CITATION:
  - Usually `RecNum`/`db-id` mismatch, not visual format issue.
