---
name: xlsx
description: "Use this skill any time a spreadsheet is the primary input or output: open/read/edit/create .xlsx, .csv, .tsv files; add columns, compute formulas, format cells, chart data, clean messy data. Do NOT trigger for Word documents, HTML reports, or scripts where a spreadsheet isn't the deliverable."
---

# XLSX — Arona's Spreadsheet Skill

## Overview

Use **openpyxl** (Excel) and **pandas** (data manipulation/CSV) via `run_code`. Write output to `OUTPUT_DIR`.

---

## Quick Reference

| Task | Library |
|------|---------|
| Create/edit .xlsx with formatting | openpyxl |
| Data analysis, CSV, DataFrame ops | pandas |
| Read uploaded .xlsx | openpyxl or pandas |

---

## Creating a Formatted Spreadsheet

```python
# spreadsheet.xlsx
import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.styles import numbers as xl_numbers
from openpyxl.chart import BarChart, Reference

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Report"

# ── Styles ────────────────────────────────────────────────────────────
header_fill  = PatternFill("solid", fgColor="4472C4")
alt_fill     = PatternFill("solid", fgColor="D9E1F2")
header_font  = Font(name="Arial", bold=True, color="FFFFFF", size=11)
body_font    = Font(name="Arial", size=10)
center_align = Alignment(horizontal="center", vertical="center")
thin_border  = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin")
)

# ── Headers ───────────────────────────────────────────────────────────
headers = ["Name", "Q1", "Q2", "Q3", "Q4", "Total"]
for col, h in enumerate(headers, start=1):
    cell = ws.cell(row=1, column=col, value=h)
    cell.font   = header_font
    cell.fill   = header_fill
    cell.alignment = center_align
    cell.border = thin_border

# ── Data ──────────────────────────────────────────────────────────────
data = [
    ("Alice", 100, 120, 110, 130),
    ("Bob",   80,  90,  95, 100),
    ("Carol", 150, 160, 170, 180),
]
for row_idx, (name, q1, q2, q3, q4) in enumerate(data, start=2):
    fill = alt_fill if row_idx % 2 == 0 else PatternFill()
    values = [name, q1, q2, q3, q4, None]
    for col, val in enumerate(values, start=1):
        cell = ws.cell(row=row_idx, column=col, value=val)
        cell.font   = body_font
        cell.fill   = fill
        cell.border = thin_border
        if col > 1:
            cell.alignment = center_align
    # SUM formula in Total column
    ws.cell(row=row_idx, column=6).value = f"=SUM(B{row_idx}:E{row_idx})"

# ── Column widths ─────────────────────────────────────────────────────
ws.column_dimensions["A"].width = 15
for col in ["B","C","D","E","F"]:
    ws.column_dimensions[col].width = 10

# ── Chart ─────────────────────────────────────────────────────────────
chart = BarChart()
chart.title  = "Quarterly Results"
chart.y_axis.title = "Value"
chart.x_axis.title = "Person"
data_ref  = Reference(ws, min_col=2, max_col=5, min_row=1, max_row=4)
names_ref = Reference(ws, min_col=1, min_row=2, max_row=4)
chart.add_data(data_ref, titles_from_data=True)
chart.set_categories(names_ref)
chart.shape = 4
ws.add_chart(chart, "H2")

# ── Save ──────────────────────────────────────────────────────────────
out_path = os.path.join(OUTPUT_DIR, "report.xlsx")
wb.save(out_path)
print(f"Saved: {out_path}")
```

---

## Reading an Uploaded File

> **Binary files (xlsx, xls, ods, csv) cannot be parsed directly from the attachment.**
> Download first with `run_shell`, then read with `run_code`.

**Step 1 — Download (run_shell):**
```bash
curl -L -o 'uploaded.xlsx' '<URL from the hint block>'
```

**Step 2 — Read all sheets (run_code):**
```python
# read_all_sheets.py
import pandas as pd

sheets = pd.read_excel("uploaded.xlsx", sheet_name=None)  # None = all sheets as dict
for sheet_name, df in sheets.items():
    print(f"=== {sheet_name} ({df.shape[0]}R x {df.shape[1]}C) ===")
    print(df.head(10).to_string())
    print(df.dtypes)
    print()
```

**Or with openpyxl (when you need formulas, formatting, named ranges):**
```python
# read_openpyxl.py
import openpyxl

wb = openpyxl.load_workbook("uploaded.xlsx", data_only=True)  # data_only=True → evaluated values, not formula strings
for sheet_name in wb.sheetnames:
    ws = wb[sheet_name]
    print(f"=== {sheet_name} ({ws.max_row}R x {ws.max_column}C) ===")
    for row in ws.iter_rows(min_row=1, max_row=5, values_only=True):
        print(row)
```

**CSV:**
```python
import pandas as pd
df = pd.read_csv("uploaded.csv")
print(df.head())
print(df.describe())
```

---

## Data Cleaning with Pandas

```python
# clean.py
import os
import pandas as pd

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

df = pd.read_excel("messy.xlsx")

# Drop empty rows/cols
df.dropna(how="all", inplace=True)
df.dropna(axis=1, how="all", inplace=True)

# Strip whitespace from string columns
for col in df.select_dtypes("object"):
    df[col] = df[col].str.strip()

# Fill numeric NaN with 0
df.fillna({"Revenue": 0, "Quantity": 0}, inplace=True)

# Save cleaned
df.to_excel(os.path.join(OUTPUT_DIR, "cleaned.xlsx"), index=False)
print("Cleaned.")
```

---

## Number Formats (openpyxl)

```python
from openpyxl.styles import numbers

cell.number_format = "#,##0"          # Integer with comma
cell.number_format = "#,##0.00"       # 2 decimal places
cell.number_format = "$#,##0;($#,##0);-"  # Currency, negatives in parens, zero as dash
cell.number_format = "0.0%"           # Percentage
cell.number_format = "0.0x"          # Multiples
```

---

## Rules

- Always write output to `OUTPUT_DIR`.
- `openpyxl` and `pandas` are pre-installed in the sandbox.
- Zero formula errors (`#REF!`, `#DIV/0!`, etc.) — verify all references before saving.
- Use `Arial` as default font unless otherwise specified.
- Uploaded files are in the working directory — reference by filename only.