# Arona SKILL.md — Adaptation Summary

## Files REMOVED (không phù hợp với Arona)

| File | Lý do |
|------|-------|
| `public/frontend-design` | Dành cho React/HTML artifacts của Claude.ai — Arona là Discord bot |
| `public/product-self-knowledge` | Tài liệu sản phẩm Anthropic/Claude — không liên quan |
| `examples/benepass-reimbursement` | Tool riêng của một công ty, dùng browser automation |
| `examples/brand-guidelines` | Brand của Anthropic — không liên quan |
| `examples/mcp-builder` | Xây MCP server — không liên quan với Arona |
| `examples/theme-factory` | Theme cho artifacts của Claude.ai |
| `examples/web-artifacts-builder` | React artifacts cho Claude.ai |
| `examples/skill-creator` | Dùng eval scripts và Claude-specific tooling |
| `examples/algorithmic-art` | p5.js interactive HTML viewer — không render được trên Discord |

## Files KEPT & ADAPTED (8 files)

| File | Thay đổi chính |
|------|----------------|
| `docx` | Rewrite: bỏ docx-js/Node/pandoc → dùng `python-docx` qua `run_code` |
| `pdf` | Adapt: bỏ tool refs → `run_code` với pypdf + reportlab |
| `pptx` | Rewrite: bỏ pptxgenjs/Node → `python-pptx` qua `run_code` |
| `xlsx` | Adapt: bỏ tool refs → `run_code` với openpyxl + pandas |
| `canvas-design` | Adapt: bỏ `./canvas-fonts` dir → dùng JetBrains Mono có sẵn; dùng matplotlib/Pillow |
| `doc-coauthoring` | Minor: bỏ artifact/file tool refs; thêm note về docx/pdf skill |
| `internal-comms` | Minor: giữ nguyên templates, bỏ tool-specific refs |
| `slack-gif-creator` | Major: đổi thành `discord-gif-creator`; bỏ custom modules (core.gif_builder v.v.); rewrite sang pure Pillow; cập nhật size limits cho Discord |

## Dockerfile Changes

**Added to pip install (step 7):**
```
python-docx       ← docx skill
pypdf             ← pdf skill (read/merge/split)
reportlab         ← pdf skill (create new PDFs)
python-pptx       ← pptx skill
openpyxl          ← xlsx skill
xlsxwriter        ← xlsx skill (alternative writer)
scipy             ← general science/math
sympy             ← symbolic math
imageio           ← GIF/image I/O utilities
```

## Key Pattern in All Adapted SKILLs

```
TRƯỚC (Claude):           SAU (Arona):
bash_tool              →  run_code(action="run_shell")
create_file            →  Python code viết file qua run_code(action="run_code")
present_files          →  send_output="true" trong run_code
/mnt/user-data/outputs →  OUTPUT_DIR (env var tự động set)
Artifacts (HTML/React) →  File đính kèm Discord
```
