#!/usr/bin/env python3
"""Load ekranı: anasayfa.py'de F9 ile kaydedilen programları listeler.

Solda kayıtlı programlar, sağda seçili programın ölçüleri ve büküm tablosu.
Butonlar: Load (op ekranına yükle), Edit OBJ (anasayfa'da aç), Delete (sil),
Refresh (listeyi yenile), Close.

op.py içinden açılır; tek başına da çalışır:
    python load.py
"""
import os
import json
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from anasayfa1 import PROG_DIR, load_saved, fmt, delete_program, export_programs_excel, programs_signature, temiz_ad
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

        # Program adına veya kod dizisine göre filtreleme
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *_: self.filter_list())

        # ---- sol: liste
        left = tk.Frame(self, bg=BG)
        left.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        tk.Label(left, text='Ara (program / kod):', bg=BG).pack(anchor='w')
        tk.Entry(left, textvariable=self.search_var).pack(fill='x', pady=(0, 5))
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
                ("Excel Export", self.do_export, "#dff0f8", "black"),
                ("Eski Kayıtları Al", self.import_old_records, "#dff0f8", "black"),
                ("Close", self.destroy, "#dff0f8", "black"))):
            bar.columnconfigure(i, weight=1)
            tk.Button(bar, text=name, command=cmd, width=11, height=2,
                      font=("Segoe UI", 11), bg=bg, fg=fg
                      ).grid(row=0, column=i, padx=4, sticky="ew")

        self.bind("<Escape>", lambda e: self.destroy())
        self.refresh(select=current)
        self.tree.focus_set()
        self._sig = programs_signature()
        self.after(500, self._auto_refresh)      # başka pencerede kaydedilen program hemen listeye gelir

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

    def _auto_refresh(self):
        """Açık LOAD ekranını yeni kayıtlarla otomatik eşitle (yalnızca programs/ değişince)."""
        if not self.winfo_exists():
            return
        sig = programs_signature()
        if sig != self._sig:
            self._sig = sig
            self.refresh()
        self.after(500, self._auto_refresh)

    def refresh(self, select=None):
        """programs/ klasörünü yeniden tarar. Açılışta (seçili program yoksa) hiçbir program seçili gelmez."""
        keep = select or self.selected()
        self.saved = load_saved()
        self.tree.delete(*self.tree.get_children())
        self.filter_list()
        if keep in self.saved:
            self.tree.selection_set(keep)
            self.tree.focus(keep)
            self.tree.see(keep)
        if self.saved:
            self.say(f"{len(self.saved)} kayıtlı program.")
        else:
            self.show_details()
            self.say("Henüz kayıtlı program yok. anasayfa'da F9 ile kaydedin.", RED)

    def filter_list(self):
        if not hasattr(self, 'tree'):
            return
        keep = self.selected()
        query = self.search_var.get().strip().casefold()
        self.tree.delete(*self.tree.get_children())
        for n in sorted(self.saved):
            d = self.saved[n]
            code = str(d.get('inputs', {}).get('code', ''))
            if query and query not in n.casefold() and query not in code.casefold():
                continue
            self.tree.insert('', 'end', iid=n, values=(
                n, fmt(d.get('result', {}).get('mat_len', 0)), code))
        visible = self.tree.get_children()
        if visible and (keep in visible or query):
            target = keep if keep in visible else visible[0]
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
        else:
            self.show_details()

    def import_old_records(self):
        """Önceki proje klasöründeki JSON kayıtlarını kayıpsız kopyala."""
        folder = filedialog.askdirectory(parent=self, title="Eski proje klasörünü veya programs klasörünü seçin")
        if not folder:
            return
        source = os.path.join(folder, "programs") if os.path.isdir(os.path.join(folder, "programs")) else folder
        os.makedirs(PROG_DIR, exist_ok=True)
        imported = skipped = invalid = 0
        for filename in os.listdir(source):
            if not filename.lower().endswith(".json") or filename.lower().endswith(".obj.json"):
                continue
            oldpath = os.path.join(source, filename)
            if not os.path.isfile(oldpath):
                continue
            try:
                with open(oldpath, encoding="utf-8") as fh:
                    record = json.load(fh)
                if not isinstance(record, dict) or not all(k in record for k in ("name", "inputs", "result")):
                    invalid += 1
                    continue
                name = record["name"]
                name = temiz_ad(name) if isinstance(name, str) else name
                if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in '/\\'):
                    invalid += 1
                    continue
                target = os.path.join(PROG_DIR, name + ".json")
                if os.path.exists(target):
                    skipped += 1
                    continue
                shutil.copy2(oldpath, target)
                oldobj = os.path.join(source, record["name"] + ".obj.json")
                newobj = os.path.join(PROG_DIR, name + ".obj.json")
                if os.path.isfile(oldobj) and not os.path.exists(newobj):
                    shutil.copy2(oldobj, newobj)
                imported += 1
            except (OSError, ValueError, TypeError, KeyError):
                invalid += 1
        self.refresh()
        messagebox.showinfo("Eski Kayıtlar", f"Aktarılan: {imported}\nZaten mevcut (üzerine yazılmadı): {skipped}\nGeçersiz/uyumsuz: {invalid}\n\nOP ve Edit OBJ listesini yenilemek için programı yeniden başlatın.", parent=self)

    def do_export(self):
        filename = filedialog.asksaveasfilename(parent=self, title="Programları Excel’e aktar",
                     defaultextension='.xlsx', filetypes=[('Excel dosyası', '*.xlsx')],
                     initialfile='Kaydedilen_Programlar.xlsx')
        if not filename:
            return
        try:
            count = export_programs_excel(filename)
            self.say(f'{count} program Excel dosyasına aktarıldı.')
        except (OSError, PermissionError, ValueError, ImportError) as exc:
            messagebox.showerror('Excel Export', str(exc), parent=self)

    def show_details(self):
        self.bends.delete(*self.bends.get_children())
        name = self.selected()
        if not name or name not in self.saved:
            for var in self.v.values():
                var.set("")
            return
        d = self.saved[name]
        i, r = d["inputs"], d["result"]
        old = (d.get("saved_result") or {}).get("mat_len")
        if old is not None and abs(float(old) - float(r["mat_len"])) > 0.05:
            self.say(f"{name}: güncel formülle yeniden hesaplandı (kayıttaki Mat.Len {fmt(old)} -> {fmt(r['mat_len'])}).")
        else:
            self.say(f"{len(self.saved)} kayıtlı program.")
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
        try:
            from editobj import open_edit_obj
            open_edit_obj(self, name)
            self.say(f"{name} Edit OBJ ekranında açıldı.")
        except Exception as e:
            self.say(f"Edit OBJ açılamadı: {e}", RED)

    def do_delete(self):
        name = self.selected()
        if not name:
            self.say("Önce listeden bir program seçin.", RED)
            return
        if not messagebox.askyesno("Sil", f"{name} silinsin mi?", parent=self):
            return
        try:
            delete_program(name)
        except (OSError, PermissionError, ImportError) as e:
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