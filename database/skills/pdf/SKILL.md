---
name: pdf
description: "Use this skill whenever Sensei wants to do anything with PDF files: read/extract text, merge, split, create new PDFs, add watermarks, rotate pages, encrypt/decrypt, extract images. Triggers on .pdf file mentions or PDF-related requests."
---

# PDF — Arona's PDF Skill

## Overview

Use **pypdf** (reading/merging/splitting) and **reportlab** (creating new PDFs) via `run_code`. Always write to `OUTPUT_DIR` and set `send_output: "true"`.

---

## Reading & Extracting

```python
# read_pdf.py
import os
from pypdf import PdfReader

reader = PdfReader("uploaded.pdf")
print(f"Pages: {len(reader.pages)}")

text = ""
for i, page in enumerate(reader.pages):
    t = page.extract_text()
    if t:
        text += f"\n--- Page {i+1} ---\n{t}"

print(text[:3000])  # Print first 3000 chars
```

---

## Merging PDFs

```python
# merge.py
import os
from pypdf import PdfWriter, PdfReader

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

writer = PdfWriter()
for filename in ["doc1.pdf", "doc2.pdf"]:
    reader = PdfReader(filename)
    for page in reader.pages:
        writer.add_page(page)

with open(os.path.join(OUTPUT_DIR, "merged.pdf"), "wb") as f:
    writer.write(f)
print("Merged.")
```

---

## Splitting PDF

```python
# split.py
import os
from pypdf import PdfReader, PdfWriter

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

reader = PdfReader("uploaded.pdf")
for i, page in enumerate(reader.pages):
    writer = PdfWriter()
    writer.add_page(page)
    with open(os.path.join(OUTPUT_DIR, f"page_{i+1}.pdf"), "wb") as f:
        writer.write(f)
print(f"Split into {len(reader.pages)} files.")
```

---

## Creating a New PDF (reportlab)

```python
# create_pdf.py
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
out_path = os.path.join(OUTPUT_DIR, "document.pdf")

doc = SimpleDocTemplate(out_path, pagesize=A4,
                         leftMargin=2*cm, rightMargin=2*cm,
                         topMargin=2*cm, bottomMargin=2*cm)
styles = getSampleStyleSheet()
story = []

# Title
story.append(Paragraph("Document Title", styles["Title"]))
story.append(Spacer(1, 0.5*cm))

# Body
story.append(Paragraph("This is a paragraph of normal text.", styles["BodyText"]))
story.append(Spacer(1, 0.3*cm))

# Table
data = [["Name", "Value", "Notes"],
        ["Item A", "100", "Example"],
        ["Item B", "200", "Another"]]
t = Table(data, colWidths=[5*cm, 4*cm, 7*cm])
t.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
    ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
    ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
    ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
]))
story.append(t)

doc.build(story)
print(f"Created: {out_path}")
```

---

## Watermark

```python
# watermark.py
import os
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO

def make_watermark(text="CONFIDENTIAL"):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 60)
    c.setFillColorRGB(0.7, 0.7, 0.7, alpha=0.3)
    c.saveState()
    c.translate(A4[0]/2, A4[1]/2)
    c.rotate(45)
    c.drawCentredString(0, 0, text)
    c.restoreState()
    c.save()
    buf.seek(0)
    return PdfReader(buf)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

reader = PdfReader("uploaded.pdf")
wm_reader = make_watermark("CONFIDENTIAL")
wm_page = wm_reader.pages[0]

writer = PdfWriter()
for page in reader.pages:
    page.merge_page(wm_page)
    writer.add_page(page)

with open(os.path.join(OUTPUT_DIR, "watermarked.pdf"), "wb") as f:
    writer.write(f)
print("Done.")
```

---

## Encrypt / Decrypt

```python
# encrypt: add password
writer = PdfWriter()
writer.append(PdfReader("uploaded.pdf"))
writer.encrypt("password123")
with open(os.path.join(OUTPUT_DIR, "encrypted.pdf"), "wb") as f:
    writer.write(f)

# decrypt
reader = PdfReader("encrypted.pdf")
if reader.is_encrypted:
    reader.decrypt("password123")
```

---

## Rules

- Write all output files to `OUTPUT_DIR`.
- Use `send_output: "true"` in `run_code`.
- `pypdf` and `reportlab` are pre-installed in the sandbox.
- Uploaded PDFs from Sensei are in the working directory — reference by filename only.
