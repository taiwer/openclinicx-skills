---
name: endnote-management
description: Build, preserve, validate, and repair EndNote-manageable citations in DOCX manuscripts. Use pre-built scripts in scripts/ instead of writing ad-hoc Python; only write custom logic for edge cases.
version: 1.2.0
author: GitHub Copilot
---


# EndNote Citation Management Skill v1.2.0

本技能确保文稿中的引用和文献区100%可被EndNote识别、编辑和更新，兼容性以真实EndNote产物为唯一标准。

## ⚡ Agent Quick Start（必读）

**本技能自带 `scripts/` 目录，包含可直接调用的通用脚本。Agent 不应每次从头编写 Python 转换代码。**

| 脚本 | 用途 | 何时调用 |
|------|------|----------|
| [`scripts/build_endnote.py`](scripts/build_endnote.py) | 一键 Build Mode 转换 | 用户要求将方括号引用转为 EndNote 格式 |
| [`scripts/validate_endnote.py`](scripts/validate_endnote.py) | 独立校验 | 转换后验证、调试问题、已有文件检查 |
| [`scripts/endnote_utils.py`](scripts/endnote_utils.py) | 共享工具库 | 被上面两个脚本自动 import，包含所有 OOXML 操作类 |

**Build Mode 一行命令完成：**
```bash
python3 scripts/build_endnote.py <input.docx>
python3 scripts/build_endnote.py <input.docx> -o <output.docx>
python3 scripts/build_endnote.py <input.docx> --mode synthesis    # 默认模式
```

**Validate 一行命令完成：**
```bash
python3 scripts/validate_endnote.py <file.docx>
python3 scripts/validate_endnote.py <file.docx> --strict
python3 scripts/validate_endnote.py <file.docx> -o report.txt
```

**Agent 规则：**
1. **优先调用 scripts/ 中的脚本**，不要重写转换逻辑。
2. 仅在脚本无法覆盖的边缘场景（如自定义 Donor-Clone、特殊引用格式）才编写额外代码。
3. 转换完成后**必须**运行 `validate_endnote.py --strict` 确认结果。

## 终极优化原则

1. **Build Mode 仅采用 Golden Pattern B（EN.CITE + EN.CITE.DATA + base64 payload）**，禁止输出任何残缺EN.CITE。
2. **强制 Donor-Clone（模板克隆）机制**：推荐维护一个健康的EndNote模板（Donor DOCX），所有引用字段直接克隆模板节点，仅替换RecNum/DisplayText/base64 payload明文部分。
3. **工程实践**：使用 `scripts/build_endnote.py` 一键完成解包-替换-校验-打包，避免手写拼接和多轮人工试错。
4. **所有校验与清理在单一脚本内完成**，校验不通过直接报错，杜绝半成品。

---

## Known failure learned from production

Case observed:

- A converted DOCX contained `EN.CITE` and passed XML checks.
- EndNote in Word still reported: no editable citations.
- Or: Citations looked correct but showed author names, titles, or years incorrectly in EndNote UI.

Root causes identified:

1. Using synthetic citation payloads that look valid but do not match EndNote-compatible field patterns used by real documents.
2. For multi-citation groups, missing or non-compatible `EN.CITE.DATA` + `w:fldData` structure.
3. Minor formatting details in `w:instrText` (for example missing leading space before `ADDIN`) can reduce compatibility.
4. **【新发现】Payload内的Author/Title/Year/Journal等关键元信息与参考文献条目不一致**：
   - 例如，payload中Author为"Author et al."，但参考文献中为"A Author, B Author, C Author"。
   - 例如，payload中Year为"2023"，但参考文献中为"2024"。
   - 这导致EndNote UI显示信息混乱或作者名错误，即使字段结构完全正确。

Operational takeaway:

- Structural validation is necessary but not sufficient.
- **Payload元信息与参考文献条目的一致性同样必不可少**。
- UI-level EndNote recognition in Word is the final acceptance criterion.

This skill supports two modes:

- `Build mode`: for new drafts that start from Markdown or plain DOCX with text-only citations.
- `Preserve mode`: for translation, polishing, or revision of an existing EndNote-enabled DOCX.

The priority is always:

1. Keep EndNote structures valid.
2. Keep citation-bibliography linkage intact.
3. Keep manuscript text edits correct.

## Non-negotiable principles

- A visible citation like `[1]` is not enough. It must be a Word field chain that EndNote can manage.
- Never delete or flatten EndNote field runs during editing.
- EndNote linkage is defined in `word/document.xml`; do not treat `word/endnotes.xml` as citation linkage.
- Use style name mapping from `word/styles.xml`; never hardcode style IDs.
- Validate after every conversion or major edit.

## Files that matter and why

In unzipped DOCX package:

- `[Content_Types].xml`: package content map. Usually not where citation logic lives.
- `_rels/.rels`: top-level relationships. Usually not a citation logic point.
- `word/document.xml`: main source of truth for citation fields and bibliography anchor.
- `word/styles.xml`: bibliography style definitions, such as `EndNote Bibliography`.
- `word/_rels/document.xml.rels`: relationships for the main document. Usually stable, but check if document is malformed.
- `word/endnotes.xml`: Word endnotes feature, not EndNote citation management.

## EndNote structures to preserve or build

### A. Citation field structure (body citations)

A valid citation is represented by Word field runs around the visible result text.

Common patterns seen in real files:

- `ADDIN EN.CITE <EndNote>...` inline payload in `w:instrText`.
- `ADDIN EN.CITE` plus nested `ADDIN EN.CITE.DATA` with base64 payload in `w:fldData`.

Important compatibility details from known-good files:

- `w:instrText` commonly starts with a leading space: ` ADDIN EN.CITE ...`
- In inline payloads, `DisplayText` is usually embedded inside `<Cite>`.
- Multi-cite groups are often represented by outer `EN.CITE` and inner `EN.CITE.DATA` with payload in `w:fldData`.

**【关键要求】Payload内容与参考文献条目必须一致**：

Payload通常包含以下元信息（base64编码或明文），**必须与文献映射JSON中的对应条目内容保持一致**：
- `Author`：作者信息
- `Title`：题目
- `Year`：年份
- `Journal`：期刊名
- `Volume/Pages`：卷次/页码
- `RecNum`：记录编号（与映射key对应）

如不一致，EndNote在Word中会出现作者信息错误、题目显示异常等问题。

Minimal practical requirement:

- There is an `EN.CITE` field chain around each citation instance.

Preferred robust requirement:

- `EN.CITE` + `EN.CITE.DATA` + payload with `RecNum` and `db-id`.
- **Payload的Author/Title/Year/Journal等关键字段与参考文献条目逐字对应**。

### B. Bibliography anchor structure (reference section)

A manageable bibliography must include a field anchor:

- First bibliography paragraph includes `ADDIN EN.REFLIST` with field begin and separate.
- Final anchor paragraph includes field end to close bibliography field.

Without `EN.REFLIST`, body citations may exist but bibliography linkage is often incomplete.

### C. Shared library context

Citations in one manuscript should normally share one `db-id` context (from EndNote payload).

- Mixed `db-id` values are allowed only if explicitly required by user.
- Otherwise treat mixed `db-id` as a warning or repair target.

### D. Bibliography paragraph style

Bibliography lines should use style named `EndNote Bibliography`.

- Resolve style by name in `styles.xml`.
- Use the style ID found in the current file (can be numeric like `198`).


## 前置步骤：文献映射提取与验证（所有模式必须）

在任何构建或修复操作之前，**必须先提取、验证、保存文献映射**：

1. **定位参考文献区**
   - 识别文献列表的起始和结束段落。
   - 支持常见格式：`References`、`参考文献`、`Bibliography` 等。

2. **按序号提取所有文献条目**
   - 逐条解析每个文献条目（如 `[1] Author et al., 2023...`）。
   - 提取 RecNum（序号）与完整文献文本。
   - **同时逐条解析出关键元信息**：Author、Title、Year、Journal、Volume、Pages等。

3. **构建文献映射JSON**
   ```json
   {
     "1": {
       "full_text": "Author et al., 2023. Title. Journal, 10(2):123-145.",
       "author": "Author et al.",
       "year": "2023",
       "title": "Title",
       "journal": "Journal",
       "volume": "10",
       "pages": "123-145"
     },
     "2": {...},
     ...
   }
   ```
   - RecNum 作为 key，文献全文和关键元信息作为 value。
   - 保存为 `{input_docx}.references.json`。

4. **验证映射完整性**
   - ✓ 序号连续无缺失（除非允许）。
   - ✓ 无重复序号。
   - ✓ 无空文献条目。
   - ✓ 关键元信息（Author/Year/Title等）字段完整。
   - ✓ 若校验失败，直接报错并输出日志。

5. **正文引用序号校验**
   - 扫描正文所有引用 token（如 `[1]`、`[1,2]`、`[3-5]`）。
   - 验证所有引用序号都在文献映射范围内。
   - 若出现超范围或无效序号，记录并报错。

---

## Build mode workflow

**直接调用预置脚本，一行命令完成：**

```bash
python3 scripts/build_endnote.py <input.docx>
python3 scripts/build_endnote.py <input.docx> -o <output.docx>
python3 scripts/build_endnote.py <input.docx> --mode synthesis
```

**脚本内部自动完成的流水线（无需 Agent 干预）：**

1. 解压 DOCX，解析 `word/document.xml` + `word/styles.xml`（DOM 操作，不是正则）
2. 定位参考文献区（支持中文"参考文献"和英文"References/Bibliography"）
3. 按序号提取所有文献条目及关键元信息（Author/Title/Year/Journal/Volume/Pages）
4. 构建映射 JSON，验证完整性
5. 扫描正文所有引用序号 `[N]`, `[N,N]`, `[N-N]`，校验是否在映射范围内
6. 使用 Golden Pattern B 为每个引用构建 EN.CITE + EN.CITE.DATA + base64 payload
7. 插入 EN.REFLIST 锚点到文献区
8. 查找/创建并应用 `EndNote Bibliography` 样式
9. **硬性自检**：XML well-formedness、field chain balance、instrText format、payload integrity
10. 打包输出，输出名默认加 `-SYNTHESIS-MODE.docx`

> Agent 只需调用脚本，脚本内部已实现所有 SKILL 要求的硬性规则（DOM 操作、禁止正则、XML 自检、Payload 对齐等）。

### 当需要 Donor-Clone 时

如果环境中有健康的 Donor DOCX，可在 `endnote_utils.py` 的 `FieldBuilder` 类中添加 `clone_from_donor()` 方法。当前 `scripts/build_endnote.py` 默认使用 Synthesis Mode。

> 可参考 `references/endnote-ooxml-golden-patterns.md` 作为 Golden Pattern 模板。


---

## Preserve mode workflow（有损风险最小化+映射保真+Payload对齐）

1. **前置步骤完成后**，确认文献映射有效且与正文引用一致。
2. **编辑前基线快照**：
   - 文献映射（.references.json，含关键元信息）。
   - 现有payload中的Author/Title/Year/Journal/Pages等（做对齐检查）。
   - 字段数量、EN.CITE/EN.CITE.DATA/EN.REFLIST 数量。
   - `db-id` 集合。
   - 文献区锚点位置。
3. **只改非字段 runs 的文本内容**，禁止修改引用序号或文献顺序。
4. **禁止清空或重写包含字段的整段内容**。
5. **编辑后重新提取文献映射和payload元信息**，对比基线：
   - 映射key是否变化（若变化说明文献被重新排列，需要修复所有引用RecNum）。
   - 文献条目内容是否变化（允许）。
   - **Payload中的Author/Title/Year/Journal等是否与新映射一致**（若不一致需要修复）。
6. **如果映射或字段结构被破坏，按修复优先级自动恢复**。
7. **如果Payload元信息不对齐，同步修复**。
8. 最终校验并输出。

---

## Validation gate（校验门禁）

### 前置映射校验（必须）

1. 文献映射JSON格式正确，无缺失/重复/空值。
2. 正文所有引用序号都在文献映射范围内。
3. 序号连续性符合预期（允许配置）。
4. 映射与现有字段结构一致（如有EN.CITE，RecNum必须对应映射key）。

### 字段链校验（Critical）

1. `EN.CITE` exists for citation-bearing body paragraphs.
2. `EN.REFLIST` exists in bibliography start and has closing field end.
3. Bibliography entries have `EndNote Bibliography` style applied.
4. Citation payloads contain parseable EndNote metadata (`RecNum`, `db-id`) where expected.
5. No malformed field chains (unbalanced begin/separate/end sequence).
6. Multi-cite blocks use compatible `EN.CITE.DATA` and valid base64 `w:fldData` where required.

### 强校验（Strong）

1. `db-id` count is 1 unless user requested multi-library merge.
2. Citation count in body is consistent with expected citation token count.
3. No accidental conversion inside bibliography text lines.
4. `DisplayText` in payload matches visible citation text.
5. `instrText` formatting is compatible with known-good pattern (including leading space before `ADDIN`).
6. **RecNum与文献映射key严格对应**，无误配或冗余。
7. **【关键】Payload中的Author/Title/Year/Journal/Pages等信息与文献映射条目逐字一致**：
   - 作者名拼写一致（包括大小写、姓名顺序等）
   - 题目内容完全相同
   - 年份、期刊、卷次、页码一致
   - 若不一致，直接标记为ERROR，不允许通过

### 验收校验（Acceptance）

1. EndNote UI recognizes citations as editable objects.
2. Word + EndNote update command succeeds on reopened file.
3. 文献映射与引用链接保持一致。

### 【硬性】XML合法性自检（必须）

**打包前强制执行以下检查，不允许跳过**：

1. **Well-formed XML验证**：
   - 使用 `xml.etree.ElementTree.fromstring(final_xml_string)` 对修改后的 `word/document.xml` 进行解析测试。
   - 必须能够成功解析（即便提示警告也可接受），否则说明字段链或替换逻辑破坏了结构。

2. **结构完整性检查**：
   - 验证所有 `<w:fldChar>` 标签的 begin/separate/end 配对是否平衡。
   - 验证所有 `<w:instrText>` 中的 ADDIN 指令格式是否完整。
   - 验证所有 `<w:fldData>` 中的 base64 是否可解码（不要求解析内容，仅验证base64格式）。

3. **节点计数一致性**：
   - 确认引用节点数与预期数量一致。
   - 确认EN.REFLIST节点是否完整。

**校验失败处理**：
- 若XML不well-formed，直接报错，不允许打包，要求Agent检查替换逻辑是否误伤了结构。
- 若节点不平衡，同样报错并输出具体破损位置（line number）。
- 输出详细错误日志，便于定位问题。

---

## Repair/Cleaning strategies（清理与修复建议）

1. **Donor-Clone修复**：始终优先，直接复制健康字段块。
2. **字段链修复**：如有损坏，重建begin/separate/end。
3. **Payload修复**：仅替换RecNum/db-id/DisplayText，避免全量重写。
4. **【关键】Payload元信息对齐修复**：
   - 从文献映射中提取Author/Title/Year/Journal/Pages等
   - 对比现有payload中的值，若不一致则直接覆盖
   - 特别注意：Author拼写、Title字符、Pages格式等细节必须逐字一致
   - 若映射本身格式错误，先修复映射再修复payload
5. **锚点修复**：补齐EN.REFLIST起止结构。
6. **样式修复**：按名称映射重新应用文献样式。
7. **全量复检**，不通过直接报错。

### 推荐配套脚本

- 提供一键清理/修复脚本（如clean_endnote_fields.py），支持：
  - 批量检测/修复/补建EN.CITE/EN.CITE.DATA/EN.REFLIST
  - 校验payload和样式
  - 日志输出与异常报错

---

## Anti-patterns (forbidden)

- Clearing all runs in a paragraph that contains fields.
- Replacing a citation paragraph by plain text in one write operation.
- Assuming style ID is always `EndNoteBibliography`.
- Using `word/endnotes.xml` as citation linkage source.
- Declaring success without post-edit validation.
- Claiming success without EndNote UI recognition check.
- Building synthetic payloads as first choice when donor field blocks are available.
- **【关键】Cloning payload from donor without verifying Author/Title/Year/Journal metadata matches the target bibliography entry**（盲目克隆payload而不检查作者/题目/年份/期刊等元信息是否与目标参考文献一致）。
  - 即使字段结构完全正确，如果payload元信息错误，EndNote UI会显示错误的作者名、题目等。
  - 必须逐条对比payload内容与参考文献条目。
- **【硬性禁令】使用泛匹配的正则表达式直接修改XML结构**（如 `re.sub(r'<w:r[^>]*>', ...)`）：
  - ✗ 禁止：`re.sub(r'<w:r[^>]*>', ...)` 会误伤 `<w:rPr>` 和其他子元素。
  - ✗ 禁止：`re.sub(r'<w:t[^>]*>', ...)` 可能破坏属性和嵌套结构。
  - ✗ 禁止：字符串级的 XML 拼接和替换。
  - ✓ 推荐：使用 lxml 或 xml.etree.ElementTree 的 DOM 操作（新建Element并append）。
  - ✓ 若必须用正则，加入严格边界符（如 `<w:r\b[^>]*>` 和 `<w:t\b[^>]*>`），但应优先用DOM操作。
- **【硬性禁令】跳过XML Well-formed自检直接打包**：
  - 修改后的 `document.xml` 必须通过 `xml.etree.ElementTree.fromstring()` 的解析验证。
  - 不能假设"字符串看起来对就没问题"。
  - 校验失败必定报错，不允许生成损坏的DOCX。

---

## Agent操作建议（工程落地）

- 用户要求翻译/润色，默认Preserve模式。
- 仅有方括号引用且无EndNote字段，直接Build模式，**一步到位Golden Pattern B+Donor-Clone**。
- 混合模式时，已管理引用保真，未管理引用补建。
- **【关键】若发现作者信息错误、题目显示异常，优先检查payload元信息是否与参考文献条目一致**。

### 纯净合成模式（Synthesis Mode）——当无Donor DOCX可用时

当环境中没有可信的Donor DOCX可克隆时，Agent应自动进入**纯净合成模式（Synthesis Mode）**，采用以下策略：

1. **最小化Payload策略**：
   - 只在 base64 payload 中保留最基础的结构：`<RecNum>` 和 `<DisplayText>` 和基本的 `<record>` 框架。
   - 作者、题目等元信息可以部分简化或留空（仅保留RecNum唯一标识能力）。

2. **Word文档层面的补全**：
   - 生成的DOCX中引用显示为 `[1]` 这样的格式（通过DisplayText控制）。
   - 用户在Word中打开该文件后，点击 "Update Citations and Bibliography"。
   - Word+EndNote会自动检查本地库中是否有对应RecNum的记录；若有，自动从库中拉取完整的Author/Title/Year/Journal等信息补全，同时更新DisplayText。

3. **工程可行性**：
   - 脚本只需确保生成的Golden Pattern B框架结构正确（begin/separate/end平衡）。
   - 不需要拼装复杂的base64，降低出错概率。
   - 最终可管理性完全由用户本地EndNote库决定，脚本职责是保证Word字段链有效。

4. **输出标记**：
   - 文件后缀标记为 `*-SYNTHESIS-MODE.docx`（区别于Donor-Clone模式的 `*-ENDNOTE-LINKED.docx`）。
   - 日志中明确标记：`Mode: Synthesis / Awaiting local library sync`。
   - 给用户建议：打开后需在Word中执行"更新引用与参考文献"以从本地库同步。

### 必须输出项

- 正文引用字段数
- EN.REFLIST存在性
- 文献样式映射结果
- 校验是否通过
- **Payload元信息对齐情况（Author/Title/Year/Journal等是否与参考文献条目一致）**
- **XML Well-formed 检验结果**
- **DOM树修改过程中的错误日志**（若有）
- EndNote UI可编辑性
- **Donor-Clone 或 Synthesis 模式标记**（包括预期的本地库同步说明）

---

## Scripts Architecture（v1.2.0 新增）

`scripts/` 目录提供三个可直接调用的脚本文件，无需每次编写转换代码：

```
scripts/
├── endnote_utils.py       # 共享工具库（所有 OOXML 操作类）
├── build_endnote.py       # 一键 Build Mode CLI
└── validate_endnote.py    # 独立校验 CLI
```

### `endnote_utils.py` — 共享工具库

包含以下可复用类（Agent 如需编写自定义逻辑，直接 import 使用）：

| 类 | 职责 |
|----|------|
| `Logger` | 时间戳日志 + 文件输出 |
| `DocxPackage` | DOCX 解包/解析/重新打包（context manager） |
| `ReferenceParser` | 参考文献提取与元信息解析（Author/Year/Title/Journal/Volume/Pages） |
| `CitationFinder` | DOM 遍历定位正文方括号引用 `[N]`, `[N,N]`, `[N-N]` |
| `CitationValidator` | 引用序号 vs 文献映射一致性校验 |
| `FieldBuilder` | Golden Pattern B 字段链构建（`make_run`, `make_fldchar`, `build_synthesis_payload`, `build_field_chain_runs`） |
| `CitationConverter` | 遍历全部引用并调用 `FieldBuilder` 替换 |
| `ENReflistManager` | EN.REFLIST 锚点插入/验证 |
| `BibStyleManager` | EndNote Bibliography 样式查找/创建/应用 |
| `XmlValidator` | XML well-formedness + field chain balance + instrText format + base64 integrity |
| `ReportGenerator` | Payload 对齐报告生成 |

### `build_endnote.py` — 一键转换

```bash
python3 scripts/build_endnote.py <input.docx>                     # Synthesis Mode，输出 <input>-SYNTHESIS-MODE.docx
python3 scripts/build_endnote.py <input.docx> -o <custom.docx>    # 自定义输出路径
python3 scripts/build_endnote.py <input.docx> --mode synthesis    # 显式指定模式
python3 scripts/build_endnote.py <input.docx> --no-reports        # 跳过报告生成
```

自动输出：`.docx`, `.docx.references.json`, `.log`, `.payload-alignment.txt`, `.xml-validation.txt`

### `validate_endnote.py` — 独立校验

```bash
python3 scripts/validate_endnote.py <file.docx>                  # 基本校验
python3 scripts/validate_endnote.py <file.docx> --strict          # 含引用一致性校验
python3 scripts/validate_endnote.py <file.docx> -o report.txt     # 输出到文件
```

校验项：Well-formed XML → Field chain balance → InstrText formatting → EN.REFLIST presence → Payload base64 integrity → (strict) citation-reference consistency

## Reusable reference assets

Use these files as canonical templates when moving this skill to other repositories:

- `skills/endnote-management/references/endnote-ooxml-golden-patterns.md` — Golden Pattern A/B/C OOXML templates
- `skills/endnote-management/scripts/endnote_utils.py` — Shared library (importable classes)
- `skills/endnote-management/scripts/build_endnote.py` — Universal Build Mode CLI
- `skills/endnote-management/scripts/validate_endnote.py` — Standalone validator CLI


## Minimal completion criteria（完成判定）

只有同时满足以下条件才能判定任务完成：
1. 文献映射JSON提取完整，无缺失/重复/超范围引用，关键元信息完整。
2. 正文所有引用序号都对应到文献映射key。
3. **Payload中的Author/Title/Year/Journal/Pages等与参考文献条目逐字一致**。
4. **输出DOCX通过XML Well-formed自检**（通过 `xml.etree.ElementTree.fromstring()` 验证）。
5. **字段链begin/separate/end配对平衡**，节点计数一致。
6. EndNote 能识别正文引用为可编辑字段对象。
7. 文献区由EN.REFLIST正确锚定。
8. 文档重开后引用-文献联动仍可用。
9. 修订内容未破坏字段完整性。
10. EndNote UI更新命令执行成功。

## 输出建议（包含文献映射+Payload对齐报告）

处理后应输出以下文件：

1. **DOCX输出**：
   - Build模式：`*-ENDNOTE-LINKED.docx`
   - Preserve模式：`*-PRESERVED-ENDNOTE.docx`

2. **文献映射JSON**：
   - 命名：`{输出文件名}.references.json`
   - 格式（包含关键元信息）：
     ```json
     {
       "1": {
         "full_text": "Author et al., 2023. Title. Journal, 10(2):123-145.",
         "author": "Author et al.",
         "year": "2023",
         "title": "Title",
         "journal": "Journal",
         "volume": "10",
         "pages": "123-145"
       },
       "2": {...},
       ...
     }
     ```

3. **Payload对齐报告**：
   - 命名：`{输出文件名}.payload-alignment.txt`
   - 内容：逐条记录每个引用的payload元信息与参考文献条目的对比结果
   - 示例：
     ```
     [1] ✓ PASS
       Author: Author et al. (mapping) == Author et al. (payload)
       Title: Title (mapping) == Title (payload)
       Year: 2023 (mapping) == 2023 (payload)
       Journal: Journal (mapping) == Journal (payload)
       Pages: 123-145 (mapping) == 123-145 (payload)
     
     [2] ✗ FAIL
       Author: Another Author (mapping) != Another Author et al. (payload) ❌ MISMATCH
       ...
     ```

4. **处理日志**：
   - 命名：`{输出文件名}.log`
   - 内容：
     - 映射提取结果（成功/失败）
     - 校验报告（通过/失败项）
     - Payload对齐情况
     - **XML Well-formed检验结果**（PASS/FAIL，含具体错误信息）
     - **DOM树修改过程中的错误日志**（若有begin/separate/end不平衡等）
     - 修复动作列表
     - 最终状态码

5. **XML校验报告**（若生成过程中有任何XML错误）：
   - 命名：`{输出文件名}.xml-validation.txt`
   - 内容：
     ```
     Well-formed XML Check: ✓ PASS / ✗ FAIL
     
     Field Chain Balance Check:
       - <w:fldChar begin>: 12 ✓
       - <w:fldChar separate>: 12 ✓
       - <w:fldChar end>: 12 ✓
       → Result: ✓ BALANCED
     
     Error Details (if any):
       Line 1234: <w:r> tag missing closing bracket
       Line 5678: Unbalanced <w:t> tag in field run
     ```

示例输出结构：
```
input_manuscript.docx
  → input_manuscript-ENDNOTE-LINKED.docx
  → input_manuscript-ENDNOTE-LINKED.docx.references.json
  → input_manuscript-ENDNOTE-LINKED.payload-alignment.txt
  → input_manuscript-ENDNOTE-LINKED.xml-validation.txt （若有错误）
  → input_manuscript-ENDNOTE-LINKED.log
```

这样做使得审校、回滚和重复处理都可追溯，特别是可快速定位作者信息错误、XML结构破损等问题。XML校验报告确保生成的DOCX绝不是残缺或损坏的文件。
