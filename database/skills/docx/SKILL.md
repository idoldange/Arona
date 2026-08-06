---
name: docx
description: "Use this skill whenever Sensei wants to create, read, edit, or manipulate Word documents (.docx files). Triggers: 'Word doc', 'word document', '.docx', reports, memos, letters, templates with formatting like headings, tables, page numbers. Do NOT use for PDFs, spreadsheets, or general coding."
---

# DOCX — Arona's Word Document Skill

## Overview

Use **python-docx** via `run_code` to create or edit `.docx` files. Always write files to `OUTPUT_DIR` and set `send_output: "true"` so they're sent to Discord automatically.

## Quick Reference

| Task | Approach |
|------|----------|
| Create new document | python-docx (see below) |
| Read/extract content | python-docx `.paragraphs`, `.tables` |
| Edit existing doc | Upload file → load with python-docx → modify → save |

---

## Creating a Document

```python
# document.docx
import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

doc = Document()

# --- Page setup (A4) ---
section = doc.sections[0]
section.page_width  = Cm(21)
section.page_height = Cm(29.7)
section.left_margin = section.right_margin = Cm(2.54)
section.top_margin  = section.bottom_margin = Cm(2.54)

# --- Title ---
title = doc.add_heading("Document Title", level=0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER

# --- Heading ---
doc.add_heading("Section 1", level=1)

# --- Paragraph ---
p = doc.add_paragraph("Normal paragraph text here.")

# --- Bold / Italic inline ---
p2 = doc.add_paragraph()
p2.add_run("Bold text ").bold = True
p2.add_run("and ").italic = True
p2.add_run("normal text.")

# --- Table ---
table = doc.add_table(rows=2, cols=3)
table.style = "Table Grid"
hdr = table.rows[0].cells
hdr[0].text, hdr[1].text, hdr[2].text = "Name", "Value", "Notes"
row = table.rows[1].cells
row[0].text, row[1].text, row[2].text = "Item A", "100", "Example"

# --- Page break ---
doc.add_page_break()

# --- Save ---
out_path = os.path.join(OUTPUT_DIR, "document.docx")
doc.save(out_path)
print(f"Saved: {out_path}")
```

Run with:
```
run_code(action="run_code", code=<above>, send_output="true")
```

---

## Styles & Formatting

### Font colors
```python
from docx.shared import RGBColor
run = paragraph.add_run("Colored text")
run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)  # Red
```

### Custom paragraph style
```python
style = doc.styles.add_style("CustomBody", WD_STYLE_TYPE.PARAGRAPH)
style.font.name = "Arial"
style.font.size = Pt(11)
para = doc.add_paragraph("Styled text", style="CustomBody")
```

### Cell background color
```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def set_cell_bg(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)

set_cell_bg(table.rows[0].cells[0], "4472C4")  # Blue header
```

---

## Reading an Existing Document

If Sensei uploads a `.docx` file, it's available in the workspace directory. Reference it by filename:

```python
# read_doc.py
import os
from docx import Document

# File will be in the working directory (same as script)
doc = Document("uploaded_file.docx")

for para in doc.paragraphs:
    if para.text.strip():
        print(f"[{para.style.name}] {para.text}")

for i, table in enumerate(doc.tables):
    print(f"\nTable {i+1}:")
    for row in table.rows:
        print([cell.text for cell in row.cells])
```

---

## Editing an Existing Document

```python
# edit_doc.py
import os
from docx import Document
from docx.shared import Pt

doc = Document("uploaded_file.docx")

# Find and replace text
for para in doc.paragraphs:
    for run in para.runs:
        if "OLD_TEXT" in run.text:
            run.text = run.text.replace("OLD_TEXT", "NEW_TEXT")

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
doc.save(os.path.join(OUTPUT_DIR, "edited_document.docx"))
print("Done")
```

---

## Rules

- Always write output to `OUTPUT_DIR` (set by environment automatically).
- Always use `send_output: "true"` in `run_code` so the file is sent to Discord.
- Uploaded files from Sensei are in the workspace directory — reference by filename only (not full path).
- `python-docx` is pre-installed in the sandbox.
