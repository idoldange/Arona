---
name: discord-gif-creator
description: Create animated GIFs optimized for Discord. Use when Sensei requests an animated GIF, emoji GIF, or Discord sticker. Works with PIL/Pillow via run_code.
---

# Discord GIF Creator — Arona's Animation Skill

## Overview

Create animated GIFs using **Pillow** (PIL) via `run_code`. Write output to `OUTPUT_DIR`, set `send_output: "true"`.

---

## Discord Size Requirements

| Type | Dimensions | Max File Size | FPS |
|------|-----------|--------------|-----|
| Emoji/Reaction | 128×128 px | 256 KB | 10-15 |
| Sticker | 320×320 px | 500 KB | 10-20 |
| Message GIF | Up to 480×480 px | 8 MB | 10-30 |
| Nitro Emoji | 128×128 px | 256 KB | 10-15 |

---

## Core Workflow

```python
# animation.gif
import os
import math
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

W, H = 128, 128
FPS = 12
DURATION_MS = 1000 // FPS  # ms per frame
NUM_FRAMES = 24

frames = []
for i in range(NUM_FRAMES):
    t = i / NUM_FRAMES  # 0.0 → 1.0

    frame = Image.new("RGBA", (W, H), (30, 30, 46, 255))
    draw = ImageDraw.Draw(frame)

    # === YOUR ANIMATION CODE HERE ===
    # Example: bouncing circle
    y = int(H * 0.3 + H * 0.4 * abs(math.sin(t * math.pi * 2)))
    draw.ellipse([W//2-15, y-15, W//2+15, y+15],
                 fill=(100, 180, 255, 255), outline=(200, 220, 255, 255), width=2)

    frames.append(frame.convert("RGBA"))

# Save GIF
out_path = os.path.join(OUTPUT_DIR, "animation.gif")
frames[0].save(
    out_path,
    save_all=True,
    append_images=frames[1:],
    duration=DURATION_MS,
    loop=0,          # 0 = loop forever
    optimize=True,
)
import os as _os
size_kb = _os.path.getsize(out_path) / 1024
print(f"Saved: {out_path} ({size_kb:.1f} KB, {len(frames)} frames)")
```

---

## Drawing Primitives

```python
draw = ImageDraw.Draw(frame)

# Circle / oval
draw.ellipse([x1, y1, x2, y2], fill=(R,G,B,A), outline=(R,G,B,A), width=2)

# Rectangle
draw.rectangle([x1, y1, x2, y2], fill=(R,G,B,A), outline=(R,G,B,A), width=2)

# Polygon (triangle, star, etc.)
points = [(x1,y1), (x2,y2), (x3,y3)]
draw.polygon(points, fill=(R,G,B,A), outline=(R,G,B,A))

# Line
draw.line([(x1,y1), (x2,y2)], fill=(R,G,B,A), width=3)

# Text (use JetBrains Mono — available in the sandbox)
try:
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/jetbrains/JetBrainsMonoNerdFont-Regular.ttf", 16
    )
except:
    font = ImageFont.load_default()
draw.text((x, y), "text", font=font, fill=(R,G,B,A), anchor="mm")
```

---

## Animation Patterns

### Easing functions
```python
def ease_out(t): return 1 - (1-t)**3
def ease_in(t):  return t**3
def ease_in_out(t): return 3*t**2 - 2*t**3
def bounce_out(t):
    if t < 1/2.75:    return 7.5625 * t * t
    elif t < 2/2.75:  t -= 1.5/2.75;   return 7.5625*t*t + 0.75
    elif t < 2.5/2.75:t -= 2.25/2.75;  return 7.5625*t*t + 0.9375
    else:              t -= 2.625/2.75; return 7.5625*t*t + 0.984375
```

### Pulse / heartbeat
```python
scale = 0.85 + 0.3 * abs(math.sin(t * math.pi * 2))
r = int(20 * scale)
draw.ellipse([W//2-r, H//2-r, W//2+r, H//2+r], fill=PINK)
```

### Spin / rotate
```python
base_img = Image.new("RGBA", (W, H), (0,0,0,0))
icon = draw_your_icon(base_img)
rotated = icon.rotate(t * 360, resample=Image.BICUBIC, expand=False)
frame.paste(rotated, (0, 0), rotated)
```

### Fade in / out
```python
alpha = int(255 * ease_in_out(t))  # fade in
frame.putalpha(alpha)
```

### Particle burst
```python
import random
random.seed(42)
particles = [(random.uniform(0, 360), random.uniform(1, 3)) for _ in range(20)]

for angle_deg, speed in particles:
    angle = math.radians(angle_deg)
    px = W//2 + int(math.cos(angle) * speed * t * W * 0.5)
    py = H//2 + int(math.sin(angle) * speed * t * H * 0.5)
    draw.ellipse([px-3, py-3, px+3, py+3], fill=(255, 200, 100, int(255*(1-t))))
```

---

## Size Optimization (if file is too large)

```python
# Reduce colors (quantize)
frame_palette = frame.convert("P", palette=Image.ADAPTIVE, colors=64)

# Lower FPS
DURATION_MS = 1000 // 8  # 8 FPS

# Smaller canvas — scale down after drawing
frame = frame.resize((64, 64), Image.LANCZOS)

# Remove near-duplicate frames
# Only add frame if it differs from previous by > threshold
```

---

## Using Sensei's Uploaded Image

If Sensei uploads an image to animate:
```python
from PIL import Image

uploaded = Image.open("photo.png").convert("RGBA").resize((128, 128))
# Use as background, apply effects per frame
for i in range(NUM_FRAMES):
    frame = uploaded.copy()
    draw = ImageDraw.Draw(frame)
    # ... overlay animation on top
    frames.append(frame)
```

---

## Rules

- Write output to `OUTPUT_DIR`.
- Use `send_output: "true"` in `run_code`.
- `Pillow` is pre-installed in the sandbox.
- JetBrains Mono Nerd Font is at `/usr/share/fonts/truetype/jetbrains/`.
- Always wrap `ImageFont.truetype` in try/except with `load_default()` fallback.
- Stick to RGBA mode throughout; convert to palette mode only when saving if needed.
- Always check and print the output file size to verify Discord limits.
