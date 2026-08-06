---
name: frontend-design
description: Create distinctive, production-grade frontend interfaces with high design quality. Use when Sensei asks to build websites, landing pages, dashboards, HTML/CSS/JS components, web UIs, or anything visual for the web. Output is a .html or .jsx file sent to Discord — a live preview link is automatically appended to the message.
---

This skill guides creation of distinctive, production-grade frontend interfaces that avoid generic "AI slop" aesthetics. Implement real working code with exceptional attention to aesthetic details and creative choices.

## Delivery Model (Arona-specific)

Output is a **`.html`** or **`.jsx`** file sent to Discord via `create_files` → `send_files`. A live preview link is automatically appended to the message — Sensei can click it to view instantly in browser.

**CRITICAL: Everything must live in a single file.** The preview only supports one file — no separate CSS, JS, or component files. All styles, scripts, and markup go inline into the one output file.

**Choose format based on complexity:**
- **`.html`** — vanilla HTML/CSS/JS. Best for landing pages, static layouts, simple interactivity.
- **`.jsx`** — React component with hooks. Best for dashboards, interactive UIs, stateful apps.

### Delivery flow
```
1. create_files([{"filename": "page.html", "content": "<full html here>"}])
   → returns [{"file_id": "...", "filename": "page.html"}]

2. send_files(file_refs=["<file_id>"])
   → uploads to Discord, preview link auto-appended
```

**Never use `run_code` just to write a text file to disk — use `create_files` instead.**
Only use `run_code` if the page requires server-side generation (e.g., matplotlib chart embedded as base64 data URI).

---

## Design Thinking

Before coding, commit to a clear aesthetic direction:

- **Purpose**: What problem does this interface solve? Who uses it?
- **Tone**: Choose deliberately — brutally minimal, maximalist, retro-futuristic, organic, luxury, playful, editorial, brutalist, art deco, industrial. Execute it with precision, not timidity.
- **Constraints**: Format choice, self-contained delivery, performance.
- **Differentiation**: What's the one thing someone will remember?

Then implement working code that is production-grade, visually cohesive, and meticulously refined in every detail.

---

## Frontend Aesthetics Guidelines

**Default to dark mode.** Dark backgrounds give accent colors more room to breathe, age better, and are easier on the eyes.

- **Typography**: Pick fonts that have character. Avoid Inter, Roboto, Arial, Space Grotesk — these are invisible choices. Load from Google Fonts. Pair a distinctive display font with a clean body font.
- **Color**: Define a palette in `:root` variables. One dominant background, one surface, one sharp accent. Avoid evenly-distributed palettes — they look indecisive.
- **Motion**: One well-timed page load with staggered reveals beats scattered micro-interactions. CSS `animation-delay` is enough. Hover states should feel intentional, not reflexive.
- **Layout**: Avoid predictable card grids. Use asymmetry, overlap, generous whitespace, or controlled density — whichever serves the content.
- **Atmosphere**: Solid color backgrounds are a missed opportunity. Use gradient meshes, subtle noise textures, geometric patterns, or layered transparencies to add depth.

NEVER use purple gradients on white, cookie-cutter card layouts, or fonts that disappear into the background. No two designs should look the same.

---

## Allowed CDN Imports

### For `.html`
```html
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=DM+Sans:wght@300;400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
```

### For `.jsx`
React 18 + hooks. Tailwind utility classes pre-loaded. Available: `recharts`, `lucide-react`, `lodash`, `d3`, `mathjs`.

---

## Quality Checklist

- [ ] Dark mode by default unless context explicitly calls for light
- [ ] Fonts load from Google Fonts CDN — no system fonts
- [ ] **Single file only** — no separate CSS/JS/component files, everything inline
- [ ] **HTML**: All styles and scripts inline or from CDN. Self-contained, works from `file://`
- [ ] **JSX**: All components in one file. Only available libraries. Default export, no required props
- [ ] No broken images — use CSS shapes, gradients, or data URIs
- [ ] Responsive — `viewport` meta tag, fluid widths
- [ ] Animations feel deliberate, not scattered
- [ ] Color palette defined in `:root` variables (HTML) or Tailwind classes (JSX)