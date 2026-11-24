#!/usr/bin/env python3
"""OP Mode (JOG / Angle Measurement) ekranı.

Çalıştırma:
    python op.py

Klavye:
    Yukarı/Aşağı : Büküm satırı seç
    Sol/Sağ      : Alt düğmeler arasında gezin (seçili düğme kırmızı)
    Enter/Boşluk : Seçili alt düğmeyi çalıştır
    F1           : Operating Mode değiştir (JOG / AUTO / MANUAL)
    F2           : Bending Mode değiştir (Linear Measurement / Angle Measurement / Manual / Depth)
    F3           : Side-Stop ve X sıfırla
    F4           : Tool ve Y sıfırla
    F5           : Yan dayamayı seçili değere götür (AUTO modda sıradakine geçer)
    E            : Emergency Stop aç/kapat
    F11          : Gerçek tam ekran aç/kapat

Alt düğmeler:
    OP Mode / Bending Mode : mod değiştirir
    Edit OBJ               : seçili programı anasayfa.py'de açar
    Load                   : anasayfa.py'de F9 ile kaydedilen programları listeler
    Program>               : büküm tablosunu düzenler
    Param>                 : PLC ayarları
    Monitor                : canlı durum penceresi

Not: Bu ekran simülasyondur, makineye komut göndermez.
Stroke* yalnızca idealize geometrik tahmindir, gerçek makine strok değeri değildir.
"""

import tkinter as tk
import subprocess
import sys
import os
import math
from tkinter import ttk, simpledialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from anasayfa1 import PRESETS, calc, fmt, load_saved, parse, programs_signature
from load1 import LoadScreen
from ekran import olcek_uygula, tam_ekran
from zhesap import z_hesapla, z_bukumler, k_geri_hesapla
from editobj import open_edit_obj, open_program
# Edit OBJ düğmesi:

try:
    import plc as plc_cfg          # PLC okuyucu (isteğe bağlı)
except ImportError:
    plc_cfg = None


BG, CYAN, PANEL = "white", "#7fc8e8", "#1d74c6"
RED, BLUE, GRAYBOX = "#e01010", "#1a1acb", "#ececec"

# Seçilebilir modlar
OP_MODES = ["JOG", "AUTO", "MANUAL"]
BEND_MODES = ["Linear Measurement", "Angle Measurement", "Manual", "Depth"]   # makine ekranı: Linear Measurement


# Fotoğraftan alınan program: (yan dayama, açı) satırları
PROGRAMS = {
    "2410955": dict(
        mat_len=1447.4,
        t=10,
        w=80,
        tool=63,
        r=5,
        mat=1,
        bends=[
            (1333.0, -90.0),
            (1212.3, -90.0),
            (248.2, -45.0),
            (189.6, 45.0)
        ]
    ),
}


def preset_program(name):
    """PRESETS kaydından tablo üretir (tek haneli kodlar için calc kullanılır)."""
    p = PRESETS[name]
    code = str(p["code"])
    outer = p["dim"] in (0, 2)
    mode = "len" if p["dim"] in (0, 1) else "angle"
    d = dict(A=p["a"], L=p["l"], H=p["h"], C=p["c"], W=abs(p["ang"]))
    try:
        st = calc(code, p["t"], p["r"], outer, mode, d, ac=p.get("ac", 1))
        mat_len, bends = st["mat"], list(st["bends"])
    except ValueError:
        # çok haneli kod vb.: kayıtlı/ekran değerini kullan, büküm tablosu boş
        mat_len, bends = p.get("ref", 0.0), []
    return dict(mat_len=mat_len, t=p["t"], w=p["w"], tool=p["tool"],
                r=p["r"], mat=p["mat"], bends=bends)

def theoretical_stroke(angle_deg, v_opening):
    """Yalnızca idealize V-kalıp geometrik tahmini; makine Y komutu DEĞİLDİR."""
    if v_opening is None:
        return None
    angle = abs(float(angle_deg))
    if not (0 < angle < 180) or v_opening <= 0:
        return None
    return (v_opening / 2.0) * math.tan(math.radians(angle) / 2.0)


class OpScreen(tk.Toplevel):

    def __init__(self, master, name="", program=None):
        """program verilirse ana arayüzden gelen program kullanılır."""

        super().__init__(master)
        self.k = olcek_uygula(master)          # ekran boyutuna göre ölçek

        self.title("OP Mode")
        self.configure(bg=BG, padx=10, pady=8)

        # Pencere büyüyünce içerik de büyüsün
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        st = ttk.Style(self)
        st.configure("Treeview", rowheight=int(24 * self.k),
                     font=("Segoe UI", 11))
        st.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"))

        self.progs = {}

        if program is not None:
            self.progs[name] = program

        self.v_opening = None  # V açıklığı kullanıcıdan alınır; Tool ID ile karıştırılmaz
        self.estop = False
        self.sel = 0
        self.cur = None
        self.plc = None
        self._plc_status = None
        self._poll_id = None
        self.btns = []
        self.btn_i = 0

        self.v = {
            k: tk.StringVar()
            for k in (
                "prog", "matlen", "t", "w", "tool", "r", "mat",
                "toolpos", "side", "ang", "corr", "opmode", "bendmode",
                "x", "y"
            )
        }

        self.v["toolpos"].set("28,8")
        self.v["side"].set("86,0")
        self.v["ang"].set("-0,1")
        self.v["corr"].set("0,0")
        self.v["x"].set("0,0")
        self.v["y"].set("0,0")
        self.v["opmode"].set(OP_MODES[0])
        self.v["bendmode"].set(BEND_MODES[0])

        self.build_header()
        self.reload_saved()          # combobox (build_header) oluştuktan sonra çağrılmalı
        self.build_left()
        self.build_right()
        self.build_buttons()

        self.bind("<Up>", lambda e: self.select(self.sel - 1))
        self.bind("<Down>", lambda e: self.select(self.sel + 1))
        self.bind("<Return>", lambda e: self.run_btn())
        self.bind("<KP_Enter>", lambda e: self.run_btn())
        self.bind("<space>", lambda e: self.run_btn())
        self.bind("<F1>", lambda e: self.toggle_opmode())
        self.bind("<F2>", lambda e: self.toggle_bendmode())
        self.bind("<F5>", lambda e: self.move_side_stop())
        self.bind("<F3>", lambda e: self.zero_side())
        self.bind("<F4>", lambda e: self.zero_tool())
        self.bind("<e>", lambda e: self.toggle_estop())
        self.bind("<Left>", lambda e: self.move_btn(-1))
        self.bind("<Right>", lambda e: self.move_btn(1))

        if name and name in self.progs:
            self.load_program(name)
        else:
            self.clear_program()
        self._sync_programs()
        self.focus_set()

        self.start_plc()
        tam_ekran(self)              # pencere büyür; F11 = gerçek tam ekran

    # ------------------------------------------------------------
    # Düzen
    # ------------------------------------------------------------

    def kv(self, parent, row, col, label, key):

        tk.Label(
            parent, text=label, bg=BG, font=("Segoe UI", 12),
            anchor="w", width=10
        ).grid(row=row, column=col, sticky="w", padx=(6, 0))

        tk.Label(
            parent, textvariable=self.v[key], bg=BG, fg=RED,
            font=("Segoe UI", 12), anchor="e", width=8
        ).grid(row=row, column=col + 1, sticky="e")

    def build_header(self):

        hdr = tk.Frame(self, bg=BG)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.columnconfigure(1, weight=1)

        logo = tk.Canvas(
            hdr,
            width=int(190 * self.k),
            height=int(110 * self.k),
            bg=BG,
            highlightthickness=0
        )
        logo.grid(row=0, column=0, rowspan=2, sticky="w")

        logo.create_rectangle(8, 8, 80, 50, fill="#e01010", outline="")

        for dx in range(0, 70, 14):
            logo.create_polygon(
                40 + dx, 82, 70 + dx, 10, 82 + dx, 10, 52 + dx, 82,
                fill="#2a2ab0", outline=""
            )

        logo.create_oval(105, 40, 165, 100, fill="#e01010", outline="")

        logo.create_text(
            8, 98, text="V1.05B", anchor="w", font=("Segoe UI", 12)
        )

        logo.scale("all", 0, 0, self.k, self.k)

        top = tk.Frame(hdr, bg=BG)
        top.grid(row=0, column=1, sticky="w", padx=20)

        for i, (lab, key) in enumerate((
            ("Operating Mode :", "opmode"),
            ("Bending Mode :", "bendmode")
        )):
            tk.Label(
                top, text=lab, bg=BG, font=("Segoe UI", 13)
            ).grid(row=0, column=2 * i, padx=(0, 4))

            tk.Label(
                top, textvariable=self.v[key], bg=BG, fg=RED,
                font=("Segoe UI", 13)
            ).grid(row=0, column=2 * i + 1, padx=(0, 24))

        tk.Label(
            top, text="Program :", bg=BG, font=("Segoe UI", 13)
        ).grid(row=0, column=4)

        tk.Label(
            top, textvariable=self.v["prog"], bg=BG, fg=RED,
            font=("Segoe UI", 13)
        ).grid(row=0, column=5, padx=4)

        self.combo = ttk.Combobox(
            hdr, values=list(self.progs), state="readonly", width=14
        )
        self.combo.grid(row=1, column=1, sticky="w", padx=20)
        self.combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self.load_program(self.combo.get())
        )

    def box(self, parent, title, key, row, col=0):

        tk.Label(
            parent, text=title, bg=CYAN, font=("Segoe UI", 12),
            relief="ridge", bd=2, width=22, height=2
        ).grid(row=row, column=col, padx=6, pady=(8, 0))

        tk.Label(
            parent, textvariable=self.v[key], bg=GRAYBOX, fg="#999",
            font=("Segoe UI", 18, "bold"), relief="sunken", bd=1, width=11
        ).grid(row=row + 1, column=col, padx=6)

    def build_left(self):

        left = tk.Frame(self, bg=BG)
        left.grid(row=1, column=0, sticky="nsew")
        for c in range(3):
            left.columnconfigure(c, weight=1)
        left.rowconfigure(2, weight=1)

        # Üst sıra: X | Tool | Y
        self.box(left, "X Position(mm)", "x", 0, col=0)
        self.box(left, "Tool Position(mm)", "toolpos", 0, col=1)
        self.box(left, "Y Position(mm)", "y", 0, col=2)

        self.machine = tk.Canvas(
            left,
            width=int(520 * self.k),
            height=int(230 * self.k),
            bg=BG,
            highlightthickness=0
        )
        self.machine.grid(
            row=2, column=0, columnspan=3, pady=6, sticky="nsew"
        )
        self.machine.bind("<Configure>", lambda e: self.draw_machine())

        self.box(left, "Side-Stop Position(mm)", "side", 3, col=0)
        self.box(left, "Angle(deg)", "ang", 3, col=1)

        tk.Label(
            left, text="Angle Correction(deg)", bg=CYAN,
            font=("Segoe UI", 12), relief="ridge", bd=2, width=22, height=2
        ).grid(row=5, column=0, padx=6, pady=(8, 0))

        self.corr_entry = tk.Entry(
            left, textvariable=self.v["corr"], bg="#c8c8c8", fg="#222",
            font=("Segoe UI", 14, "bold"), relief="sunken", bd=1, width=14,
            justify="center"
        )
        self.corr_entry.grid(row=5, column=1, padx=6, pady=(8, 0))
        self.corr_entry.bind("<Return>", self.apply_angle_correction)
        self.corr_entry.bind("<KP_Enter>", self.apply_angle_correction)
        for key in ("Up", "Down", "space", "e"):
            self.corr_entry.bind("<" + key + ">", lambda event: "break")
        self.corr_entry.bind("<Left>", self.corr_cursor)
        self.corr_entry.bind("<Right>", self.corr_cursor)

        self.status = tk.Label(
            left, text="", bg=BG, fg=BLUE, font=("Segoe UI", 14, "bold")
        )
        self.status.grid(
            row=6, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 0)
        )

        tk.Label(
            left, bg=BG, justify="left", font=("Segoe UI", 10, "bold"),
            text=(
                "[F1]Operating Mode :[F2]Bending Mode :[F3]Side-Stop/X Zero :"
                "[F4]Tool/Y Zero :[F11]Full Screen\n"
                "(B2)+(Side-Stop +) / (Side-Stop -):Move Side-Stop\n"
                "(B1)+(B3)/(B4)/(Foot SW):Move Tool"
            )
        ).grid(row=7, column=0, columnspan=3, sticky="w", padx=6, pady=6)

    def build_right(self):

        right = tk.Frame(self, bg=BG)
        right.grid(row=1, column=1, sticky="nsew", padx=(20, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=3)
        right.rowconfigure(2, weight=2)

        info = tk.Frame(right, bg=BG, relief="groove", bd=2)
        info.grid(row=0, column=0, sticky="ew")
        for c in (1, 3):
            info.columnconfigure(c, weight=1)

        self.kv(info, 0, 0, "Mat.Len", "matlen")
        self.kv(info, 0, 2, "Thickness", "t")
        self.kv(info, 1, 0, "Width", "w")
        self.kv(info, 1, 2, "Tool", "tool")
        self.kv(info, 2, 0, "Radius", "r")
        self.kv(info, 2, 2, "Mat.", "mat")

        self.table = ttk.Treeview(
            right,
            columns=("no", "ss", "ang", "stroke"),
            show="headings",
            height=6,
            selectmode="browse"
        )

        for col, txt, w in (
            ("no", "NO.", 60),
            ("ss", "Side-Stop", 120),
            ("ang", "Angle", 120),
            ("stroke", "Stroke* (mm)", 135)
        ):
            self.table.heading(col, text=txt)
            self.table.column(col, width=w, anchor="center", stretch=True)

        self.table.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        self.table.tag_configure("cur", background=RED, foreground="white")
        self.table.bind("<Button-1>", self.on_click)

        self.prev = tk.Canvas(
            right,
            width=int(440 * self.k),
            height=int(110 * self.k),
            bg=BG,
            highlightthickness=1,
            highlightbackground="#bbb"
        )
        self.prev.grid(row=2, column=0, sticky="nsew", pady=8)
        self.prev.bind("<Configure>", lambda e: self.draw_preview())

    # ------------------------------------------------------------
    # Alt butonlar (Sol/Sağ ile gezilir, seçili olan kırmızı)
    # ------------------------------------------------------------

    def build_buttons(self):

        bar = tk.Frame(self, bg=BG)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        actions = {
            "OP Mode": self.toggle_opmode,
            "Bending\nMode": self.toggle_bendmode,
            "Edit OBJ": self.open_edit_obj,
            "Load": self.open_load,
            "Program>": self.open_anasayfa1,
            "Param>": self.open_param,
            "Monitor": self.open_monitor,
            "Stroke V": self.set_v_opening,
            "Z Hesap": self.open_zhesap,
            "Exit": self.destroy,
        }

        self.btns = []

        for i, (name, cmd) in enumerate(actions.items()):
            bar.columnconfigure(i, weight=1)
            b = tk.Button(
                bar,
                text=name,
                command=lambda i=i, c=cmd: self.press_btn(i, c),
                width=11,
                height=2,
                font=("Segoe UI", 11),
                takefocus=0
            )
            b.grid(row=0, column=i, padx=4, sticky="ew")
            self.btns.append(b)

        self.hl_button(3)            # başlangıçta Load seçili (eski görünüm)

    def hl_button(self, i):
        """i. düğmeyi kırmızı yapar, diğerlerini normal renge döndürür."""
        self.btn_i = i % len(self.btns)
        for j, b in enumerate(self.btns):
            if j == self.btn_i:
                b.config(bg=RED, fg="white",
                         activebackground=RED, activeforeground="white")
            else:
                b.config(bg="#dff0f8", fg="black",
                         activebackground="#dff0f8", activeforeground="black")

    def move_btn(self, d):
        self.hl_button(self.btn_i + d)

    def press_btn(self, i, cmd):
        """Mouse ile tıklanan düğme de kırmızı olur."""
        self.hl_button(i)
        cmd()

    def run_btn(self):
        """Klavyede seçili düğmeyi çalıştırır."""
        self.btns[self.btn_i].invoke()

    # ------------------------------------------------------------
    # anasayfa.py açma (seçili programla)
    # ------------------------------------------------------------

    def open_edit_obj(self):
        """Seçili programın Edit OBJ tablosunu aynı uygulamada aç."""
        try:
            open_edit_obj(self, self.v["prog"].get())
        except Exception as exc:
            self.say(f"Edit OBJ açılamadı: {exc}", RED)

    def open_anasayfa1(self):

        dosya = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "anasayfa1.py"
        )

        if not os.path.exists(dosya):
            self.say("anasayfa1.py dosyası bulunamadı!", RED)
            return

        prog = self.v["prog"].get()

        try:
            # Seçili programın adı anasayfa1'ya argüman olarak gönderilir;
            # kayıtlı (programs/) veya PRESETS'te yoksa varsayılan açılır.
            subprocess.Popen(
                [sys.executable, dosya, prog],
                cwd=os.path.dirname(dosya)
            )

            if prog in PRESETS or prog in load_saved():
                self.say(f"{prog} anasayfa1'da açıldı.", BLUE)
            else:
                self.say(
                    f"{prog} anasayfa1 'da tanımlı değil; varsayılan açıldı.",
                    RED
                )

        except Exception as e:
            self.say(f"Program açılamadı: {e}", RED)

    # ------------------------------------------------------------
    # Çizimler
    # ------------------------------------------------------------

    def draw_machine(self):

        m = self.machine
        m.delete("all")

        m.create_rectangle(0, 100, 430, 165, fill=PANEL, outline="")
        m.create_rectangle(330, 0, 430, 230, fill=PANEL, outline="")

        m.create_oval(14, 118, 44, 148, fill="#2db52d", outline="#145214")
        m.create_oval(270, 108, 300, 138, fill="#2db52d", outline="#145214")

        m.create_rectangle(110, 122, 285, 140, fill="#111", outline="")
        m.create_rectangle(110, 118, 150, 150, fill="#111", outline="")

        m.create_oval(362, 8, 394, 36, fill="#e01010", outline="#900")
        m.create_rectangle(362, 48, 394, 90, fill="#e01010", outline="#900")

        m.create_polygon(362, 90, 394, 90, 378, 118, fill="#111")

        m.create_polygon(
            350, 215, 350, 160, 366, 160, 378, 178,
            390, 160, 406, 160, 406, 215,
            fill="#111"
        )

        if self.estop:
            m.create_oval(40, 60, 78, 98, fill="#e01010", outline="")
            m.create_line(50, 70, 68, 88, fill="white", width=5)
            m.create_line(68, 70, 50, 88, fill="white", width=5)

        # Pencere boyutuna göre ölçekle ve ortala (çizim 430x230 tasarlandı)
        w = m.winfo_width() if m.winfo_width() > 50 else m.winfo_reqwidth()
        h = m.winfo_height() if m.winfo_height() > 50 else m.winfo_reqheight()
        s = max(0.3, min(w / 430, h / 230))
        m.scale("all", 0, 0, s, s)
        m.move("all", (w - 430 * s) / 2, (h - 230 * s) / 2)

    def draw_preview(self):
        """Seçili adım öncesi/sonrası kümülatif profilin şematik görünümü.

        Side-Stop değerleri makine koordinatıdır; gerçek OBJ uzunluğu değildir.
        Aralarındaki farklar yalnızca göreli adım aralığı için kullanılır.
        Bu önizleme makine hareketi veya üretim ölçüsü olarak kullanılmaz.
        """
        p = self.prev
        p.delete("all")
        W, H = max(p.winfo_width(), 440), max(p.winfo_height(), 150)
        bends = list(self.cur.get("bends", [])) if self.cur else []
        if not bends:
            p.create_text(W / 2, H / 2, text="Büküm adımı bulunamadı", fill="#666")
            return
        idx = max(0, min(self.sel, len(bends)-1))
        positions = [float(b[0]) for b in bends]
        angles = [float(b[1]) for b in bends]
        # Başlangıç/bitiş payları bilinmiyorsa görsel amaçlı bir uç payı kullan.
        diffs = [abs(positions[i] - positions[i-1]) for i in range(1,len(positions))]
        typical = sorted([d for d in diffs if d > 0.001])
        margin = max(20.0, (typical[len(typical)//2] if typical else 65.0)*0.55)
        lengths = [margin] + [max(1.0,d) for d in diffs] + [margin]

        def shape(upto):
            pts=[(0.0,0.0)]
            heading=0.0
            x=y=0.0
            for j, length in enumerate(lengths):
                x += length*math.cos(heading)
                y -= length*math.sin(heading)
                pts.append((x,y))
                if j < len(angles) and j < upto:
                    heading += math.radians(angles[j])
            return pts

        before=shape(idx)
        after=shape(idx+1)
        allpts=before+after
        xmin=min(x for x,y in allpts); xmax=max(x for x,y in allpts)
        ymin=min(y for x,y in allpts); ymax=max(y for x,y in allpts)
        left,right,top,bottom=32,18,43,38
        sx=(W-left-right)/max(xmax-xmin,1)
        sy=(H-top-bottom)/max(ymax-ymin,1)
        scale=min(sx,sy)
        dx=(W-left-right-(xmax-xmin)*scale)/2
        dy=(H-top-bottom-(ymax-ymin)*scale)/2
        def screen(pt):
            return (left+dx+(pt[0]-xmin)*scale,top+dy+(pt[1]-ymin)*scale)
        def line(points,color,width,dash=None):
            coords=[]
            for pt in points: coords.extend(screen(pt))
            if len(coords)>=4:
                kw={"fill":color,"width":width,"capstyle":"round","joinstyle":"round"}
                if dash: kw["dash"]=dash
                p.create_line(*coords,**kw)
        # Gri artık düz referans değil: önceki tüm bükümler uygulanmış profil.
        line(before,"#9ca3af",4,(5,3))
        # Siyah: seçilen büküm de tamamlandıktan sonraki tüm profil.
        line(after,"#171717",5)
        pivot=screen(after[idx+1])
        p.create_oval(pivot[0]-4,pivot[1]-4,pivot[0]+4,pivot[1]+4,
                      fill="#171717",outline="")
        # Kırmızı dayama simgesi: yalnızca referans işareti, kalibre edilmiş ofset değil.
        stop=screen(after[0])
        p.create_line(stop[0],stop[1]-18,stop[0],stop[1]+14,
                      fill="#dc1616",width=4)
        p.create_polygon(stop[0]-6,stop[1]-18,stop[0]+6,stop[1]-18,
                         stop[0],stop[1]-25,fill="#dc1616",outline="")
        p.create_text(10,8,anchor="nw",fill=BLUE,font=("Segoe UI",10,"bold"),
                      text=f"Adım {idx+1}/{len(bends)}   Side-Stop: {fmt(positions[idx])} mm   Açı: {fmt(angles[idx])}°")
        p.create_text(W-10,10,anchor="ne",fill="#666",font=("Segoe UI",8),
                      text="Kümülatif şematik görünüm")
        p.create_text(10,H-7,anchor="sw",fill="#666",font=("Segoe UI",8),
                      text="Gri: bu adımdan önce   |   Siyah: bu adımdan sonra   |   Kırmızı: dayama")

    # ------------------------------------------------------------
    # Program listesi / Load
    # ------------------------------------------------------------

    def reload_saved(self):
        """programs/ klasöründeki kayıtları listeye ekler."""
        saved = load_saved()
        self.progs = {n: p for n, p in self.progs.items() if n in saved}
        for n, d in saved.items():
            r, i = d["result"], d["inputs"]
            self.progs[n] = dict(
                mat_len=r["mat_len"], t=i["t"], w=i["w"], tool=i["tool"],
                r=i["r"], mat=i["mat"],
                bends=[tuple(b) for b in r["bends"]]
            )
        self.combo.config(values=list(self.progs))

    def clear_program(self):
        """OP ekranında kayıt seçilmediyse boş tablo ve boş ölçüler göster."""
        self.cur = None
        self.sel = 0
        self.combo.set("")
        for key in ("prog", "matlen", "t", "w", "tool", "r", "mat"):
            self.v[key].set("")
        self.table.delete(*self.table.get_children())
        self.draw_preview()
        self.refresh_status()

    def _sync_programs(self):
        """Ana sayfada yapılan kayıt ve silmeleri OP listesine yansıt (kayıt değişince, 0,5 sn'de bir yoklanır)."""
        try:
            sig = programs_signature()
            if sig != getattr(self, "_prog_sig", None):
                self._prog_sig = sig
                selected = self.v["prog"].get()
                self.reload_saved()
                if selected and selected not in self.progs:
                    self.clear_program()
                elif selected and self.cur is not None:
                    self.load_program(selected)
        finally:
            self.after(500, self._sync_programs)

    def open_load(self):
        """Kayıtlı programları listeler; çift tık veya Yükle ile açar."""
        self.say("Load düğmesine basıldı...", BLUE)
        try:
            LoadScreen(
                self,
                on_load=self.load_from_screen,
                on_delete=self.after_delete,
                current=self.v["prog"].get()
            )
            self.say("Load ekranı açıldı.", BLUE)
        except Exception as e:
            self.say(f"Load ekranı açılamadı: {e}", RED)
            raise

    def load_from_screen(self, name):
        """Load ekranında seçilen programı op ekranına yükler
        ve anasayfa1.py'de de açar."""
        self.reload_saved()
        self.load_program(name)
        

    def after_delete(self, name):
        """Load ekranında silinen programı op listesinden de çıkarır."""
        if name not in PROGRAMS:
            self.progs.pop(name, None)
        self.combo.config(values=list(self.progs))

    # ------------------------------------------------------------
    # Alt buton işlevleri
    # ------------------------------------------------------------

    def toggle_opmode(self):
        """Operating Mode: JOG -> AUTO -> MANUAL -> JOG ..."""
        i = (OP_MODES.index(self.v["opmode"].get()) + 1) % len(OP_MODES)
        self.v["opmode"].set(OP_MODES[i])
        self.say(f"Operating Mode: {OP_MODES[i]}", BLUE)

    def toggle_bendmode(self):
        """Bending Mode: Linear Measurement -> Angle Measurement -> Manual -> Depth -> ..."""
        i = (BEND_MODES.index(self.v["bendmode"].get()) + 1) % len(BEND_MODES)
        self.v["bendmode"].set(BEND_MODES[i])
        self.say(f"Bending Mode: {BEND_MODES[i]}", BLUE)

    def zero_side(self):
        """F3: Side-Stop ve X pozisyonunu sıfırlar."""
        self.v["side"].set(fmt(0))
        self.v["x"].set(fmt(0))
        self.say("Side-Stop / X sıfırlandı.", BLUE)

    def zero_tool(self):
        """F4: Tool ve Y pozisyonunu sıfırlar."""
        self.v["toolpos"].set(fmt(0))
        self.v["y"].set(fmt(0))
        self.say("Tool / Y sıfırlandı.", BLUE)

    def corr_cursor(self, event):
        entry = event.widget
        pos = entry.index("insert")
        entry.icursor(max(0, min(len(entry.get()), pos + (-1 if event.keysym == "Left" else 1))))
        return "break"

    def apply_angle_correction(self, event=None):
        try:
            value = parse(self.v["corr"].get())
            if not math.isfinite(value):
                raise ValueError("Geçersiz açı")
        except (ValueError, TypeError):
            self.say("Geçerli bir açı düzeltmesi girin.", RED)
            return "break"
        self.v["corr"].set(fmt(value))
        self.say(f"Açı düzeltmesi: {fmt(value)}° (yerel giriş; PLC'ye yazılmadı).", BLUE)
        self.focus_set()
        return "break"

    def stroke_text(self, angle):
        value = theoretical_stroke(angle, self.v_opening)
        return fmt(value) if value is not None else "—"

    def set_v_opening(self):
        """V kalıp açıklığını iste; sadece ekrandaki tahmini yeniden hesapla."""
        initial = fmt(self.v_opening) if self.v_opening is not None else ""
        value = simpledialog.askstring(
            "Stroke için V açıklığı", "Alt kalıp V açıklığı (mm):",
            initialvalue=initial, parent=self)
        if value is None:
            return
        try:
            v = parse(value)
            if not math.isfinite(v) or v <= 0:
                raise ValueError
        except ValueError:
            self.say("V açıklığı pozitif sayı olmalı.", RED)
            return
        self.v_opening = v
        if self.cur is not None:
            self.refresh_table()
        self.say("Stroke*: yalnızca teorik tahmin; PLC'ye gönderilmez.", BLUE)

    def refresh_table(self):
        """Ana ekrandaki büküm tablosunu self.cur'dan yeniden doldurur."""
        self.table.delete(*self.table.get_children())
        for i, (pos, ang) in enumerate(self.cur["bends"], 1):
            self.table.insert(
                "", "end", iid=str(i - 1),
                values=(i, fmt(pos), fmt(ang), self.stroke_text(ang))
            )
        n = len(self.cur["bends"])
        if n:
            self.select(min(self.sel, n - 1))
        else:
            self.draw_preview()

    # ---- Program> : büküm tablosunu düzenle
    def open_program(self):
        if self.cur is None:
            return
        if getattr(self, "prog_win", None) and self.prog_win.winfo_exists():
            self.prog_win.lift()
            return

        win = self.prog_win = tk.Toplevel(self)
        win.title("Program - Büküm Tablosu")
        win.configure(bg=BG, padx=10, pady=10)

        tv = ttk.Treeview(win, columns=("no", "ss", "ang", "stroke"), show="headings",
                          height=8, selectmode="browse")
        for col, txt, w in (("no", "NO.", 50), ("ss", "Side-Stop", 120),
                            ("ang", "Angle", 100),
                             ("stroke", "Stroke*", 110)):
            tv.heading(col, text=txt)
            tv.column(col, width=w, anchor="center")
        tv.grid(row=0, column=0, columnspan=3, pady=(0, 8))

        def fill():
            tv.delete(*tv.get_children())
            for i, (p, a) in enumerate(self.cur["bends"]):
                tv.insert("", "end", iid=str(i),
                          values=(i + 1, fmt(p), fmt(a), self.stroke_text(a)))
            self.refresh_table()

        def ask(p0, a0):
            s = simpledialog.askstring("Büküm", "Side-Stop (mm):",
                                       initialvalue=fmt(p0), parent=win)
            if s is None:
                return None
            g = simpledialog.askstring("Büküm", "Açı (derece):",
                                       initialvalue=fmt(a0), parent=win)
            if g is None:
                return None
            try:
                return parse(s), parse(g)
            except ValueError:
                self.say("Geçersiz sayı girildi.", RED)
                return None

            

        def edit(e=None):
            sel = tv.selection()
            if not sel:
                return
            i = int(sel[0])
            r = ask(*self.cur["bends"][i])
            if r:
                self.cur["bends"][i] = r
                fill()

        def add():
            r = ask(0.0, 0.0)
            if r:
                self.cur["bends"].append(r)
                fill()

        def delete():
            sel = tv.selection()
            if sel:
                del self.cur["bends"][int(sel[0])]
                fill()

        tv.bind("<Double-1>", edit)
        for col, (txt, cmd) in enumerate((("Düzenle", edit), ("Ekle", add),
                                          ("Sil", delete))):
            tk.Button(win, text=txt, command=cmd, width=10
                      ).grid(row=1, column=col, padx=4)
        fill()

    # ---- Z Hesap : Z büküm açınım / büküm ekseni hesabı
    def open_zhesap(self):
        if getattr(self, "z_win", None) and self.z_win.winfo_exists():
            self.z_win.lift()
            return
        if self.cur is None:
            return

        win = self.z_win = tk.Toplevel(self)
        win.title("Z Hesap")
        win.configure(bg=BG, padx=14, pady=10)

        c = self.cur
        alanlar = (
            ("Kalınlık t", "t", c["t"]),
            ("İç radius r", "r", c["r"]),
            ("K-faktörü", "k", 0.40),
            ("Genişlik", "w", c["w"]),
            ("Alt kol A", "A", 82.0),
            ("Toplam boy L", "L", 152.0),
            ("Yükseklik H", "H", 95.0),
            ("Açı W (derece)", "W", 60.9),
            ("Makine Mat.Len (k için)", "mach", ""),
        )
        ev = {}
        for i, (lab, key, val) in enumerate(alanlar):
            tk.Label(win, text=lab, bg=BG, font=("Segoe UI", 11),
                     anchor="w", width=22
                     ).grid(row=i, column=0, sticky="w", pady=2)
            ev[key] = tk.StringVar(value="" if val == "" else fmt(val))
            tk.Entry(win, textvariable=ev[key], width=12, justify="right",
                     font=("Segoe UI", 11)).grid(row=i, column=1, pady=2)

        sonuc = tk.StringVar(value="")
        tk.Label(win, textvariable=sonuc, bg=BG, fg=BLUE, justify="left",
                 font=("Segoe UI", 11, "bold")
                 ).grid(row=len(alanlar), column=0, columnspan=2,
                        sticky="w", pady=(8, 0))

        def oku():
            return {k: parse(v.get()) for k, v in ev.items() if k != "mach"}

        def hesapla(uygula):
            try:
                p = oku()
                s = z_hesapla(p["t"], p["r"], p["k"], p["A"], p["L"],
                              p["H"], p["W"])
            except ValueError as e:
                sonuc.set(str(e))
                self.say(str(e), RED)
                return
            metin = (f"Açınım: {fmt(s['mat_len'])} mm\n"
                     f"Eğik kol: {fmt(s['egik'])}   C: {fmt(s['C'])}\n"
                     f"Büküm payı: {fmt(s['ba'])}\n"
                     f"Eksen 1: {fmt(s['eksen1'])}   Eksen 2: {fmt(s['eksen2'])}")
            mach = ev["mach"].get().strip()
            if mach:
                try:
                    kk = k_geri_hesapla(parse(mach), p["t"], p["r"], p["A"],
                                        p["L"], p["H"], p["W"])
                    metin += f"\nMakineyle eşleşen k ≈ {kk:.3f}"
                except ValueError:
                    pass
            sonuc.set(metin)
            if not uygula:
                return
            self.cur["mat_len"] = s["mat_len"]
            self.cur["bends"] = z_bukumler(s, p["W"])
            for k in ("t", "r", "w"):
                self.cur[k] = p[k]
                self.v[k].set(fmt(p[k]))
            self.v["matlen"].set(fmt(s["mat_len"]))
            self.sel = 0
            self.refresh_table()
            self.say("Z hesabı tabloya uygulandı (side-stop = eksen konumu).",
                     BLUE)

        satir = len(alanlar) + 1
        tk.Button(win, text="Hesapla", width=10,
                  command=lambda: hesapla(False)
                  ).grid(row=satir, column=0, pady=(10, 0))
        tk.Button(win, text="Tabloya uygula", width=14,
                  command=lambda: hesapla(True)
                  ).grid(row=satir, column=1, pady=(10, 0))

    # ---- Param> : PLC ayarları
    def open_param(self):
        from paramscreen import menu
        menu(self)

    def open_plc_settings(self):
        if plc_cfg is None:
            self.say("plc.py bulunamadı; parametre ayarlanamaz.", RED)
            return
        if getattr(self, "param_win", None) and self.param_win.winfo_exists():
            self.param_win.lift()
            return

        win = self.param_win = tk.Toplevel(self)
        win.title("Param - PLC Ayarları")
        win.configure(bg=BG, padx=14, pady=10)

        g = lambda n, d: getattr(plc_cfg, n, d)
        en = tk.BooleanVar(value=bool(g("ENABLED", False)))
        fields = {
            "Port": tk.StringVar(value=str(g("PORT", "COM3"))),
            "Baud": tk.StringVar(value=str(g("BAUD", 9600))),
            "Parity (N/E/O)": tk.StringVar(value=str(g("PARITY", "N"))),
            "İstasyon no": tk.StringVar(value=str(g("STATION", 1))),
            "Okuma aralığı (sn)": tk.StringVar(value=str(g("INTERVAL", 0.5))),
        }

        tk.Checkbutton(win, text="PLC okumayı etkinleştir", variable=en,
                       bg=BG, activebackground=BG, font=("Segoe UI", 11)
                       ).grid(row=0, column=0, columnspan=2, sticky="w",
                              pady=(0, 6))
        for i, (lab, var) in enumerate(fields.items(), 1):
            tk.Label(win, text=lab, bg=BG, font=("Segoe UI", 11),
                     anchor="w", width=18
                     ).grid(row=i, column=0, sticky="w", pady=2)
            tk.Entry(win, textvariable=var, width=12, justify="right",
                     font=("Segoe UI", 11)).grid(row=i, column=1, pady=2)

        def apply():
            try:
                port = fields["Port"].get().strip()
                baud = int(fields["Baud"].get())
                parity = fields["Parity (N/E/O)"].get().strip().upper()
                station = int(fields["İstasyon no"].get())
                interval = float(
                    fields["Okuma aralığı (sn)"].get().replace(",", "."))
            except ValueError:
                self.say("Parametrelerde geçersiz sayı var.", RED)
                return
            if not port or parity not in ("N", "E", "O") or interval <= 0:
                self.say("Port boş olamaz, parity N/E/O, aralık > 0 olmalı.",
                         RED)
                return
            self.stop_plc()
            plc_cfg.ENABLED = en.get()
            plc_cfg.PORT, plc_cfg.BAUD, plc_cfg.PARITY = port, baud, parity
            plc_cfg.STATION, plc_cfg.INTERVAL = station, interval
            self._plc_status = None
            self.start_plc()
            if not plc_cfg.ENABLED:
                self.say("PLC okuma kapalı.", BLUE)
            win.destroy()

        row = len(fields) + 1
        tk.Button(win, text="Uygula", command=apply, width=10
                  ).grid(row=row, column=0, pady=(10, 0))
        tk.Button(win, text="Kapat", command=win.destroy, width=10
                  ).grid(row=row, column=1, pady=(10, 0))

    # ---- Monitor : canlı durum
    def open_monitor(self):
        if getattr(self, "mon_win", None) and self.mon_win.winfo_exists():
            self.mon_win.lift()
            return

        win = self.mon_win = tk.Toplevel(self)
        win.title("Monitor")
        win.configure(bg=BG, padx=14, pady=10)

        rows = (("PLC", "plc"), ("Program", "prog"), ("Mode", "mode"),
                ("E-Stop", "estop"), ("Seçili büküm", "bend"),
                ("Side-Stop (mm)", "side"), ("Tool (mm)", "toolpos"),
                ("Angle (deg)", "ang"), ("X (mm)", "x"), ("Y (mm)", "y"))
        mv = {k: tk.StringVar() for _, k in rows}

        for i, (lab, k) in enumerate(rows):
            tk.Label(win, text=lab + " :", bg=BG, font=("Segoe UI", 12),
                     anchor="w", width=16
                     ).grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(win, textvariable=mv[k], bg=BG, fg=RED,
                     font=("Segoe UI", 12, "bold"), anchor="e", width=30
                     ).grid(row=i, column=1, sticky="e")

        def upd():
            if not win.winfo_exists():
                return
            mv["plc"].set(self._plc_status or "PLC kapalı")
            mv["prog"].set(self.v["prog"].get())
            mv["mode"].set(
                f'{self.v["opmode"].get()} / {self.v["bendmode"].get()}')
            mv["estop"].set("AKTİF" if self.estop else "Normal")
            if self.cur and self.cur["bends"]:
                p, a = self.cur["bends"][self.sel]
                mv["bend"].set(f"{self.sel + 1}:  {fmt(p)} mm / {fmt(a)}°")
            else:
                mv["bend"].set("-")
            for k in ("side", "toolpos", "ang", "x", "y"):
                mv[k].set(self.v[k].get())
            win.after(300, upd)

        upd()

    # ------------------------------------------------------------
    # PLC (plc.py; ENABLED = True ise çalışır)
    # ------------------------------------------------------------

    def start_plc(self):
        """plc.py'de ENABLED = True ise PLC'den okumayı başlatır."""
        if plc_cfg is None or not getattr(plc_cfg, "ENABLED", False):
            return
        self.plc = plc_cfg.PlcReader()
        self.plc.start()
        self._poll_id = self.after(300, self.poll_plc)

    def stop_plc(self):
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if self.plc is not None:
            self.plc.stop()
            self.plc = None

    def poll_plc(self):
        """PLC'den gelen son değerleri ekrana yazar (periyodik)."""
        if self.plc is None:
            return
        vals, status, ok = self.plc.snapshot()
        for key in ("side", "toolpos", "ang", "x", "y"):
            if ok and key in vals:
                self.v[key].set(fmt(vals[key]))
        if status != self._plc_status:          # sadece durum değişince mesaj ver
            self._plc_status = status
            self.say(status, BLUE if ok else RED)
        self._poll_id = self.after(
            int(getattr(plc_cfg, "INTERVAL", 0.5) * 1000), self.poll_plc)

    def destroy(self):
        self.stop_plc()
        super().destroy()

    # ------------------------------------------------------------
    # Durum / mantık
    # ------------------------------------------------------------

    def say(self, text, color=BLUE):
        self.status.config(text=text, fg=color)

    def refresh_status(self):
        if self.estop:
            self.say("Emergency Stop!")
        else:
            self.say((self.cur or {}).get("note", ""), RED)

    def toggle_estop(self):
        self.estop = not self.estop
        self.draw_machine()
        self.refresh_status()

    def load_program(self, name):

        if name not in self.progs:
            self.say(f"Program bulunamadı: {name}", RED)
            return

        self.cur = c = self.progs[name]

        self.combo.set(name)
        self.v["prog"].set(name)
        self.v_opening = None  # Her program için V yeniden doğrulanmalı
        self.v["matlen"].set(fmt(c["mat_len"]))

        for k in ("t", "w", "tool", "r"):
            self.v[k].set(fmt(c[k]))

        self.v["mat"].set(str(c["mat"]))

        self.table.delete(*self.table.get_children())

        for i, (pos, ang) in enumerate(c["bends"], 1):
            self.table.insert(
                "", "end", iid=str(i - 1),
                values=(i, fmt(pos), fmt(ang), self.stroke_text(ang))
            )

        self.sel = 0

        self.select(0)
        self.draw_machine()
        self.draw_preview()
        self.refresh_status()

    def select(self, i):

        if not self.cur:
            return

        n = len(self.cur["bends"])

        if not n:
            return

        self.sel = max(0, min(i, n - 1))

        for iid in self.table.get_children():
            self.table.item(
                iid,
                tags=("cur",) if int(iid) == self.sel else ()
            )

        selected = str(self.sel)
        if self.table.exists(selected):
            self.table.selection_set(selected)
            self.table.focus(selected)
        self.draw_preview()

    def on_click(self, e):

        iid = self.table.identify_row(e.y)

        if iid:
            self.select(int(iid))

    def move_side_stop(self):

        if self.estop:
            self.say("Emergency Stop! Hareket yok.")
            return

        pos, _ = self.cur["bends"][self.sel]

        self.v["side"].set(fmt(pos))
        self.v["x"].set(fmt(pos))          # X ekseni = yan dayama konumu

        self.say(f"Side-Stop {fmt(pos)} mm konumuna gitti (simülasyon).")

        if self.v["opmode"].get() == "AUTO":
            self.select(self.sel + 1)


# ------------------------------------------------------------
# Program başlangıcı
# ------------------------------------------------------------

if __name__ == "__main__":

    root = tk.Tk()
    root.withdraw()
    olcek_uygula(root)

    win = OpScreen(root, name="")

    win.bind(
        "<Destroy>",
        lambda e: root.quit() if e.widget is win else None
    )

    root.mainloop()