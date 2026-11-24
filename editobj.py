"""Edit OBJ ekranı (makinedeki Edit OBJ penceresi) -- hücreleri düzenlenebilir, ve ana ekran başlatıcıları.

op1.py içinde:
    from editobj import open_edit_obj, open_program
    Edit OBJ düğmesi -> command=lambda: open_edit_obj(root, program_adi)
    Program düğmesi  -> command=lambda: open_program(program_adi)

Tek başına:  python editobj.py [PROGRAM]
anasayfa1.py ile aynı klasörde durmalıdır (hesap ve program listesi oradan gelir).

Düzenleme: hücre seç (tık / ok tuşları), Enter veya çift tık ya da doğrudan rakam yazarak değiştir.
  OBJ Len. değişince EXE Len. (kümülatif yan dayama) yeniden hesaplanır; EXE Len. değişince OBJ Len. farktan çıkar.
  Insert = boş satır ekle, Delete = satırı sil, F5 = hesaplanan değerlere dön, F9 = kaydet ve çık, Esc = kaydetmeden çık.
Bending Mode "Angle Measurement" iken Stroke sütunu gösterilmez.
"""
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = 15                      # makine ekranında görünen satır sayısı
MAX_ROWS = 30                  # tablo uzunluğu (kaydırmalı)
STROKE_NONE = 999.0            # büküm olmayan / boş satırların Stroke değeri (makine ekranı: 999,0)
OLD_STROKE_NONE = 999.9        # eski kayıtlarda kalan değer; yüklenirken 999,0'a çevrilir
MODES = ("Linear Measurement", "Angle Measurement")
COLS = ("obj", "exe", "ang", "stk")


def parse_num(text):
    """'1.299,5' / '12,5' / '-60' -> float (virgül ondalıktır)."""
    t = str(text).strip().replace(" ", "")
    if not t:
        raise ValueError("Boş değer")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def fmt1(v):
    return f"{v:.1f}".replace(".", ",")


# ----------------------------------------------------------------- tablo modeli (tkinter gerekmez)
class ObjTable:
    """Edit OBJ satırları. Her satır: obj (önceki yan dayamaya göre aralık), exe (yan dayama, kümülatif),
    ang (büküm açısı), stk (stroke). Dolu satırlar baştan itibaren ardışıktır; obj == 0 ilk boş satırı gösterir."""

    def __init__(self, rows=None):
        self.rows = [dict(obj=0.0, exe=0.0, ang=0.0, stk=STROKE_NONE) for _ in range(MAX_ROWS)]
        for i, r in enumerate((rows or [])[:MAX_ROWS]):
            stk = float(r["stk"])
            if stk == OLD_STROKE_NONE:
                stk = STROKE_NONE
            self.rows[i].update(obj=float(r["obj"]), ang=float(r["ang"]), stk=stk)
        self.recalc()

    # -- yardımcılar
    def used(self):
        n = 0
        for i, r in enumerate(self.rows):
            if r["obj"] != 0.0 or r["ang"] != 0.0:
                n = i + 1
        return n

    def recalc(self):
        """EXE = OBJ'lerin kümülatif toplamı (dolu satırlar için); boş satırlar 0."""
        tot = 0.0
        n = self.used()
        for i, r in enumerate(self.rows):
            if i < n:
                tot += r["obj"]
                r["exe"] = tot
            else:
                r["exe"] = 0.0

    def _new_row_defaults(self, i):
        r = self.rows[i]
        if r["obj"] == 0.0 and r["ang"] == 0.0:
            r["stk"] = STROKE_NONE                      # henüz büküm değil

    # -- düzenleme
    def set_value(self, i, col, value):
        if not 0 <= i < MAX_ROWS:
            raise ValueError("Satır yok")
        v = float(value)
        r = self.rows[i]
        if col == "obj":
            if v < 0:
                raise ValueError("OBJ Len. negatif olamaz")
            r["obj"] = v
        elif col == "exe":
            prev = self.rows[i - 1]["exe"] if i else 0.0
            if v < prev:
                raise ValueError(f"EXE Len. önceki satırdan ({fmt1(prev)}) küçük olamaz")
            r["obj"] = v - prev
        elif col == "ang":
            if abs(v) > 180:
                raise ValueError("Açı −180 ile 180 arasında olmalı")
            r["ang"] = v
            if v != 0.0 and r["stk"] == STROKE_NONE:
                r["stk"] = 0.0                          # büküm oldu: stroke 0,0'dan başlar
            elif v == 0.0:
                r["stk"] = STROKE_NONE
        elif col == "stk":
            r["stk"] = v
        else:
            raise ValueError("Bilinmeyen sütun")
        # önceki satırlarda boşluk kalmasın: arada boş satır varsa aşağıdakileri yukarı çek
        self.recalc()
        if col in ("obj", "exe") and r["obj"] != 0.0 and r["stk"] == STROKE_NONE and r["ang"] != 0.0:
            r["stk"] = 0.0

    def insert_row(self, i):
        self.rows.insert(i, dict(obj=0.0, exe=0.0, ang=0.0, stk=STROKE_NONE))
        del self.rows[MAX_ROWS:]
        self.recalc()

    def delete_row(self, i):
        del self.rows[i]
        self.rows.append(dict(obj=0.0, exe=0.0, ang=0.0, stk=STROKE_NONE))
        self.recalc()

    # -- çıktı
    def points(self):
        """Makine ekranındaki gibi profil önizlemesi.

        OBJ satırları bükümler arasındaki mesafelerdir; her büküm satırındaki açı kadar
        yön değişir (işaretli, gerçek açı). Makine fotoğrafındaki bir profil
        (110 düz, -60° basamak, 221 düz, +60° basamak, 678 düz) bu şekilde elde edilir.
        Bu bir CNC takım yolu değil, şematik profildir.
        """
        used = self.rows[:self.used()]
        if not used:
            return [(0.0, 0.0)]
        x = y = heading = 0.0
        pts = [(x, y)]
        for r in used:
            length = abs(float(r["obj"]))
            x += length * math.cos(heading)
            y += length * math.sin(heading)
            pts.append((x, y))
            heading += math.radians(float(r["ang"]))
        if len(pts) < 2 or math.hypot(pts[-1][0], pts[-1][1]) <= 1e-9:
            return [(0.0, 0.0)]
        return pts

    def to_json(self, mode):
        n = self.used()
        return dict(mode=mode, rows=[dict(obj=r["obj"], exe=r["exe"], ang=r["ang"], stk=r["stk"])
                                     for r in self.rows[:n]])

    def text_rows(self, show_stroke=True):
        out = []
        for i, r in enumerate(self.rows):
            out.append((i + 1, fmt1(r["obj"]), fmt1(r["exe"]), fmt1(r["ang"]), fmt1(r["stk"])))
        return out


# ----------------------------------------------------------------- hesap / kayıt
def _ana():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import anasayfa1
    return anasayfa1


def program_names():
    """Hazır + diskte kayıtlı program adları (başka pencerede kaydedilenler dahil)."""
    a = _ana()
    try:
        for n, d in a.load_saved(recompute=False).items():
            a.PRESETS[n] = d["inputs"]
    except Exception:                                                   # noqa: BLE001
        pass
    return list(a.PRESETS)


def program_data(name):
    """Kaydedilmiş sonucu öncelikle kullan; hazır programlarda formülü çalıştır."""
    a = _ana()
    saved = a.load_saved().get(name)
    if saved:
        return saved["inputs"], _with_experimental_calibration(saved["inputs"], saved["result"])
    p = a.PRESETS[name]
    code = str(p["code"])
    src = p.get("stages") or [p]
    stages = []
    for i, ch in enumerate(code):
        st = src[i] if i < len(src) else src[-1]
        stages.append(dict(ac=int(st.get("ac", 1)), A=st.get("a", 0),
                           L=st.get("l", 0), H=st.get("h", 0),
                           C=st.get("c", 0), W=st.get("ang", 0),
                           dim=int(st.get("dim", p.get("dim", 2)))))
    res = a.calc_stages(code, p["t"], p["r"], int(p.get("dim", 2)), stages)
    return p, _with_experimental_calibration(p, res)


def _with_experimental_calibration(inputs, res):
    """Eskiden yalnız 2243438 ekran referansına göre ağırlıklı 44 düzeltmesi uygulanıyordu.

    ZW_programlar.xlsx (169 adet Z+Z, C girilen 1. hane) ile ac=2 türetmesi (AC2_OFF0/1) ve hane geçiş payı
    (STAGE_JOINT) uydurulunca genel formül tek-referans düzeltmesinden daha iyi çıktı (OBJ ≤1,5 mm: 57 -> 72
    program), bu yüzden düzeltme kaldırıldı. Geriye uyumluluk için işlev duruyor ve sonucu değiştirmez.
    """
    return res


OBJ_TOLERANCE_MM = 0.5


def validate_result_geometry(res, tolerance=OBJ_TOLERANCE_MM):
    """Geometri tutarlılığı; makine doğruluğunun kanıtı DEĞİLDİR.

    Sonucu sessizce sıralayıp/son parça uydurup geçmek yerine
    hatalı, negatif veya malzeme sınırını aşan konumları bildirir.
    """
    mat = float(res.get("mat", res.get("mat_len", 0)))
    if not math.isfinite(mat) or mat <= 0:
        raise ValueError("Mat.Length pozitif ve sonlu olmalı")
    bends = [(float(pos), float(ang)) for pos, ang in res.get("bends", [])]
    for pos, ang in bends:
        if not math.isfinite(pos) or not math.isfinite(ang):
            raise ValueError("Büküm pozisyonu/açısı sonlu olmalı")
        if pos < -tolerance or pos > mat + tolerance:
            raise ValueError(f"Side Stop {pos:.2f} mm, Mat.Length {mat:.2f} mm sınırları dışında")
    ordered = sorted(bends)
    for (p1, _), (p2, _) in zip(ordered, ordered[1:]):
        if p2 - p1 < -1e-8:
            raise ValueError("Büküm sırası tutarsız")
        if abs(p2 - p1) < 1e-8:
            raise ValueError("Aynı konumda iki büküm: OBJ sıfır olur; manuel kontrol gerekli")
    return mat, ordered


def table_from_result(res):
    """Deneysel Side Stop konumlarını OBJ farklarına ve EXE kümülatifine çevirir.

    Excel'deki Adımlar sütunuyla karşılaştırma: ondalıklı (makinece hesaplanmış) programlarda işaret ve OBJ
    düzeni uyuşuyor; elle düzenlenmiş OBJ'li programlar formülle uyuşmak zorunda değildir.
    EXE=birikimli OBJ bağıntısı matematiksel olarak sağlanır; gerçek makine
    doğruluğu ancak ölçülmüş OBJ/EXE referansıyla ±0,5 mm test edilerek onaylanır.
    """
    mat, bends = validate_result_geometry(res)
    rows = []
    previous = 0.0
    for pos, angle in bends:
        # Yuvarlama yalnızca ekranda yapılır, iç hesapta tam hassasiyet korunur.
        pos = min(max(pos, 0.0), mat)
        rows.append(dict(obj=pos - previous, ang=angle, stk=STROKE_NONE))
        previous = pos
    mat_obj = mat
    tail = mat_obj - sum(row["obj"] for row in rows)
    if tail > 1e-9:
        rows.append(dict(obj=tail, ang=0.0, stk=STROKE_NONE))
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Büküm tablosu {MAX_ROWS} satırı aşıyor")
    result = ObjTable(rows)
    if abs(sum(r["obj"] for r in result.rows) - mat_obj) > OBJ_TOLERANCE_MM:
        raise ValueError("OBJ toplamı ile Mat.Length arasında ±0,5 mm üzeri tutarsızlık")
    return result



def compare_machine_reference(result, machine_obj, machine_exe, tolerance=OBJ_TOLERANCE_MM):
    """Gerçek makine ile satır bazında karşılaştırma; referanslar formüle girmez.

    Her satır OBJ ve EXE ayrı ayrı toleransı sağlamalıdır. Bu fonksiyon
    yalnızca doğrulama yapar, katsayı öğrenmez veya sonucu değiştirmez.
    """
    table = table_from_result(result)
    if len(machine_obj) != len(machine_exe):
        raise ValueError("Makine OBJ ve EXE satır sayıları farklı")
    if table.used() != len(machine_obj):
        return {"passed": False, "reason": "Satır sayısı farklı", "predicted_rows": table.used(),
                "machine_rows": len(machine_obj), "rows": []}
    rows = []
    for i, (obj, exe) in enumerate(zip(machine_obj, machine_exe)):
        row = table.rows[i]
        e_obj = row["obj"] - float(obj)
        e_exe = row["exe"] - float(exe)
        rows.append({"row": i + 1, "obj_pred": row["obj"], "obj_real": float(obj),
                     "exe_pred": row["exe"], "exe_real": float(exe),
                     "obj_error": e_obj, "exe_error": e_exe,
                     "passed": abs(e_obj) <= tolerance and abs(e_exe) <= tolerance})
    return {"passed": all(row["passed"] for row in rows), "rows": rows,
            "max_obj_error": max(abs(row["obj_error"]) for row in rows),
            "max_exe_error": max(abs(row["exe_error"]) for row in rows)}

def obj_path(name):
    return os.path.join(HERE, "programs", _ana().temiz_ad(name) + ".obj.json")


def _legacy_obj_path(name):
    """Eski kayıtlarda '.ZW' uzantılı adla yazılmış OBJ dosyası."""
    return os.path.join(HERE, "programs", _ana().temiz_ad(name) + ".ZW.obj.json")


def load_table(name):
    """Kaydedilmiş düzenleme varsa onu, yoksa hesaplananı döndürür: (tablo, mod, kayıtlı_mı)."""
    path = obj_path(name)
    if not os.path.isfile(path) and os.path.isfile(_legacy_obj_path(name)):
        path = _legacy_obj_path(name)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            table = ObjTable(d["rows"])
            # set_value negatif OBJ'ye izin vermez: negatif varsa kayıt eski hatalı
            # Side-Stop dönüşümünden kalmıştır -> yok say, yeniden hesapla.
            if any(r["obj"] < 0 for r in table.rows):
                raise ValueError("eski hatalı kayıt")
            return table, d.get("mode", MODES[0]), True
        except (OSError, ValueError, KeyError):
            pass
    p, res = program_data(name)
    return table_from_result(res), MODES[0], False


def save_table(name, table, mode):
    os.makedirs(os.path.join(HERE, "programs"), exist_ok=True)
    with open(obj_path(name), "w", encoding="utf-8") as f:
        json.dump(dict(name=name, **table.to_json(mode)), f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------- başlatıcılar
def open_program(program=None):
    """Program düğmesi: anasayfa1.py'yi ayrı pencerede açar (program adı verilirse onu yükler)."""
    cmd = [sys.executable, os.path.join(HERE, "anasayfa1.py")]
    if program:
        cmd.append(program)
    return subprocess.Popen(cmd, cwd=HERE)


def open_edit_obj(parent=None, program=None):
    """Edit OBJ düğmesi: düzenlenebilir Edit OBJ penceresini açar."""
    import tkinter as tk
    from tkinter import ttk
    BG, HEAD, BLUE_HDR, RED, BLUE = "#ffffff", "#cfe8f4", "#41b5e8", "#d11111", "#1a1acb"
    F = ("Segoe UI", 12)
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("Edit OBJ")
    win.configure(bg=BG)
    try:
        win.state("zoomed")
    except tk.TclError:
        win.geometry("1200x780")

    names = program_names()
    if program not in names:
        program = ""                          # program verilmediyse ekran boş açılır
    cur = tk.StringVar(value=program)
    mode = tk.StringVar(value=MODES[0])
    S = {"table": ObjTable(), "col": 0, "edit": None, "saved": False}

    # ---- üst satır
    top = tk.Frame(win, bg=BG)
    top.pack(fill="x", padx=14, pady=(8, 0))
    tk.Label(top, text="OP Mode :", bg=BG, font=F).pack(side="left")
    tk.Label(top, text="JOG", bg=BG, fg=RED, font=("Segoe UI", 12, "bold")).pack(side="left", padx=(4, 24))
    tk.Label(top, text="Bending Mode :", bg=BG, font=F).pack(side="left")
    mcb = ttk.Combobox(top, textvariable=mode, values=MODES, state="readonly", width=20, font=F)
    mcb.pack(side="left", padx=(4, 24))
    tk.Label(top, text="Program :", bg=BG, font=F).pack(side="left")
    cb = ttk.Combobox(top, textvariable=cur, values=names, state="readonly", width=26, font=F)
    cb.pack(side="left", padx=4)

    tk.Label(win, text="Edit OBJ", bg=BLUE_HDR, fg="white", anchor="w", font=("Segoe UI", 10, "bold")
             ).pack(fill="x", padx=14, pady=(6, 0))
    info = tk.Frame(win, bg=HEAD)
    info.pack(fill="x", padx=14)
    lab_t, lab_tool, lab_r = (tk.StringVar() for _ in range(3))
    for txt, var in (("Thickness [mm]:", lab_t), ("Tool [mm]:", lab_tool), ("Radius [mm]:", lab_r)):
        tk.Label(info, text=txt, bg=HEAD, font=F).pack(side="left", padx=(14, 4), pady=6)
        tk.Label(info, textvariable=var, bg=HEAD, font=("Segoe UI", 12, "bold")).pack(side="left", padx=(0, 40))
    tk.Label(win, text="Length unit:(mm) Angle unit:(deg)", bg=HEAD, anchor="w", font=F
             ).pack(fill="x", padx=14)

    body = tk.Frame(win, bg=HEAD)
    body.pack(fill="both", expand=True, padx=14)
    left = tk.Frame(body, bg=HEAD)
    left.pack(side="left", fill="y", padx=(0, 12), pady=6)
    style = ttk.Style(win)
    style.configure("Obj.Treeview", font=("Segoe UI", 13), rowheight=30)
    style.configure("Obj.Treeview.Heading", font=("Segoe UI", 12, "bold"))
    style.map("Obj.Treeview", background=[("selected", RED)], foreground=[("selected", "white")])
    tv = ttk.Treeview(left, columns=("no",) + COLS, show="headings", height=ROWS,
                      style="Obj.Treeview", selectmode="browse")
    for col, txt, w in (("no", "", 50), ("obj", "OBJ Len.", 130), ("exe", "EXE Len.", 130),
                        ("ang", "Angle", 130), ("stk", "Stroke", 130)):
        tv.heading(col, text=txt)
        tv.column(col, width=w, anchor="e" if col != "no" else "center")
    tv.pack(side="left", fill="y")
    sb = ttk.Scrollbar(left, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    sb.pack(side="left", fill="y")

    right = tk.Frame(body, bg=HEAD)
    right.pack(side="left", fill="both", expand=True, pady=6)
    tk.Label(right, text="Preview", bg=HEAD, font=F).pack()
    cv = tk.Canvas(right, bg="white", highlightthickness=1, highlightbackground="#888")
    cv.pack(fill="both", expand=True)

    bar = tk.Frame(win, bg=BG)
    bar.pack(fill="x", padx=14, pady=(4, 0))
    status = tk.Label(bar, text="", bg=BG, fg=RED, font=F, anchor="w")
    status.pack(side="left", fill="x", expand=True)
    for txt, cmd in (("Satır ekle [Ins]", lambda: do_insert()), ("Satır sil [Del]", lambda: do_delete()),
                     ("Sıfırla [F5]", lambda: do_reset()), ("Kaydet ve çık [F9]", lambda: do_save()),
                     ("Çık [Esc]", lambda: win.destroy())):
        tk.Button(bar, text=txt, command=cmd, font=("Segoe UI", 10)).pack(side="left", padx=2)
    tk.Label(win, text="[up][down][Left][Right]Move cursor,<Enter>Edit,<F2>Search AS-table£¬<Esc>Quit,<F9> confirm and exit",
             bg=BG, fg=BLUE, anchor="w", font=("Segoe UI", 11, "bold")).pack(fill="x", padx=14, pady=(0, 8))

    # ---- görünüm
    def visible_cols():
        return ("no", "obj", "exe", "ang") if mode.get() == MODES[1] else ("no",) + COLS

    def active_cols():
        return [c for c in COLS if c in visible_cols()]

    def say(msg="", err=True):
        status.config(text=msg, fg=RED if err else "#1a7a2a")

    def sel_row():
        sel = tv.selection()
        return tv.index(sel[0]) if sel else 0

    def draw():
        cv.delete("all")
        pts = S["table"].points()
        if len(pts) < 2:
            return
        W = max(cv.winfo_width(), 200)
        H = max(cv.winfo_height(), 200)
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        if width < 1e-9 and height < 1e-9:
            return
        # Yatay makine önizlemesi: düz profillerde sıfır yüksekliğe bölme yok.
        pad = 35
        k = min((W - 2 * pad) / max(width, 1.0),
                (H - 2 * pad) / max(height, 1.0))
        k = max(k, 0.001)
        P = [(W / 2 + (x - (min(xs) + max(xs)) / 2) * k,
              H / 2 - (y - (min(ys) + max(ys)) / 2) * k)
             for x, y in pts]
        if len(P) >= 2:
            cv.create_line(P[0][0], P[0][1], P[1][0], P[1][1],
                           fill=RED, width=4, capstyle="round")
        if len(P) > 2:
            cv.create_line(*sum(P[1:], ()), fill="#222", width=2,
                           joinstyle="round", capstyle="round")
        cv.create_rectangle(P[0][0] - 6, P[0][1] - 6,
                            P[0][0] + 6, P[0][1] + 6,
                            fill=BLUE, outline=BLUE)

    def refresh(keep=None):
        i = sel_row() if keep is None else keep
        tv.configure(displaycolumns=visible_cols())
        tv.delete(*tv.get_children())
        for row in S["table"].text_rows():
            tv.insert("", "end", values=row)
        kids = tv.get_children()
        if kids:
            i = min(max(i, 0), len(kids) - 1)
            tv.selection_set(kids[i])
            tv.focus(kids[i])
            tv.see(kids[i])
        if S["col"] >= len(active_cols()):
            S["col"] = 0
        draw()

    def blank():
        """Program seçili değilken boş ekran."""
        end_edit(commit=False)
        S["table"], S["saved"] = ObjTable(), False
        for var in (lab_t, lab_tool, lab_r):
            var.set("")
        tv.delete(*tv.get_children())
        cv.delete("all")
        say("Üstteki listeden bir program seçin.", err=False)

    def load(*_):
        end_edit(commit=False)
        if not cur.get():
            blank()
            return
        try:
            tbl, md, saved = load_table(cur.get())
            p, _res = program_data(cur.get())
        except Exception as e:                                           # noqa: BLE001
            say(f"Program hesaplanamadı: {e}")
            return
        S["table"], S["saved"] = tbl, saved
        mode.set(md)
        a = _ana()
        lab_t.set(a.fmt(p["t"]))
        lab_tool.set(a.fmt(p.get("tool", 0)))
        lab_r.set(a.fmt(p["r"]))
        say("Manuel OBJ tablosu yüklendi (otomatik hesap yerine); makineyle doğrulayın." if saved else "UYARI: OBJ/EXE deneysel Side Stop hesabıdır; ±0,5 mm makine doğruluğu doğrulanmadı.", err=not saved)
        refresh(0)

    # ---- hücre düzenleme
    def cell_box(i, colname):
        kids = tv.get_children()
        tv.see(kids[i])
        return tv.bbox(kids[i], colname)

    def start_edit(initial=None):
        end_edit(commit=False)
        if not cur.get():
            return
        i, colname = sel_row(), active_cols()[S["col"]]
        box = cell_box(i, colname)
        if not box:
            return
        x, y, w, h = box
        e = tk.Entry(tv, font=("Segoe UI", 13), justify="right", bd=1, relief="solid")
        cur_text = fmt1(S["table"].rows[i][colname])
        e.insert(0, initial if initial is not None else cur_text)
        if initial is None:
            e.select_range(0, "end")
        e.place(x=x, y=y, width=w, height=h)
        e.focus_set()
        e.icursor("end")
        e.bind("<Return>", lambda ev: (end_edit(True), move(1, 0), "break")[-1])
        e.bind("<Tab>", lambda ev: (end_edit(True), move(0, 1), "break")[-1])
        e.bind("<Escape>", lambda ev: (end_edit(False), "break")[-1])
        S["edit"] = (e, i, colname)

    def end_edit(commit=True):
        ed = S["edit"]
        if not ed:
            return True
        e, i, colname = ed
        S["edit"] = None
        txt = e.get()
        e.destroy()
        if not commit:
            tv.focus_set()
            return True
        try:
            S["table"].set_value(i, colname, parse_num(txt))
            say("")
            ok = True
        except ValueError as err:
            say(str(err) if str(err) and not str(err).startswith("could not") else "Geçerli bir sayı girin")
            ok = False
        refresh(i)
        tv.focus_set()
        return ok

    def move(dr, dc):
        kids = tv.get_children()
        if not kids:
            return
        i = min(max(sel_row() + dr, 0), len(kids) - 1)
        S["col"] = min(max(S["col"] + dc, 0), len(active_cols()) - 1)
        tv.selection_set(kids[i])
        tv.focus(kids[i])
        tv.see(kids[i])
        mark()

    def mark():
        say(f"Satır {sel_row() + 1} — {dict(obj='OBJ Len.', exe='EXE Len.', ang='Angle', stk='Stroke')[active_cols()[S['col']]]}",
            err=False)

    def on_click(ev):
        if tv.identify_region(ev.x, ev.y) != "cell":
            return
        end_edit(True)
        iid = tv.identify_row(ev.y)
        c = tv.identify_column(ev.x)                                     # "#1".. (görünen sütun sırası)
        shown = list(visible_cols())
        idx = int(c[1:]) - 1
        if iid and 0 <= idx < len(shown) and shown[idx] != "no":
            S["col"] = active_cols().index(shown[idx])
            tv.selection_set(iid)
            tv.focus(iid)
            mark()
        return "break"

    def on_key(ev):
        if S["edit"] is None and ev.char and ev.char in "0123456789-,." and isinstance(ev.widget, (tk.Tk, tk.Toplevel, ttk.Treeview)):
            start_edit(ev.char)
            return "break"

    def do_insert():
        if not cur.get():
            return
        end_edit(True)
        S["table"].insert_row(sel_row())
        refresh()

    def do_delete():
        if not cur.get():
            return
        end_edit(False)
        S["table"].delete_row(sel_row())
        refresh()

    def do_reset():
        end_edit(False)
        if not cur.get():
            return
        try:
            _p, res = program_data(cur.get())
        except Exception as e:                                           # noqa: BLE001
            say(f"Program hesaplanamadı: {e}")
            return
        S["table"] = table_from_result(res)
        say("Deneysel hesap yüklendi; ±0,5 mm makine doğruluğu doğrulanmadı.", err=True)
        refresh(0)

    def do_save():
        if not cur.get():
            say("Önce bir program seçin.")
            return
        if not end_edit(True):
            return
        try:
            save_table(cur.get(), S["table"], mode.get())
        except OSError as e:
            say(f"Kaydedilemedi: {e}")
            return
        win.destroy()

    tv.bind("<Button-1>", on_click)
    tv.bind("<Double-1>", lambda e: start_edit())
    cb.bind("<<ComboboxSelected>>", load)
    mcb.bind("<<ComboboxSelected>>", lambda e: (end_edit(True), refresh()))
    cv.bind("<Configure>", lambda e: draw())
    win.bind("<Up>", lambda e: None if S["edit"] else (move(-1, 0), "break")[-1])
    win.bind("<Down>", lambda e: None if S["edit"] else (move(1, 0), "break")[-1])
    win.bind("<Left>", lambda e: None if S["edit"] else (move(0, -1), "break")[-1])
    win.bind("<Right>", lambda e: None if S["edit"] else (move(0, 1), "break")[-1])
    win.bind("<Return>", lambda e: None if S["edit"] else (start_edit(), "break")[-1])
    win.bind("<Insert>", lambda e: do_insert())
    win.bind("<Delete>", lambda e: None if S["edit"] else do_delete())
    win.bind("<F2>", lambda e: (cb.focus_set(), cb.event_generate("<Down>")))
    win.bind("<F5>", lambda e: do_reset())
    win.bind("<F9>", lambda e: do_save())
    win.bind("<Escape>", lambda e: win.destroy())
    win.bind("<Key>", on_key)
    load()
    tv.focus_set()

    # başka pencerede kaydedilen / silinen programlar listeye hemen yansısın (0,5 sn'de bir yoklanır)
    sync = {"sig": _ana().programs_signature()}

    def poll():
        try:
            if not win.winfo_exists():
                return
            sig = _ana().programs_signature()
            if sig != sync["sig"]:
                sync["sig"] = sig
                nm = program_names()
                cb.config(values=nm)
                if cur.get() and cur.get() not in nm:
                    cur.set("")
                    blank()
        finally:
            try:
                win.after(500, poll)
            except tk.TclError:
                pass

    win.after(500, poll)
    if parent is None:
        win.mainloop()
    return win


if __name__ == "__main__":
    open_edit_obj(None, sys.argv[1] if len(sys.argv) > 1 else None)