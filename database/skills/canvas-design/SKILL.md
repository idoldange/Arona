---
name: canvas-design
description: Create beautiful visual art as .png or .pdf files using design philosophy. Use when Sensei asks for a poster, piece of art, design, or other static visual. Create original designs; never copy existing artists' work.
---

# Canvas Design — Arona's Visual Art Skill

## Overview

Create high-quality visual art using **matplotlib** and **Pillow** via `run_code`. Write output to `OUTPUT_DIR`, set `send_output: "true"`.

This skill works in **two steps**:
1. **Design Philosophy** — define the aesthetic movement.
2. **Canvas Creation** — express it as a `.png` or `.pdf` file.

---

## Step 1: Design Philosophy Creation

Before touching code, create a visual philosophy. Name the movement (1-2 words like "Brutalist Joy" or "Chromatic Silence"), then articulate it in 4-6 paragraphs covering:

- Space and form
- Color and material
- Scale and rhythm
- Composition and balance
- Visual hierarchy

**Critical guidelines:**
- Each design aspect mentioned once — no repetition.
- Emphasize craftsmanship: "meticulous," "painstaking attention," "master-level execution."
- Leave creative space for visual interpretation — not too prescriptive.
- Information lives in *design*, not paragraphs.

**Examples of movements:**
- "Concrete Poetry" — monumental form, bold geometry, Brutalist spatial divisions, text as rare visual gesture.
- "Chromatic Language" — color as information system, geometric precision, typography as label.
- "Analog Meditation" — paper grain, vast negative space, whispered typography.
- "Organic Systems" — rounded forms, natural clustering, diagrams over prose.

---

## Step 2: Identify the Conceptual Reference

Before coding, extract the **subtle thread** from Sensei's request — not always literal, always sophisticated. Someone familiar with the subject should *feel* it; others simply experience a masterful composition.

---

## Step 3: Canvas Creation (matplotlib + Pillow)

### Matplotlib approach (vector-quality output)

```python
# artwork.png
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.colors import LinearSegmentedColormap

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

fig, ax = plt.subplots(figsize=(10, 14), dpi=150)
ax.set_aspect("equal")
ax.axis("off")
fig.patch.set_facecolor("#1E1E2E")

# ── Background ────────────────────────────────────────────────────────
ax.set_xlim(0, 100)
ax.set_ylim(0, 140)
bg = patches.Rectangle((0,0), 100, 140, fc="#1E1E2E", zorder=0)
ax.add_patch(bg)

# ── Example: geometric composition ───────────────────────────────────
# Large circle
circle = plt.Circle((50, 70), 40, color="#4472C4", alpha=0.8, zorder=1)
ax.add_patch(circle)

# Inner accent
circle2 = plt.Circle((50, 70), 28, color="#CDD6F4", alpha=0.15, zorder=2)
ax.add_patch(circle2)

# Horizontal rule
ax.plot([10, 90], [100, 100], color="#CDD6F4", lw=0.8, alpha=0.5, zorder=3)

# Typography (minimal, visual-first)
ax.text(50, 120, "MOVEMENT NAME", color="#CDD6F4", fontsize=14,
        ha="center", va="center", fontweight="bold",
        fontfamily="monospace", alpha=0.9, zorder=4)
ax.text(50, 113, "subtitle or year", color="#888899", fontsize=8,
        ha="center", va="center", fontfamily="monospace", alpha=0.7, zorder=4)

# ── Save ──────────────────────────────────────────────────────────────
out_path = os.path.join(OUTPUT_DIR, "artwork.png")
fig.savefig(out_path, bbox_inches="tight", facecolor=fig.get_facecolor(), dpi=150)
plt.close(fig)
print(f"Saved: {out_path}")
```

### Pillow approach (pixel-level control)

```python
# artwork.png
import os
from PIL import Image, ImageDraw, ImageFont
import numpy as np

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

W, H = 1200, 1600
img = Image.new("RGB", (W, H), color=(30, 30, 46))
draw = ImageDraw.Draw(img, "RGBA")

# Geometric shapes
draw.ellipse([W//2-300, H//2-300, W//2+300, H//2+300],
             fill=(68, 114, 196, 200))
draw.rectangle([100, 100, W-100, H-100],
               outline=(205, 214, 244, 80), width=2)

# Minimal text (use default font if custom fonts unavailable)
try:
    font_lg = ImageFont.truetype("/usr/share/fonts/truetype/jetbrains/JetBrainsMonoNerdFont-Regular.ttf", 60)
    font_sm = ImageFont.truetype("/usr/share/fonts/truetype/jetbrains/JetBrainsMonoNerdFont-Regular.ttf", 28)
except:
    font_lg = ImageFont.load_default()
    font_sm = ImageFont.load_default()

draw.text((W//2, 120), "MOVEMENT", font=font_lg, fill=(205, 214, 244, 230), anchor="mt")
draw.text((W//2, H-120), "2025", font=font_sm, fill=(136, 136, 153, 180), anchor="mb")

out_path = os.path.join(OUTPUT_DIR, "artwork.png")
img.save(out_path, quality=95)
print(f"Saved: {out_path}")
```

---

## Advanced Techniques

### Noise / texture field (matplotlib)
```python
from matplotlib.colors import LinearSegmentedColormap
import numpy as np

x = np.linspace(0, 1, 500)
y = np.linspace(0, 1, 500)
X, Y = np.meshgrid(x, y)
Z = np.sin(X * 8 * np.pi) * np.cos(Y * 6 * np.pi)
ax.contourf(X * 100, Y * 140, Z, levels=20, cmap="Blues", alpha=0.4, zorder=1)
```

### Gradient mesh (Pillow)
```python
import numpy as np
from PIL import Image

arr = np.zeros((H, W, 3), dtype=np.uint8)
for y in range(H):
    t = y / H
    arr[y, :] = [
        int(30 * (1-t) + 68 * t),   # R
        int(30 * (1-t) + 114 * t),  # G
        int(46 * (1-t) + 196 * t),  # B
    ]
img = Image.fromarray(arr)
```

---

## Final Refinement

After creating the first version, always refine:
1. Check composition balance and spacing.
2. Ensure text doesn't overlap shapes.
3. Tighten color relationships.
4. Push contrast between foreground and background.

Ask: "How can I make what's already here more of a piece of art?" — avoid adding more shapes just to fill space.

---

## Rules

- Write output to `OUTPUT_DIR`.
- Use `send_output: "true"` in `run_code`.
- `matplotlib` and `Pillow` (PIL) are pre-installed.
- JetBrains Mono Nerd Font is available at `/usr/share/fonts/truetype/jetbrains/`.
- Always wrap ImageFont.truetype in try/except with a fallback.
- Output must be a single `.png` or `.pdf` file (unless Sensei requests more pages).
- Text must never overflow the canvas boundaries.
