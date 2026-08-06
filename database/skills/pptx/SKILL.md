---
name: pptx
description: "Use this skill any time a .pptx file is involved: creating slide decks, pitch decks, presentations; reading or extracting text from .pptx files; editing or updating existing presentations. Trigger on 'deck', 'slides', 'presentation', or .pptx filenames."
---

# PPTX — Arona's Presentation Skill

## Overview

Use **python-pptx** via `run_code`. Write output to `OUTPUT_DIR`, set `send_output: "true"`.

---

## Creating a Presentation from Scratch

```python
# presentation.pptx
import os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

prs = Presentation()
prs.slide_width  = Inches(13.33)   # 16:9 widescreen
prs.slide_height = Inches(7.5)

# ── SLIDE 1: Title slide ──────────────────────────────────────────────
blank_layout = prs.slide_layouts[6]  # completely blank
slide = prs.slides.add_slide(blank_layout)

# Dark background
from pptx.util import Inches
bg = slide.shapes.add_shape(
    1,  # MSO_SHAPE_TYPE.RECTANGLE
    0, 0, prs.slide_width, prs.slide_height
)
bg.fill.solid()
bg.fill.fore_color.rgb = RGBColor(0x1E, 0x1E, 0x2E)
bg.line.fill.background()

# Title text box
txBox = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11.33), Inches(1.5))
tf = txBox.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.alignment = PP_ALIGN.CENTER
run = p.add_run()
run.text = "Presentation Title"
run.font.size = Pt(48)
run.font.bold = True
run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

# Subtitle
txBox2 = slide.shapes.add_textbox(Inches(1), Inches(4.2), Inches(11.33), Inches(0.8))
tf2 = txBox2.text_frame
p2 = tf2.paragraphs[0]
p2.alignment = PP_ALIGN.CENTER
run2 = p2.add_run()
run2.text = "Subtitle or Author Name"
run2.font.size = Pt(24)
run2.font.color.rgb = RGBColor(0xAA, 0xAA, 0xCC)

# ── SLIDE 2: Bullet slide ─────────────────────────────────────────────
slide2 = prs.slides.add_slide(prs.slide_layouts[6])

# Background
bg2 = slide2.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
bg2.fill.solid()
bg2.fill.fore_color.rgb = RGBColor(0xF5, 0xF5, 0xF7)
bg2.line.fill.background()

# Heading
h = slide2.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
hp = h.text_frame.paragraphs[0]
hr = hp.add_run()
hr.text = "Key Points"
hr.font.size = Pt(36)
hr.font.bold = True
hr.font.color.rgb = RGBColor(0x1E, 0x1E, 0x2E)

# Bullets
bullets = ["First important point", "Second important point", "Third important point"]
body = slide2.shapes.add_textbox(Inches(0.8), Inches(1.5), Inches(11), Inches(4.5))
tf3 = body.text_frame
tf3.word_wrap = True
for i, bullet in enumerate(bullets):
    p = tf3.paragraphs[0] if i == 0 else tf3.add_paragraph()
    p.level = 0
    r = p.add_run()
    r.text = f"• {bullet}"
    r.font.size = Pt(22)
    r.font.color.rgb = RGBColor(0x33, 0x33, 0x33)

# ── Save ──────────────────────────────────────────────────────────────
out_path = os.path.join(OUTPUT_DIR, "presentation.pptx")
prs.save(out_path)
print(f"Saved: {out_path}")
```

---

## Reading an Existing Presentation

```python
# read_pptx.py
from pptx import Presentation

prs = Presentation("uploaded.pptx")
print(f"Slides: {len(prs.slides)}")
for i, slide in enumerate(prs.slides):
    print(f"\n--- Slide {i+1} ---")
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                if para.text.strip():
                    print(f"  [{shape.name}] {para.text}")
```

---

## Editing an Existing Presentation

```python
# edit_pptx.py
import os
from pptx import Presentation
from pptx.util import Pt
from pptx.dml.color import RGBColor

prs = Presentation("uploaded.pptx")

# Replace text across all slides
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if "OLD_TEXT" in run.text:
                        run.text = run.text.replace("OLD_TEXT", "NEW_TEXT")

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
prs.save(os.path.join(OUTPUT_DIR, "edited.pptx"))
print("Done.")
```

---

## Adding Images to a Slide

```python
from pptx.util import Inches

# Sensei's uploaded image is in the workspace directory
slide.shapes.add_picture("photo.jpg", Inches(1), Inches(1.5), Inches(5), Inches(3.5))
```

---

## Design Tips

- **Widescreen (16:9)**: `slide_width=Inches(13.33)`, `slide_height=Inches(7.5)` — use this by default.
- **Dark theme**: Fill background shape with dark RGB, use white/light text.
- **Consistency**: Apply same font sizes, colors, and margins across slides.
- **Avoid built-in layouts**: Use blank layout (`prs.slide_layouts[6]`) + manual text boxes for full control.
- **No emoji in text**: python-pptx may not render them correctly.

---

## Rules

- Write all output to `OUTPUT_DIR`.
- Use `send_output: "true"` in `run_code`.
- `python-pptx` is pre-installed in the sandbox.
- Uploaded `.pptx` files are in the working directory — reference by filename only.
