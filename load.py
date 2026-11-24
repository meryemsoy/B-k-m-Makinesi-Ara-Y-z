#!/usr/bin/env python3
"""Load ekranı: anasayfa.py'de F9 ile kaydedilen programları listeler.

Solda kayıtlı programlar, sağda seçili programın ölçüleri ve büküm tablosu.
Butonlar: Load (op ekranına yükle), Edit OBJ (anasayfa'da aç), Delete (sil),
Refresh (listeyi yenile), Close.

op.py içinden açılır; tek başına da çalışır:
    python load.py
"""
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from anasayfa import PROG_DIR, load_saved, fmt
from ekran import olcek_uygula, tam_ekran

BG, CYAN = "white", "#7fc8e8"
RED, BLUE = "#e01010", "#1a1acb"
HERE = os.path.dirname(os.path.abspath(__file__))


class LoadScreen(tk.Toplevel):

    def __init__(self, master, on_load=None, on_delete=None, current=None):
        """on_load(ad)   : Load'a basılınca çağrılır.
        on_delete(ad) : bir kayıt silinince çağrılır.
        current       : listede başta seçili gelecek program adı."""
        super().__init__(master)
        self.k = olcek_uygula(master)
        self.title("Load - Kayıtlı Programlar")
        self.configure(bg=BG, padx=12, pady=10)
        self.on_load, self.on_delete = on_load, on_delete
        self.saved = {}
        self.v = {k: tk.StringVar() for k in
                  ("matlen", "t", "w", "tool", "r", "mat", "code")}

        # Pencere büyüyünce içerik de büyüsün
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        st = ttk.Style(self)
        st.configure("Treeview", rowheight=int(24 * self.k),
                     font=("Segoe UI", 11))
        st.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"))

        tk.Label(self, text="Kayıtlı Programlar", bg=CYAN, relief="ridge", bd=2,
                 font=("Segoe UI", 13, "bold"), width=34
                 ).grid(row=0, column=0, sticky="ew")
        tk.Label(self, text="Program Bilgisi", bg=CYAN, relief="ridge", bd=2,
                 font=("Segoe UI", 13, "bold"), width=40
                 ).grid(row=0, column=1, sticky="ew", padx=(14, 0))

        # ---- sol: liste
        left = tk.Frame(self, bg=BG)
        left.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.tree = ttk.Treeview(left, columns=("name", "len", "code"),
                                 show="headings", height=16, selectmode="browse")
        for col, txt, w in (("name", "Program", 170), ("len", "Mat.Len", 90),
                            ("code", "Kod", 60)):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor="center", stretch=True)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_details())
        self.tree.bind("<Double-Button-1>", lambda e: self.do_load())
        self.tree.bind("<Return>", lambda e: self.do_load())

        # ---- sağ: bilgi + büküm tablosu
        right = tk.Frame(self, bg=BG)
        right.grid(row=1, column=1, sticky="nsew", padx=(14, 0), pady=(6, 0))
        info = tk.Frame(right, bg=BG, relief="groove", bd=2)
        info.pack(anchor="w", fill="x")
        for c in (1, 3):
            info.columnconfigure(c, weight=1)
        self.kv(info, 0, 0, "Mat.Len", "matlen")
        self.kv(info, 0, 2, "Thickness", "t")
        self.kv(info, 1, 0, "Width", "w")
        self.kv(info, 1, 2, "Tool", "tool")
        self.kv(info, 2, 0, "Radius", "r")
        self.kv(info, 2, 2, "Mat.", "mat")
        self.kv(info, 3, 0, "Code", "code")

        self.bends = ttk.Treeview(right, columns=("no", "ss", "ang"),
                                  show="headings", height=9, selectmode="none")
        for col, txt, w in (("no", "NO.", 60), ("ss", "Side-Stop", 150),
                            ("ang", "Angle", 150)):
            self.bends.heading(col, text=txt)
            self.bends.column(col, width=w, anchor="center", stretch=True)
        self.bends.pack(anchor="w", pady=(8, 0), fill="both", expand=True)

        self.status = tk.Label(self, text="", bg=BG, fg=BLUE, anchor="w",
                               font=("Segoe UI", 11, "bold"))
        self.status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # ---- butonlar
        bar = tk.Frame(self, bg=BG)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for i, (name, cmd, bg, fg) in enumerate((
                ("Load", self.do_load, RED, "white"),
                ("Edit OBJ", self.do_edit, "#dff0f8", "black"),
                ("Delete", self.do_delete, "#dff0f8", "black"),
                ("Refresh", self.refresh, "#dff0f8", "black"),
                ("Close", self.destroy, "#dff0f8", "black"))):
            bar.columnconfigure(i, weight=1)
            tk.Button(bar, text=name, command=cmd, width=11, height=2,
                      font=("Segoe UI", 11), bg=bg, fg=fg
                      ).grid(row=0, column=i, padx=4, sticky="ew")

        self.bind("<Escape>", lambda e: self.destroy())
        self.refresh(select=current)
        self.tree.focus_set()

        tam_ekran(self)              # pencere büyür; F11 = gerçek tam ekran

        # pencere diğerlerinin arkasında kalmasın
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # ------------------------------------------------------------
    def kv(self, parent, row, col, label, key):
        tk.Label(parent, text=label, bg=BG, font=("Segoe UI", 12), anchor="w",
                 width=10).grid(row=row, column=col, sticky="w", padx=(6, 0))
        tk.Label(parent, textvariable=self.v[key], bg=BG, fg=RED,
                 font=("Segoe UI", 12), anchor="e", width=8
                 ).grid(row=row, column=col + 1, sticky="e")

    def say(self, text, color=BLUE):
        self.status.config(text=text, fg=color)

    def selected(self):
        s = self.tree.selection()
        return s[0] if s else None

    def refresh(self, select=None):
        """programs/ klasörünü yeniden tarar."""
        keep = select or self.selected()
        self.saved = load_saved()
        self.tree.delete(*self.tree.get_children())
        for n in sorted(self.saved):
            d = self.saved[n]
            self.tree.insert("", "end", iid=n, values=(
                n, fmt(d["result"]["mat_len"]), d["inputs"].get("code", "")))
        if self.saved:
            target = keep if keep in self.saved else sorted(self.saved)[0]
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
            self.say(f"{len(self.saved)} kayıtlı program.")
        else:
            self.show_details()
            self.say("Henüz kayıtlı program yok. anasayfa'da F9 ile kaydedin.", RED)

    def show_details(self):
        self.bends.delete(*self.bends.get_children())
        name = self.selected()
        if not name or name not in self.saved:
            for var in self.v.values():
                var.set("")
            return
        d = self.saved[name]
        i, r = d["inputs"], d["result"]
        self.v["matlen"].set(fmt(r["mat_len"]))
        for k in ("t", "w", "tool", "r"):
            self.v[k].set(fmt(i[k]))
        self.v["mat"].set(str(i["mat"]))
        self.v["code"].set(str(i.get("code", "")))
        for n, (pos, ang) in enumerate(r["bends"], 1):
            self.bends.insert("", "end", values=(n, fmt(pos), fmt(ang)))

    # ------------------------------------------------------------
    def do_load(self):
        name = self.selected()
        if not name:
            self.say("Önce listeden bir program seçin.", RED)
            return
        if self.on_load:
            self.on_load(name)
        self.destroy()

    def do_edit(self):
        name = self.selected()
        if not name:
            self.say("Önce listeden bir program seçin.", RED)
            return
        dosya = os.path.join(HERE, "anasayfa.py")
        if not os.path.exists(dosya):
            self.say("anasayfa.py dosyası bulunamadı!", RED)
            return
        try:
            subprocess.Popen([sys.executable, dosya, name], cwd=HERE)
            self.say(f"{name} anasayfa'da açıldı.")
        except Exception as e:
            self.say(f"Program açılamadı: {e}", RED)

    def do_delete(self):
        name = self.selected()
        if not name:
            self.say("Önce listeden bir program seçin.", RED)
            return
        if not messagebox.askyesno("Sil", f"{name} silinsin mi?", parent=self):
            return
        try:
            os.remove(os.path.join(PROG_DIR, name + ".json"))
        except OSError as e:
            self.say(f"Silinemedi: {e}", RED)
            return
        if self.on_delete:
            self.on_delete(name)
        self.refresh()
        self.say(f"{name} silindi.")


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    olcek_uygula(root)
    win = LoadScreen(root, on_load=lambda n: print("Yüklenecek:", n))
    win.bind("<Destroy>", lambda e: root.quit() if e.widget is win else None)
    root.mainloop()