"""
bond_editor.py — chạy: python bond_editor.py
GUI editor cho database/affection.db — không cần cài thêm gì.
"""

import sqlite3
import os
import tkinter as tk
from tkinter import filedialog, messagebox

DB_PATH = "database/affection.db"

RANKS = [
    (  0,  10, "Stranger",      "#666677"),
    ( 10,  25, "Acquaintance",  "#888899"),
    ( 25,  45, "Friend",        "#7b93ff"),
    ( 45,  60, "Close Friend",  "#52e8c0"),
    ( 60,  75, "Trusted",       "#a8d8a8"),
    ( 75,  90, "Bonded",        "#ffb07b"),
    ( 90, 100, "Irreplaceable", "#ff7eb3"),
    (100, 101, "Soulbound",     "#ffe07b"),
]

def get_rank(bond):
    for lo, hi, name, color in RANKS:
        if lo <= bond < hi:
            return name, color
    return "Soulbound", "#ffe07b"


class BondEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Bond Editor — affection.db")
        self.configure(bg="#0d0e12")
        self.geometry("620x480")
        self.resizable(True, True)
        self.db_path = None
        self._entries = {}

        self._build_ui()
        self._try_load_default()

    def _build_ui(self):
        bar = tk.Frame(self, bg="#0d0e12", pady=10)
        bar.pack(fill="x", padx=16)

        tk.Label(bar, text="BOND EDITOR", bg="#0d0e12", fg="#7b93ff",
                 font=("Courier", 14, "bold")).pack(side="left")

        btn_frame = tk.Frame(bar, bg="#0d0e12")
        btn_frame.pack(side="right")
        self._btn(btn_frame, "Open DB", self._open_file, "#222").pack(side="left", padx=(0, 8))
        self._btn(btn_frame, "Save", self._save, "#7b93ff", fg="#0d0e12").pack(side="left")

        self.status_var = tk.StringVar(value="No file loaded")
        tk.Label(self, textvariable=self.status_var, bg="#0d0e12", fg="#444",
                 font=("Courier", 9)).pack(padx=16, anchor="w")

        tk.Frame(self, bg="#1a1c24", height=1).pack(fill="x", padx=16, pady=(4, 0))

        hdr = tk.Frame(self, bg="#0d0e12")
        hdr.pack(fill="x", padx=16, pady=(8, 2))
        for text, w in [("USER ID", 22), ("BOND", 9), ("RANK", 18)]:
            tk.Label(hdr, text=text, bg="#0d0e12", fg="#333",
                     font=("Courier", 9), width=w, anchor="w").pack(side="left")

        tk.Frame(self, bg="#1a1c24", height=1).pack(fill="x", padx=16)

        outer = tk.Frame(self, bg="#0d0e12")
        outer.pack(fill="both", expand=True, padx=16, pady=4)

        self.canvas = tk.Canvas(outer, bg="#0d0e12", highlightthickness=0)
        scroll = tk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.rows_frame = tk.Frame(self.canvas, bg="#0d0e12")
        self._cw = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")

        self.rows_frame.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._cw, width=e.width))
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))

    def _btn(self, parent, text, cmd, bg, fg="#d4d8e8"):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, relief="flat",
                         font=("Courier", 10, "bold"),
                         padx=14, pady=5, cursor="hand2",
                         activebackground=bg, activeforeground=fg)

    def _try_load_default(self):
        if os.path.exists(DB_PATH):
            self._load(DB_PATH)

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Open affection.db",
            filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")])
        if path:
            self._load(path)

    def _load(self, path):
        try:
            con = sqlite3.connect(path)
            rows = con.execute("SELECT user_id, bond FROM bond ORDER BY bond DESC").fetchall()
            con.close()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open DB:\n{e}")
            return

        self.db_path = path
        self._entries.clear()
        for w in self.rows_frame.winfo_children():
            w.destroy()
        for uid, bond in rows:
            self._add_row(uid, bond)

        fname = os.path.basename(path)
        self.status_var.set(f"{fname}  ·  {len(rows)} users")

    def _add_row(self, uid, bond):
        rank_name, rank_color = get_rank(bond)
        frame = tk.Frame(self.rows_frame, bg="#0d0e12")
        frame.pack(fill="x", pady=1)

        tk.Label(frame, text=str(uid), bg="#0d0e12", fg="#444",
                 font=("Courier", 11), width=22, anchor="w").pack(side="left")

        var = tk.StringVar(value=f"{bond:.1f}")
        self._entries[uid] = var

        tk.Entry(frame, textvariable=var, bg="#151720", fg="#d4d8e8",
                 insertbackground="#7b93ff", relief="flat",
                 font=("Courier", 11), width=8,
                 highlightthickness=1, highlightcolor="#7b93ff",
                 highlightbackground="#222530").pack(side="left", padx=(0, 14))

        rank_lbl = tk.Label(frame, text=rank_name, bg="#0d0e12",
                            fg=rank_color, font=("Courier", 10))
        rank_lbl.pack(side="left")

        def on_change(*_, lbl=rank_lbl, v=var):
            try:
                val = max(0.0, min(100.0, float(v.get())))
                n, c = get_rank(val)
                lbl.config(text=n, fg=c)
            except ValueError:
                pass
        var.trace_add("write", on_change)

        def enter(e, f=frame):
            f.config(bg="#111320")
            for c in f.winfo_children():
                if isinstance(c, tk.Label): c.config(bg="#111320")
        def leave(e, f=frame):
            f.config(bg="#0d0e12")
            for c in f.winfo_children():
                if isinstance(c, tk.Label): c.config(bg="#0d0e12")
        frame.bind("<Enter>", enter)
        frame.bind("<Leave>", leave)

    def _save(self):
        if not self.db_path:
            messagebox.showwarning("No file", "Open a DB file first.")
            return
        try:
            con = sqlite3.connect(self.db_path)
            for uid, var in self._entries.items():
                try:
                    val = max(0.0, min(100.0, float(var.get())))
                except ValueError:
                    continue
                con.execute("""
                    INSERT INTO bond (user_id, bond) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET bond = excluded.bond
                """, (uid, val))
            con.commit()
            con.close()
            self.status_var.set("Saved ✓  —  restart the bot to reload cache")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))


if __name__ == "__main__":
    BondEditor().mainloop()
