# REVİZYON: işaretli A/L/H/C kabul edilir. Excel adım verileri Side Stop mutlak
# pozisyonu değildir; bu yüzden çoklu kodların PLC onayı kaldırılmıştır.
# Excel çalışma sırasında okunmaz. Matematiksel model deneysel/doğrulanmamıştır.
#!/usr/bin/env python3
"""Bara büküm programı arayüzü (Tkinter) + büküm formülleri + PLC (OPC UA) yazma.

Çalıştırma : python anasayfa.py [PROGRAM_ADI]
Formül testi: python anasayfa.py --test
Side-Stop uydurma: python anasayfa.py --fit olcumler.json  (hane geçiş payı, işaretli −/+)

PROGRAM_ADI verilirse (örn. NEW.ZW) o program açılışta yüklenir
(op.py içindeki "Edit OBJ" düğmesi bu şekilde çağırır).

F9  : program kaydedilir (programs/ADI.ZW.json: girdiler + hesaplanan sonuç) ve
      hesaplanan değerler OPC UA ile PLC'ye yazılır. ESC: çıkış.
op.py'deki Load düğmesi bu kayıtları listeler.

Code String = aşama dizisi. İlk hane ana şekli (1-5), sonraki haneler devam
aşamalarını belirtir (41 = Z + 1, 42 = Z + 2, 44 = Z + Z, 444, 14, 21, 114 ...).
Çok haneli kodlarda ilk hane 1, 2 veya 4; devam haneleri yalnızca 1, 2, 4 olabilir.

ÇOK HANELİ KODLAR (ör. 44): HER HANENİN KENDİ ÖLÇÜ SETİ (A/C, A, L, H, W) vardır, makine
ekranındaki gibi. Arayüzde [Alt+←/→] (veya hane düğmeleri) ile hane seçilir; alanlar o haneyi gösterir.
    Toplam boy      = hanelerin tekli Mat değerlerinin toplamı  (44 örnekleri: ort. +0,8 mm, std 2,1 mm)
    Büküm sırası    : önce 1. hanenin bükümleri, sonra sonraki hanelerinki (Z'de uzak büküm önce).
    Yan dayama (Side-Stop) = şerit başından MUTLAK konum =
                      önceki hanelerin Mat toplamı + hane geçiş payı (STAGE_JOINT, Z→Z: ≈0,48·t mm)
                      + hanenin kendi yerel konumu.
    Açı (Angle)     : her hane kendi W işaretiyle (negatif W: basamak aşağı).
PLC'ye çok haneli program gönderimi PLC_ALLOW_MULTI ile açılır (varsayılan kapalı).

Dim Type:  0 Dış, 1 İç, 2 Dış Açı, 3 İç Açı  ("Dış" tabanlı formüller dış ölçüdür).

------------------------------------------------------------------------------
Formüller (Tum_Deneyler.xlsx, 156 makine ölçümünden çıkarıldı; t,r = kalınlık, yarıçap)
    Büküm payı     BA(θ)  = θ·(r·(1+K_BEND_R) + K(θ)·t)           K(θ) = K_BEND0 + K_BEND1·θ   (K-faktörü, θ radyan)
    Büküm düşümü   BD(θ)  = 2·S·tan(θ/2) − BA(θ)
        S = r + t  (Dış ölçü)   |   S = r  (İç ölçü)          θ = büküm açısı (sapma, radyan)
    Kod 1 / 2   Dış, İç  (A, L, H):  θ = atan2(H, L−A) ; Mat = A + √((L−A)²+H²) − BD
                Açı      (A, C, W):  θ = W            ; Mat = A + C − BD
                (kod 2, kod 1'in aynasıdır; Excel'de aynı Mat değerlerini verdi)
    Kod 4 (Z)   Dış (A, L, H, C):  W = atan2(H − t, (L−A−C) + RUN_Z)
                Açı (A/C, L, H, W): Mat = L + (H + t·e)·tan(W/2) − 2·BD(W, dış)
                                    e = 1 (Dış)  |  e = 2 (İç)      -> Mat, A'dan bağımsız
    Kod 5 (U)   Mat = H + L1 + L2 − 2·BD(90°)      (L1 = arayüzde A, L2 = arayüzde L)
    Kod 3       İki bükümlü (W ve 90°−W): Mat = A + s + v − BD(W) − BD(90°−W)  (bkz. stage_calc).
------------------------------------------------------------------------------
"""
import asyncio
import json
import math
import os
import sys
import threading
from datetime import datetime

from asyncua import Client, ua

# Büküm payı (K-faktörlü):  BA = θ·(r·(1+K_BEND_R) + K(θ)·t),   K(θ) = K_BEND0 + K_BEND1·θ   (θ radyan)
# Eski model (r + t/2 − (0,125 + 0,0275·t)) açıdan bağımsız K veriyordu; Excel'deki kod 1/2 ölçümlerinden
# K'nın açıyla arttığı görüldü (θ=40° -> K≈0,44 ; θ=140° -> K≈0,47). Yeni model: Kod 1/2 (84 satır) RMS 0,11 mm,
# en büyük sapma 0,33 mm (makine çıktısı 0,1 mm yuvarlanıyor).
K_BEND0 = 0.409
K_BEND1 = 0.019
K_BEND_R = 0.008          # yarıçapa bağlı küçük düzeltme: BA = θ·(r·(1+K_BEND_R) + K(θ)·t)
# Tek Z'de (kod 4) Dış ölçü modunda (A, L, H, C girilince) açıyı bulmak için L−A−C farkına
# eklenen düzeltme: W = atan2(H − t, L − A − C + RUN_Z).  Excel'deki 34 satırdan yeniden uyduruldu (RMS ≈ 0,22 mm).
RUN_Z = 0.2
K_FACTOR = 0.5            # yalnızca kod 5 (U) yan dayama konumlarında (ekrandan doğrulanmış 120,7)
# Çok haneli kodlar (41/42/44/...): HER HANENİN KENDİ ÖLÇÜ SETİ vardır (makine ekranında imleç bir
# hane üzerine gelince o haneye ait A/C, A, L, H, W alanları görünür; örn. 44 -> 1. Z ve 2. Z ayrı).
# Toplam boy = hanelerin tekli Mat değerlerinin toplamı (ZW_programlar.xlsx "Notlar" sayfası).
# Doğrulama: 188 adet Z+Z (kod 44) kaydı -> ortalama fark +0,8 mm, std 2,1 mm (t=3..15 mm).
#            3 adet kod 41 örneği (Z + 90° büküm) -> fark < 0,1 mm.
MULTI_T_RANGE = (3.0, 15.0)    # Z+Z kayıtlarının kapsadığı kalınlık aralığı [mm]
MULTI_CODES = {"1", "2", "3", "4", "5"}
MULTI_FIRST = {"1", "2", "3", "4", "5"}  # çok haneli kodda izin verilen İLK haneler
# Hane geçiş payı [mm]: bir hanenin son bükümü ile sonraki hanenin ilk bükümü arasındaki yan dayama
# farkı, "önceki hane Mat'ı + sonraki hane yerel konumu" değerinden bu kadar büyük çıkıyor.
# Z->Z geçişi, ZW "Adımlar" kayıtlarından (161 Z+Z program, aykırılar dışarıda, en küçük mutlak sapma
# uyumu) kalınlığa orantılı çıktı: joint ≈ 0,48·t  (t=10 -> 4,8 mm). Sabit 4,2 mm yerine bu kullanılır:
# 2 mm içinde kalan program 111/161 -> 125/161 (5 katlı çapraz doğrulama). Açıya bağımlılık denendi,
# ama 75°+ için yalnızca 7 program olduğundan eklenmedi. Diğer geçişler için veri yok -> doğrulanmamış.
STAGE_JOINT = {("4", "4"): 4.35}
# --- İŞARETLİ (− / +) YAN DAYAMA DÜZELTMESİ ------------------------------------------------
# Hane geçiş payı artık sabit değil, kalınlığa bağlı doğrusal bir formül ve İŞARETLİDİR:
#     joint(t) = STAGE_JOINT[geçiş] + STAGE_JOINT_T[geçiş] · (t − SS_T0)       [mm]
# (+) = yan dayama şerit başından UZAKLAŞIR, (−) = şerit başına YAKLAŞIR.
# STAGE_JOINT_T varsayılan 0'dır (veri yok); makine Side-Stop ölçümlerinden
#     python anasayfa.py --fit olcumler.json
# komutu en küçük kareler ile STAGE_JOINT ve STAGE_JOINT_T değerlerini hesaplar, çıktıyı buraya yapıştırın.
SS_T0 = 10.0
STAGE_JOINT_T = {("4", "4"): 0.32}
# Büküm bazında ek işaretli düzeltme [mm]: anahtar = (kod dizisi, tablodaki büküm no 1-tabanlı).
# Örn. SS_BEND_CORR = {("44", 3): -0.4, ("44", 4): +0.6}.   --fit bunu da önerir.
SS_BEND_CORR = {}


def temiz_ad(ad):
    ad = os.path.basename(str(ad).strip())
    if ad.lower().endswith(".zw"):
        ad = ad[:-3]
    return ad

def stage_joint(prev_ch, ch, t):
    """Hane geçiş payı (işaretli, mm). Geçiş için veri yoksa None döner."""
    key = (prev_ch, ch)
    if key not in STAGE_JOINT:
        return None
    return STAGE_JOINT[key] + STAGE_JOINT_T.get(key, 0.0) * (t - SS_T0)
VERIFIED_MULTI = set()  # yeni Excel adımları mutlak Side Stop doğrulaması sağlamıyor        # yan dayama konumları da doğrulanmış çok haneli kodlar
# Çok haneli kodlarda Dış/İç ölçü modunda makine L−A−C farkına ~0,5 mm ekliyor
# (5418933-2: L=292, A+C=292 -> W≈89,7°, Mat=668,8). Yalnızca çok haneli kodlardaki Z için kullanılır.
RUN_OFF = 0.5
# Çok haneli Z'de C girilince (ac=2) A = L − C − (H−t)/tanW − AC2_OFF0 − AC2_OFF1·(t − SS_T0)  [mm]
# (ZW_programlar.xlsx Edit OBJ adımlarından uydurulur; dx_exact türetmesi 208 programda belirgin daha kötü çıktı)
AC2_OFF0 = 1.25
AC2_OFF1 = 0.13
MATS = {1: "bakır", 2: "alüminyum", 3: "pirinç", 4: "çelik", 5: "diğer"}

# Doğrulanmış kodlar (diğerleri uyarı verir ve PLC'ye gönderilmez). 3: Excel verisi yetersiz.
KNOWN_CODES = {"1", "2", "3", "4", "5", "41", "42", "44"}
STAGE_KEYS = ("ac", "a", "l", "h", "ang", "c")   # bir hanenin arayüz alanları


# ------------------------------------------------------------ PLC ayarları
PLC_URL = "opc.tcp://192.168.1.2:4840"
PLC_TIMEOUT = 5          # saniye

# PLC'deki node ID'ler (S7-1200, tarama çıktısından). Anahtar = arayüzdeki alan.
PLC_NODES = {
    "mat_len": "ns=4;i=22",   # MalzemeBoyu  (hesaplanan)
    "t":       "ns=4;i=23",   # Kalinlik
    "w":       "ns=4;i=24",   # Genislik
    "tool":    "ns=4;i=25",   # Takim
    "r":       "ns=4;i=26",   # YariCap
    "mat":     "ns=4;i=28",   # MeteryalTipi
    "dim":     "ns=4;i=29",   # OlcuTipi
    "ac":      "ns=4;i=30",   # AveyaC
    "a":       "ns=4;i=31",   # UzunlukA   (kod 5'te L1)
    "l":       "ns=4;i=32",   # UzunlukL   (kod 5'te L2)
    "h":       "ns=4;i=33",   # YukseklikH
    "c":       "ns=4;i=34",   # UzunlukC
    "ang":     "ns=4;i=35",   # AciWDerece
}

# Büküm sonuçlarını da yazmak için (varsayılan KAPALI: makine hareketi etkileyebilir).
# Anlamlarını doğruladıktan sonra True yapın. Yalnızca 1. büküm yazılır
# (tablonun 1. satırı = en büyük yan dayamalı büküm; çok haneli kodlarda son hanenin uzak bükümü).
PLC_WRITE_BEND1 = False
PLC_BEND1_NODES = {
    "pos": "ns=4;i=17",       # XekseniPozisyon  <- 1. bükümün yan dayama değeri
    "ang": "ns=4;i=19",       # YekseniAci       <- 1. bükümün açısı
}
# Kullanılmayan değişkenler (bilerek yazılmıyor): Radius (i=27), ModSecimi (i=21),
# OpMode / BendingMode / X-Y Baslangic-Bitis bitleri (i=12..16), YekseniPozisyon (i=18),
# YEkseniDuzeltme (i=20).

# İsteğe bağlı: tüm değerler yazıldıktan sonra True yapılacak onay biti.
# Kullanmak istemezseniz None bırakın.
PLC_CONFIRM_NODE = None   # örn. 'ns=4;i=NN' (PLC'de onay biti açarsanız)

# PLC'de yalnızca TEK ölçü seti (A, L, H, C, W) alanı var. Çok haneli kodlarda yalnızca 1. hanenin
# ölçüleri + toplam boy yazılabilir; PLC'nin diğer haneleri nasıl alacağı doğrulanana kadar KAPALI.
PLC_ALLOW_MULTI = False

# F9 sonrası pencere kapansın mı? False: açık kalır (arka arkaya birden fazla kayıt yapılabilir).
# True: kayıt + PLC gönderimi başarılıysa pencere kapanır.
EXIT_AFTER_SAVE = False

# Örnekler (dim = Dim Type, ref = makinedeki Mat.Length)
# Çok haneli kodlarda "stages" = hane başına ölçü seti (ac, a, l, h, ang, c). "stages" yoksa tek set
# tüm hanelere kopyalanır (eski kayıtlar).
PRESETS = {
    "NEW.ZW": dict(code="4", t=10, w=80, tool=63, r=5, mat=1, ac=1, a=112, l=1299.5,
                   h=20, ang=28, c=0, dim=2, ref=1301.4),
    # 41 = Z (W=90°) + 90° büküm:  Mat = MatZ + A2 + L2 − BD(90°)   (ZW: 5418933-2)
    "5418933-2.ZW": dict(code="41", t=5, w=40, tool=63, r=5, mat=1, ac=1, a=135, l=292,
                         h=100, ang=90, c=0, dim=2, ref=668.8,
                         stages=[dict(ac=1, a=135, l=292, h=100, ang=90, c=0),
                                 dict(ac=1, a=100, l=0, h=0, ang=90, c=198)]),
    # 44 = Z + Z: ekran fotoğrafları (hane 1: C=65, L=200, H=44, W=-60 | hane 2: A=152, L=844, H=29,5, W=60)
    "2243438.ZW": dict(code="44", t=10, w=100, tool=32, r=5, mat=1, ac=2, a=65, l=200,
                       h=44, ang=-60, c=0, dim=2, ref=1068.6,
                       stages=[dict(ac=2, a=65, l=200, h=44, ang=-60, c=0),
                               dict(ac=1, a=152, l=844, h=29.5, ang=60, c=0)]),
    "2242081.ZW": dict(code="44", t=6, w=70, tool=32, r=5, mat=1, ac=2, a=85, l=200,
                       h=44, ang=-60, c=0, dim=2, ref=830.1,
                       stages=[dict(ac=2, a=85, l=200, h=44, ang=-60, c=0),
                               dict(ac=1, a=127, l=601, h=21.5, ang=50, c=0)]),
    "2514771.ZW": dict(code="42", t=5, w=50, tool=63, r=5, mat=1, ac=1, a=60, l=249.8,
                       h=180, ang=74, c=0, dim=2, ref=570.2),   # 2. hane ölçüsü kayıtta yok
    "2U1-2U2.ZW": dict(code="4", t=5, w=60, tool=32, r=5, mat=1, ac=1, a=82, l=152,
                       h=95, ang=60.9, c=0, dim=2, ref=202.2),
    "16.ZW": dict(code="4", t=12, w=16, tool=90, r=15, mat=1, ac=1, a=100, l=500,
                  h=206, ang=45, c=0, dim=2, ref=577.8),
    # Aşağıdakiler 2. hane ölçüsü bilinmediği için ilk hane ölçüsü kopyalanır (formülle uyuşmaz):
    "119-7594 ÜÇ BÜKÜM.ZW": dict(code="41", t=10, w=25, tool=63, r=10, mat=1, ac=1, a=55,
                                 l=500, h=87.9, ang=60, c=0, dim=2, ref=741.5),
    "2237734--.ZW": dict(code="44", t=10, w=80, tool=40, r=5, mat=1, ac=1, a=80, l=200,
                         h=34, ang=-60, c=0, dim=3, ref=942.3),
}

# ------------------------------------------------------------ kayıt / yükleme
PROG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "programs")


EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Kaydedilen_Programlar.xlsx")


def save_excel(name, inputs, result):
    """F9 kaydını Excel arşivine ekler; hesaplama Excel'e bağlı değildir."""
    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("Excel kaydı için 'pip install openpyxl' komutunu çalıştırın.") from exc

    stages = inputs.get("stages") or [inputs]
    bends = result.get("bends", [])
    headers = ["Kayıt Tarihi", "Program", "Kod", "Kod Sayısı", "Mat.Length", "Kalınlık t",
               "Genişlik", "Yarıçap r", "Takım", "Malzeme", "Dim Type"]
    for i in range(1, 5):
        headers += [f"K{i} A/C", f"K{i} A", f"K{i} L", f"K{i} H", f"K{i} C", f"K{i} W"]
    headers += ["Büküm Sayısı"]
    for i in range(1, 13):
        headers += [f"Büküm {i} Side Stop", f"Büküm {i} Angle"]
    headers += ["Not"]

    if os.path.isfile(EXCEL_PATH):
        wb = load_workbook(EXCEL_PATH)
        ws = wb["Programlar"] if "Programlar" in wb.sheetnames else wb.active
        if ws.max_row == 1 and ws.cell(1, 1).value is None:
            ws.append(headers)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Programlar"
        ws.append(headers)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="17365D")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(wrap_text=True)
        for j, header in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(j)].width = 19 if j > 11 else 17
        ws.column_dimensions["A"].width = 21
        ws.column_dimensions["B"].width = 28

    row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name, str(inputs.get("code", "")),
           len(stages), result.get("mat_len", result.get("mat")), inputs.get("t"),
           inputs.get("w"), inputs.get("r"), inputs.get("tool"), inputs.get("mat"),
           inputs.get("dim")]
    for i in range(4):
        st = stages[i] if i < len(stages) else {}
        row += [st.get("ac"), st.get("a", st.get("A")), st.get("l", st.get("L")),
                st.get("h", st.get("H")), st.get("c", st.get("C")),
                st.get("ang", st.get("W"))]
    row.append(len(bends))
    for i in range(12):
        bend = bends[i] if i < len(bends) else (None, None)
        row += [bend[0], bend[1]]
    row.append("12'den fazla büküm var; kalanlar Excel'e yazılmadı" if len(bends) > 12 else "")
    # Program adı tekil anahtardır: aynı program yeniden kaydedilirse satır güncellenir.
    old_row = next((ri for ri in range(2, ws.max_row + 1)
                    if str(ws.cell(ri, 2).value or '').strip().casefold() == name.casefold()), None)
    target = old_row if old_row is not None else ws.max_row + 1
    for ci, value in enumerate(row, 1):
        ws.cell(target, ci, value)
    try:
        wb.save(EXCEL_PATH)
    finally:
        wb.close()


def save_program(name, inputs, result):
    """programs/ADI.json dosyasına girdileri ve hesaplanan sonucu yazar."""
    os.makedirs(PROG_DIR, exist_ok=True)
    with open(os.path.join(PROG_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(dict(name=name, inputs=inputs, result=result),
                  f, ensure_ascii=False, indent=2)
    save_excel(name, inputs, result)



def delete_program(name):
    """JSON ve Excel'deki programı sil. Excel açıksa silmeyi başlatma."""
    from openpyxl import load_workbook
    excel_wb = None
    if os.path.isfile(EXCEL_PATH):
        excel_wb = load_workbook(EXCEL_PATH)
        ws = excel_wb['Programlar'] if 'Programlar' in excel_wb.sheetnames else excel_wb.active
        for ri in range(ws.max_row, 1, -1):
            if str(ws.cell(ri, 2).value or '').strip().casefold() == name.casefold():
                ws.delete_rows(ri)
        try:
            excel_wb.save(EXCEL_PATH)
        finally:
            excel_wb.close()
    path = os.path.join(PROG_DIR, name + '.json')
    os.remove(path)


def export_programs_excel(destination=None):
    """JSON arşivindeki bütün programları tekil satırlarla Excel'e aktar."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    destination = destination or EXCEL_PATH
    # Geçici ayrı dosyada derle, başarılı olursa hedefe taşı.
    import tempfile
    fd, temp = tempfile.mkstemp(suffix='.xlsx', dir=os.path.dirname(os.path.abspath(destination)))
    os.close(fd)
    try:
        records = load_saved()
        # save_excel'in başlık ve satır formatını kullanmak için kayıt fonksiyonuna
        # bağımlı kalmıyoruz; eski Excel kayıtlarını yeniden üretirken tekil isim kullanıyoruz.
        headers = ['Kayıt Tarihi','Program','Kod','Kod Sayısı','Mat.Length','Kalınlık t',
                   'Genişlik','Yarıçap r','Takım','Malzeme','Dim Type']
        for k in range(1,5):
            headers.extend([f'K{k} A/C',f'K{k} A',f'K{k} L',f'K{k} H',f'K{k} C',f'K{k} W'])
        headers.append('Büküm Sayısı')
        for k in range(1,13): headers.extend([f'Büküm {k} Side Stop',f'Büküm {k} Angle'])
        headers.append('Not')
        wb=Workbook(); ws=wb.active; ws.title='Programlar'; ws.append(headers)
        ws.freeze_panes='A2'; ws.auto_filter.ref=f'A1:{get_column_letter(len(headers))}1'
        for cell in ws[1]:
            cell.fill=PatternFill('solid',fgColor='17365D')
            cell.font=Font(color='FFFFFF',bold=True)
            cell.alignment=Alignment(wrap_text=True)
        for j in range(1,len(headers)+1):
            ws.column_dimensions[get_column_letter(j)].width=19 if j>11 else 17
        ws.column_dimensions['A'].width=21;ws.column_dimensions['B'].width=28
        from datetime import datetime
        for name, rec in sorted(records.items()):
            i=rec.get('inputs',{}); r=rec.get('result',{})
            stages=i.get('stages') or [i]; bends=r.get('bends',[])
            row=[datetime.now().strftime('%Y-%m-%d %H:%M:%S'),name,str(i.get('code','')),
                 len(stages),r.get('mat_len',r.get('mat')),i.get('t'),i.get('w'),
                 i.get('r'),i.get('tool'),i.get('mat'),i.get('dim')]
            for k in range(4):
                st=stages[k] if k<len(stages) else {}
                row.extend([st.get('ac'),st.get('a',st.get('A')),st.get('l',st.get('L')),
                            st.get('h',st.get('H')),st.get('c',st.get('C')),st.get('ang',st.get('W'))])
            row.append(len(bends))
            for k in range(12):
                b=bends[k] if k<len(bends) else (None,None)
                row.extend([b[0],b[1]])
            row.append('12 üzeri bükümler Excel sütunlarına sığmadı' if len(bends)>12 else '')
            ws.append(row)
        wb.save(temp);wb.close()
        os.replace(temp,destination)
        return len(records)
    finally:
        if os.path.exists(temp): os.remove(temp)


def fresh_result(inputs, saved=None):
    """Kayıtlı ölçülerden Mat.Length ve Side-Stop'u GÜNCEL formülle yeniden hesaplar.

    F9 ile kaydedilen `result` o günün formülüyle donmuştur; formül düzeldikçe Load, OP ve Edit OBJ
    birbirinden farklı değer göstermesin diye hepsi bunu kullanır. Hesaplanamazsa kayıtlı sonuç döner."""
    try:
        code = str(inputs["code"]).strip()
        src = inputs.get("stages") or [inputs]
        stages = []
        for i, _ch in enumerate(code):
            st = src[i] if i < len(src) else src[-1]
            stages.append(dict(ac=int(st.get("ac", 1)), A=float(st.get("a", 0)), L=float(st.get("l", 0)),
                               H=float(st.get("h", 0)), C=float(st.get("c", 0)), W=float(st.get("ang", 0)),
                               dim=int(st.get("dim", inputs.get("dim", 2)))))
        res = calc_stages(code, float(inputs["t"]), float(inputs["r"]), int(inputs.get("dim", 2)), stages)
        return dict(mat_len=res["mat"], bends=[[p, a] for p, a in res["bends"]])
    except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError):
        return saved


def load_saved(recompute=True):
    """Geçerli program JSON kayıtlarını yükle; OBJ/uyumsuz dosyaları atla."""
    out = {}
    if not os.path.isdir(PROG_DIR):
        return out
    for fn in sorted(os.listdir(PROG_DIR)):
        if not fn.lower().endswith(".json") or fn.lower().endswith(".obj.json"):
            continue
        try:
            with open(os.path.join(PROG_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                continue
            inputs, result = d.get("inputs"), d.get("result")
            name = d.get("name")
            if (not isinstance(name, str) or not name.strip()
                    or not isinstance(inputs, dict)
                    or not isinstance(result, dict)):
                continue
            if not all(k in inputs for k in ("code", "t", "r", "w", "tool", "mat")):
                continue
            if recompute:
                d["saved_result"] = result
                calculated = fresh_result(inputs, result)
                if isinstance(calculated, dict):
                    d["result"] = calculated
            if not isinstance(d.get("result"), dict):
                continue
            if not all(k in d["result"] for k in ("mat_len", "bends")):
                continue
            out[name] = d
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            continue
    return out


def programs_signature():
    """programs/ klasörünün parmak izi (dosya adı, değişme zamanı, boyut). Sayfalar bunu yoklayıp yalnızca
    kayıt eklenip/silinip/değiştiğinde listelerini yeniler (başka pencerede F9 ile kaydedilen program hemen görünür)."""
    try:
        return tuple(sorted((fn, os.stat(os.path.join(PROG_DIR, fn)).st_mtime_ns,
                             os.stat(os.path.join(PROG_DIR, fn)).st_size)
                            for fn in os.listdir(PROG_DIR) if fn.lower().endswith(".json")))
    except OSError:
        return ()


for _name, _record in load_saved(recompute=False).items():
    if isinstance(_record, dict) and isinstance(_record.get("inputs"), dict):
        PRESETS[_name] = _record["inputs"]


# --------------------------------------------------------------- PLC yazma
async def plc_yaz_async(veriler):
    """veriler: [(isim, node_id, değer), ...]. Her değer PLC'deki değişken tipine çevrilir."""
    async with Client(url=PLC_URL, timeout=PLC_TIMEOUT) as client:
        for isim, node_id, deger in veriler:
            node = client.get_node(node_id)
            veri_tipi = await node.read_data_type_as_variant_type()

            if veri_tipi == ua.VariantType.Boolean:
                deger = bool(deger)
            elif veri_tipi in (ua.VariantType.Float, ua.VariantType.Double):
                deger = float(deger)
            else:
                deger = int(round(deger))

            await node.write_value(ua.DataValue(ua.Variant(deger, veri_tipi)))

        # Tüm değerler yazıldıktan sonra onay biti (varsa)
        if PLC_CONFIRM_NODE:
            node = client.get_node(PLC_CONFIRM_NODE)
            veri_tipi = await node.read_data_type_as_variant_type()
            await node.write_value(ua.DataValue(ua.Variant(True, veri_tipi)))


# --------------------------------------------------------------- formüller
def bend_allowance(t, r, th):
    """Büküm payı BA (θ radyan)."""
    k = K_BEND0 + K_BEND1 * th
    return th * (r * (1 + K_BEND_R) + k * t)


def bend_setback(t, r, th, outer=True):
    """Geri çekilme: Dış ölçüde (r+t)·tan(θ/2), iç ölçüde r·tan(θ/2)."""
    return (r + (t if outer else 0.0)) * math.tan(th / 2)


def bend_deduct(t, r, th, outer=True):
    """Büküm düşümü BD = 2·geri çekilme − BA."""
    return 2 * bend_setback(t, r, th, outer) - bend_allowance(t, r, th)


def mat_length_kod4(t, r, L, H, W, outer=True):
    """Kod 4 / Z: yalnızca açınım boyu (mm).

    W derece, diğer ölçüler mm. A/C ve takım ölçüsü bu deneysel
    formülün girdisi değildir. Kalibrasyon uydurması uygulanmaz.
    """
    if not all(math.isfinite(v) for v in (t, r, L, H, W)):
        raise ValueError("Ölçüler sonlu sayılar olmalı.")
    if t <= 0 or r < 0 or not (0 < W <= 90):
        raise ValueError("Kod 4: t>0, r>=0, L>0, H>t ve 0<W<=90 olmalı.")
    th = math.radians(W)
    bd_dis = bend_deduct(t, r, th, outer=True)
    e = 1 if outer else 2
    return L + (H + e * t) * math.tan(th / 2) - 2 * bd_dis


def dx_exact(t, H, W):
    """Z bükümde eğimin yatay kayması (dış ölçü): (H·cosW − t) / sinW.  W derece."""
    th = math.radians(W)
    return (H * math.cos(th) - t) / math.sin(th)


def z_angle_from_len(t, A, L, H, C, calibrated_multi=False):
    """Z için A, L, H, C'den açıyı (derece) bulur:  W = atan2(H − t, (L−A−C) + düzeltme).
    Tek Z: düzeltme = RUN_Z (Excel); 41/42/44: RUN_OFF (eski ekran kalibrasyonu)."""
    off = RUN_OFF if calibrated_multi else RUN_Z
    d0 = L - A - C
    if abs(H - t) < 1e-12 and abs(d0 + off) < 1e-12:
        raise ValueError("Z açısı belirsiz: yatay ve düşey fark aynı anda sıfır.")
    return math.degrees(math.atan2(H - t, d0 + off))


def z_run(t, H, W, calibrated_multi=False):
    """z_angle_from_len'in tersi: W'den  L − A − C  farkı."""
    off = RUN_OFF if calibrated_multi else RUN_Z
    return (H - t) / math.tan(math.radians(W)) - off


def stage_calc(code, t, r, outer, mode, d, ac=1, calibrated_multi=False):
    """Tek bir kod hanesinin (aşamanın) hesabı.

    code  : 1-5          outer : True = Dış ölçü (Dış / Dış Açı), False = İç
    mode  : "len"  -> 1/2: A, L, H     4: A, L, H, C     5: L1(A), L2(L), H
            "angle"-> 1/2: A, C, W     3, 4: A/C, L, H, W
    d     : dict(A, L, H, C, W)  (W mutlak derece)
    Döner : dict(mat, bends [(yan_dayama, açı)], pts, warn, W, A, flat_last, along, heading)
        bends bu aşamanın kendi sırasıdır (Z ve U'da uzak büküm önce, eski düzen korundu).
        flat_last = aşamanın son düz kısmı (bir sonraki hanenin bükümüne kadar olan boy).
    """
    A, L, H, C, W = d["A"], d["L"], d["H"], d["C"], d["W"]
    warn = ""
    if t <= 0 or r < 0:
        raise ValueError("Kalınlık > 0 ve yarıçap ≥ 0 olmalı.")

    if code in (1, 2):
        if mode == "len":
            if abs(L - A) < 1e-12 and abs(H) < 1e-12:
                raise ValueError("Açı tanımsız: L-A ve H aynı anda sıfır.")
            th = abs(math.atan2(H, L - A))           # işaretli A/L/H; büküm büyüklüğü
            leg = math.hypot(L - A, H)
        else:
            if abs(A) + abs(C) < 1e-12:
                raise ValueError("A ve C aynı anda sıfır olamaz.")
            if not 0 < W < 180:
                raise ValueError("Açı 0–180° arasında olmalı.")
            th = math.radians(W)
            leg = C
        if th >= math.radians(179):
            raise ValueError("Büküm açısı 179° altında olmalı.")
        ba = bend_allowance(t, r, th)
        sb = bend_setback(t, r, th, outer)
        mat = A + leg - (2 * sb - ba)
        if code == 1:                                # önce düz A, sonra eğik ayak
            pos = A - sb + ba / 2
            pts = [(0, 0), (A, 0), (A + leg * math.cos(th), leg * math.sin(th))]
            heading, flat = math.degrees(th), leg
        else:                                        # önce eğik ayak, sonra düz A
            pos = leg - sb + ba / 2
            pts = [(0, 0), (leg * math.cos(th), leg * math.sin(th)),
                   (leg * math.cos(th) + A, leg * math.sin(th))]
            heading, flat = 0.0, A
        # Makine Edit OBJ düzeni (ZW_programlar.xlsx Adımlar): kod 1 -> +açı, kod 2 -> −açı
        sgn_bend = 1.0 if code == 1 else -1.0
        return dict(mat=mat, bends=[(pos, sgn_bend * math.degrees(th))], pts=pts, warn=warn,
                    W=math.degrees(th), A=A, flat_last=flat, along=0.0, heading=heading)

    if code == 3:
        # Kod 3 = iki bükümlü şekil: 1. büküm W, 2. büküm (90° − W); iki büküm aynı yöne döner
        # (adım tablosu: 45/45, 15/75, 73/17). A = ilk düz kol, L = toplam yatay, H = toplam dikey.
        #   eğik kol  s = (L − A) / cos W        dik kol  v = H − (L − A)·tan W
        #   Mat = A + s + v − BD(W) − BD(90° − W)            (Dış ölçü: S = r + t)
        # ac = 2 ise A alanına son dik kol (v) girilmiştir:  s = (H − v)/sin W,  ilk düz kol = L − s·cos W.
        # ZW_programlar.xlsx: adım açıları (W, 90−W) olan 12 kodlu programda fark ≤ 0,4 mm (çoğu ≤ 0,1).
        if not 0 < W < 90:
            raise ValueError("Kod 3: açı 0–90° arasında olmalı.")
        th = math.radians(W)
        th2 = math.pi / 2 - th
        if ac == 2:
            v = A
            s = (H - v) / math.sin(th)
            fl = L - s * math.cos(th)
        else:
            fl = A
            s = (L - A) / math.cos(th)
            v = H - (L - A) * math.tan(th)
        if fl < 0 or s <= 0 or v < 0:
            raise ValueError("Kod 3: A, L, H ve W uyumsuz (kol boyu negatif çıktı).")
        ba1, ba2 = bend_allowance(t, r, th), bend_allowance(t, r, th2)
        sb1, sb2 = bend_setback(t, r, th, outer), bend_setback(t, r, th2, outer)
        mat = fl + s + v - (2 * sb1 - ba1) - (2 * sb2 - ba2)
        pos1 = fl - sb1 + ba1 / 2
        pos2 = fl - sb1 + ba1 + (s - sb1 - sb2) + ba2 / 2
        pts = [(0, 0), (fl, 0), (fl + s * math.cos(th), s * math.sin(th)),
               (fl + s * math.cos(th), s * math.sin(th) + v)]
        return dict(mat=mat, bends=[(pos2, math.degrees(th2)), (pos1, W)], pts=pts, warn=warn,
                    W=W, A=fl, flat_last=v, along=s, heading=90.0)

    if code == 4:
        if H <= t:
            raise ValueError(f"|H| ({H:g}) kalınlıktan ({t:g}) büyük olmalı.")
        if abs(A) + abs(L) < 1e-12:
            raise ValueError("A ve L aynı anda sıfır olamaz.")
        if mode == "len":
            W = z_angle_from_len(t, A, L, H, C, calibrated_multi)
            if not 0 < abs(W) <= 90:
                raise ValueError("Z geometri açısı 0-90 derece dışında; işaretli ölçüleri kontrol edin.")
            W = abs(W)
        else:
            if not 0 < W <= 90:
                raise ValueError("Z büküm için açı 0–90° arasında olmalı.")
            if ac == 2:                              # girilen değer C ise A'yı türet
                if calibrated_multi:
                    A = L - A - (H - t) / math.tan(math.radians(W)) - AC2_OFF0 - AC2_OFF1 * (t - SS_T0)
                else:
                    A = L - A - dx_exact(t, H, W)
            elif ac != 1:
                raise ValueError("A veya C alanı 1 ya da 2 olmalı.")
        th = math.radians(W)
        # Excel: Mat = L + (H + e·t)·tan(W/2) − 2·BD,  e = 1 (Dış), 2 (İç). A'dan bağımsız.
        mat = mat_length_kod4(t, r, L, H, W, outer=outer)
        # yan dayama konumları (şerit boyunca)
        ossb = bend_setback(t, r, th, outer)
        ba = bend_allowance(t, r, th)
        bd = 2 * ossb - ba
        dx = dx_exact(t, H, W)
        along = dx * math.cos(th) + H * math.sin(th)  # köşeler arası eğim boyu
        flat = L - A - dx                             # üst düz kısım (C)
        pos1 = A - ossb + ba / 2
        pos2 = pos1 + along - bd
        if flat < ossb:
            warn = "Uyarı: L, A ve eğim boyuna göre çok kısa."
        hc = H - t
        pts = [(0, 0), (A, 0), (A + hc / math.tan(th), hc), (L, hc)]
        # Dik basamakta (türetilen W 90°'ye <= 1° yakın) makine açıyı tam 90,0 gösterir; geometri/Mat.Length
        # türetilen W ile kalır, yalnızca bildirilen büküm açısı yuvarlanır (makine adımlarında 367 kayıt 90,0).
        Wd = 90.0 if (mode == "len" and abs(W - 90.0) <= 1.0) else W
        return dict(mat=mat, bends=[(pos2, -Wd), (pos1, Wd)], pts=pts, warn=warn, W=W, A=A,
                    flat_last=flat, along=along, heading=0.0)

    if code == 5:
        # Arayüzde  A = L1,  L = L2,  H = gövde (Excel: Mat = H + L1 + L2 − 2·BD(90°))
        if abs(A) + abs(L) + abs(H) < 1e-12:
            raise ValueError("L1, L2, H aynı anda sıfır olamaz.")
        th = math.pi / 2
        mat = A + H + L - 2 * bend_deduct(t, r, th, outer)
        rho = r + K_FACTOR * t                       # yan dayama: ekrandan doğrulanmış düzen
        c = 2 - math.pi / 2
        pos1 = (A - t / 2) - rho + math.pi * rho / 4
        pos2 = pos1 + (H - t) - rho * c
        pts = [(0, 0), (A, 0), (A, H), (A - L, H)]
        return dict(mat=mat, bends=[(pos2, 90.0), (pos1, 90.0)], pts=pts, warn=warn,
                    W=90.0, A=A, flat_last=L, along=H, heading=180.0)

    raise ValueError("Bilinmeyen büküm tipi.")


def stage_mode(typ, dim):
    """Hane tipi ve Dim Type'a göre ölçü modu: 'len' (A, L, H...) veya 'angle' (A/C, ..., W)."""
    if typ == 5:
        return "len"
    if typ == 3:
        return "angle"
    return "len" if dim in (0, 1) else "angle"


def straight_stage(typ, d):
    """Açısı 0 olan hane: büküm yok, yalnızca düz parça (Mat = düz boy).
    Kod 1/2: A + C (açı modunda C = eğik ayak boyu, açı 0 -> düz uzanır). Kod 3/4: L."""
    if typ in (1, 2):
        ln = d["A"] + d["C"]
        if ln <= 0:
            raise ValueError("Açı 0 iken A ve C toplamı sıfırdan büyük olmalı.")
    else:
        ln = d["L"]
        if ln <= 0:
            raise ValueError("Açı 0 iken işaretli L toplam düz boyu pozitif olmalı.")
    return dict(mat=ln, bends=[], pts=[(0.0, 0.0), (ln, 0.0)], warn="", W=0.0, A=d["A"],
                flat_last=ln, along=0.0, heading=0.0)


def calc_stages(code, t, r, dim, stages):
    """Tek veya çok haneli kod dizisini, HER HANENİN KENDİ ölçü setiyle hesaplar.

    code   : "4", "41", "44", "142" ...        dim : 0 Dış, 1 İç, 2 Dış Açı, 3 İç Açı
    stages : hane başına dict(ac, A, L, H, C, W)   (W işaretli: negatif = basamak aşağı)
    Döner  : dict(mat, bends [(yan_dayama, açı)], bend_label, pts, bend_idx, warn, verified,
                  stage_mats, joints)
      mat   = hane Mat değerlerinin toplamı
      bends = büküm tablosu sırası (önce 1. hane, sonra sonrakiler; Z'de uzak büküm önce).
              Yan dayama = şerit başından MUTLAK konum:  önceki hanelerin Mat toplamı
              + hane geçiş payı (STAGE_JOINT) + hanenin kendi yerel konumu.
      pts / bend_idx = önizleme çizgisi ve bükümlerin pts içindeki indisleri (hane birleşimleri
              düz ise tek parça çizilir).
    """
    code = str(code).strip()
    if not code or any(ch not in "12345" for ch in code):
        raise ValueError("Kod dizisi yalnızca 1–5 rakamlarından oluşmalı.")
    multi = len(code) > 1
    if multi and (code[0] not in MULTI_FIRST or any(ch not in MULTI_CODES for ch in code[1:])):
        raise ValueError("Çoklu kod: ilk ve devam haneleri 1, 2 veya 4 olmalı.")
    if len(stages) < len(code):
        raise ValueError("Her kod hanesi için ölçü seti gerekli.")
    outer = dim in (0, 2)

    total, joint_sum = 0.0, 0.0
    bends, labels, pts, bend_idx, corrs = [], [], [], [], []
    stage_mats, joints, warns = [], [], []
    px = py = hd = 0.0
    unknown_joint = any_straight = False
    for i, ch in enumerate(code):
        typ, sd = int(ch), stages[i]
        dim_i = int(sd.get("dim", dim))          # hane başına Dim Type (yoksa genel dim)
        outer_i = dim_i in (0, 2)
        mode = stage_mode(typ, dim_i)
        tag = f"Hane {i + 1} ({ch}): " if multi else ""
        W_in = sd.get("W", 0.0)
        H_in = sd.get("H", 0.0)
        # İşaretli ölçüler = büküm yönü (negatif: basamak aşağı / ters yön):
        #   Açı modu  : W < 0 yönü çevirir (kod 4'te H < 0 da çevirir, ikisi birlikte negatifse eski yön)
        #   Dış/İç modu: kod 1, 2 ve 4'te H < 0 yönü çevirir (makine: H=+50 -> +90°, H=-95 -> -90°)
        sg = -1 if (mode == "angle" and W_in < 0) else 1
        if typ in (4, 5) and H_in < 0:
            sg = -sg
        elif typ in (1, 2) and mode == "len" and H_in < 0:
            sg = -1
        d = dict(A=sd.get("A", 0.0), L=sd.get("L", 0.0),
                 H=abs(H_in) if typ in (4, 5) else H_in,     # Z ve U'da geometri |H| ile, yön sg ile
                 C=sd.get("C", 0.0), W=abs(W_in) if mode == "angle" else W_in)
        # Çok haneli kodda 2. ve sonraki hanenin açısı 0 olabilir: büküm yok, hane düz parçadır.
        straight = (i > 0 and mode == "angle" and typ in (1, 2, 3, 4) and abs(W_in) < 1e-9)
        try:
            if straight:
                st = straight_stage(typ, d)
            else:
                st = stage_calc(typ, t, r, outer_i, mode, d, ac=int(sd.get("ac", 1)),
                                calibrated_multi=multi and typ == 4)
        except (ValueError, ZeroDivisionError) as e:
            raise ValueError(tag + (str(e) or "Geçersiz ölçü değeri.")) from None
        if st["warn"]:
            warns.append(tag + st["warn"])
        if straight:
            any_straight = True
        if i > 0:
            if straight:
                j = 0.0                       # bükümü olmayan hanede geçiş payı uygulanacak yer yok
            else:
                j = stage_joint(code[i - 1], ch, t)
                if j is None:
                    j, unknown_joint = 0.0, True
            joint_sum += j
            joints.append(j)
        pos0 = total + joint_sum
        for p, a in st["bends"]:
            no = len(bends) + 1
            cr = (joint_sum if i > 0 else 0.0) + SS_BEND_CORR.get((code, no), 0.0)
            bends.append((p + pos0 + SS_BEND_CORR.get((code, no), 0.0), a * sg))
            corrs.append(cr)
            labels.append(f"{i + 1}·{ch}" if multi else "")
        total += st["mat"]
        stage_mats.append(st["mat"])

        # ---- önizleme çizgisi: hane yerel noktaları, önceki haneden devralınan konum/yöne taşınır
        c, s = math.cos(hd), math.sin(hd)
        G = [(px + x * c - y * sg * s, py + x * s + y * sg * c) for x, y in st["pts"]]
        flat_first = typ != 2                     # kod 2'nin ilk segmenti eğik ayak
        if i > 0 and flat_first:
            pts.pop()                             # bükümsüz birleşim: düz kısımlar tek parça
            add, shift = G[1:], 1
        elif i > 0:
            add, shift = G[1:], 1
        else:
            add, shift = G, 0
        base = len(pts)
        pts.extend(add)
        bend_idx += [base + m - shift for m in range(1, len(G) - 1)]
        px, py = G[-1]
        hd += sg * math.radians(st["heading"])

    # NOT: Bu sıralama sadece Side Stop konumlarına göredir; gerçek makine OBJ/EXE doğruluğu değildir.
    # Makine büküm tablosu şerit ucundaki (en büyük yan dayama) bükümden başlar (2243438.ZW ekranı:
    # 390,6 / 369,2 / 148,2 / 110,0). Bu yüzden tablo büyükten küçüğe sıralanır: 44'te önce 2. hane.
    order = sorted(range(len(bends)), key=lambda k: -bends[k][0])
    bends = [bends[k] for k in order]
    labels = [labels[k] for k in order]
    corrs = [corrs[k] for k in order]

    warn = " ".join(warns)
    verified = True
    if not multi:
        verified = typ in (1, 2, 3, 4, 5)
        if dim in (1, 3) and code == "4" and stage_mode(4, dim) == "len":
            warn += " Kod 4 İç ölçü (A, L, H, C) Excel'de ölçülmedi; İç Açı kuralı uygulandı."
    else:
        verified = False  # OBJ/EXE makine referansıyla doğrulanmadan True olamaz
        if "3" in code:
            verified = False
        if any_straight:
            verified = False
            warn += (" Açısı 0 olan hane düz parça olarak eklendi (Mat = düz boy, geçiş payı yok); "
                     "bu durum ekran/ZW ile doğrulanmadı.")
        if code not in KNOWN_CODES:
            warn += (f" UYARI: Kod {code} için ekran/ZW örneği yok; hane toplamı modeli "
                     "uygulandı ama makinede doğrulanmadı.")
        elif code not in VERIFIED_MULTI:
            warn += (f" Kod {code}: toplam boy örneklerle doğrulandı, ancak hane geçişindeki yan "
                     "dayama payı için ölçüm yok (0 alındı).")
        if not MULTI_T_RANGE[0] <= t <= MULTI_T_RANGE[1] or not all(int(sd.get("dim", dim)) in (0, 2) for sd in stages[:len(code)]):
            verified = False
            warn += (" UYARI: Çok haneli formül t=3–15 mm ve Dış/Dış Açı ölçüsüyle doğrulandı; "
                     "bu ölçüler için sonuç güvenilmez.")
        if unknown_joint:
            verified = False
    if multi and "3" in code:
        verified = False
    # İşaretli uzunluklar mevcut kalibrasyon veri aralığının dışındadır.
    # Bu durumda PLC'ye otomatik aktarımı engelle; hesaplanan pozisyonlar deneysel kalır.
    signed_input = any(any(float(sd.get(k, 0) or 0) < 0 for k in ("A", "L", "C")) for sd in stages[:len(code)])
    if signed_input:
        verified = False
        warn += " UYARI: Negatif A/L/C ile Side Stop ve Angle makine verileriyle doğrulanmadı; PLC aktarımı kapalı (negatif H ve W yön bilgisidir, serbest)."
    if total <= 0:
        verified = False
        warn += " UYARI: Hesaplanan malzeme boyu pozitif değil; geometriyi kontrol edin."
    return dict(mat=total, bends=bends, bend_label=labels, pts=pts, bend_idx=bend_idx,
                warn=warn.strip(), verified=verified, stage_mats=stage_mats, joints=joints, bend_corr=corrs)


def calc(code, t, r, outer, mode, d, ac=1, calibrated_multi=False):
    """Eski arayüz: tüm hanelere AYNI ölçü setini uygular (geriye uyumluluk). Yeni kod için
    calc_stages kullanın (her hane ayrı ölçü)."""
    dim = (0 if mode == "len" else 2) if outer else (1 if mode == "len" else 3)
    sd = dict(ac=ac, A=d["A"], L=d["L"], H=d["H"], C=d["C"], W=d["W"])
    return calc_stages(str(code).strip(), t, r, dim, [sd] * max(len(str(code).strip()), 1))


# Tum_Deneyler.xlsx'ten seçilmiş doğrulama satırları (--test ile denenir):
# (kod, dim, t, r, A, L, H, C, W, L1, L2, ref)  [tüm 150 kod 1/2/4/5 satırı]   dim: Outside/Inside/Outside Angle/Inside Angle
EXCEL_REF = [
    (1, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (1, 'Outside', 5, 5, 50, 150, 40, None, None, None, None, 156.6),
    (1, 'Outside', 5, 5, 50, 250, 40, None, None, None, None, 253.4),
    (1, 'Outside', 5, 5, 50, 100, 100, None, None, None, None, 157.2),
    (1, 'Outside', 5, 5, 50, 100, 200, None, None, None, None, 250),
    (1, 'Outside', 5, 5, 50, 50, 40, None, None, None, None, 81.3),
    (1, 'Outside', 5, 5, 100, 50, 40, None, None, None, None, 125),
    (1, 'Outside', 5, 5, 250, 50, 40, None, None, None, None, 273.6),
    (1, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (1, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (1, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (1, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (1, 'Outside', 5, 10, 50, 100, 40, None, None, None, None, 111.8),
    (1, 'Outside', 5, 10, 50, 150, 40, None, None, None, None, 156.6),
    (1, 'Outside', 5, 10, 50, 250, 40, None, None, None, None, 253.4),
    (1, 'Outside', 5, 10, 50, 100, 100, None, None, None, None, 156.7),
    (1, 'Outside', 5, 10, 50, 100, 200, None, None, None, None, 248.9),
    (1, 'Outside', 5, 10, 50, 50, 40, None, None, None, None, 79.2),
    (1, 'Outside', 5, 10, 100, 50, 40, None, None, None, None, 108.9),
    (1, 'Outside', 5, 10, 250, 50, 40, None, None, None, None, 187.4),
    (1, 'Outside', 10, 5, 50, 100, 40, None, None, None, None, 110),
    (1, 'Outside', 10, 5, 50, 150, 40, None, None, None, None, 155.5),
    (1, 'Outside', 10, 5, 50, 250, 40, None, None, None, None, 252.8),
    (1, 'Outside', 10, 5, 50, 100, 100, None, None, None, None, 153.3),
    (1, 'Outside', 10, 5, 50, 100, 200, None, None, None, None, 245),
    (1, 'Outside', 10, 5, 50, 50, 40, None, None, None, None, 74.7),
    (1, 'Outside', 10, 5, 100, 50, 40, None, None, None, None, 102.2),
    (1, 'Outside', 10, 5, 250, 50, 40, None, None, None, None, 179.4),
    (1, 'Outside', 10, 10, 50, 100, 40, None, None, None, None, 109.7),
    (1, 'Outside', 10, 10, 50, 150, 40, None, None, None, None, 155.4),
    (1, 'Outside', 10, 10, 50, 250, 40, None, None, None, None, 252.7),
    (1, 'Outside', 10, 10, 50, 100, 100, None, None, None, None, 153),
    (1, 'Outside', 10, 10, 50, 100, 200, None, None, None, None, 244.1),
    (1, 'Outside', 10, 10, 50, 50, 40, None, None, None, None, 72.9),
    (1, 'Outside', 10, 10, 100, 50, 40, None, None, None, None, 86.3),
    (1, 'Outside', 10, 10, 250, 50, 40, None, None, None, None, 93.5),
    (1, 'Outside', 12, 12, 50, 100, 40, None, None, None, None, 109),
    (1, 'Outside', 12, 12, 50, 150, 40, None, None, None, None, 155.1),
    (1, 'Outside', 12, 12, 50, 250, 40, None, None, None, None, 252.6),
    (1, 'Outside', 12, 12, 50, 100, 100, None, None, None, None, 151.2),
    (1, 'Outside', 12, 12, 50, 100, 200, None, None, None, None, 241.6),
    (1, 'Outside', 12, 12, 50, 50, 40, None, None, None, None, 69.4),
    (1, 'Outside', 12, 12, 100, 50, 40, None, None, None, None, 70.7),
    (1, 'Outside', 12, 12, 250, 50, 40, None, None, None, None, 21.3),
    (2, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (2, 'Outside', 5, 5, 50, 150, 40, None, None, None, None, 156.6),
    (2, 'Outside', 5, 5, 50, 250, 40, None, None, None, None, 253.4),
    (2, 'Outside', 5, 5, 50, 100, 100, None, None, None, None, 157.2),
    (2, 'Outside', 5, 5, 50, 100, 200, None, None, None, None, 250),
    (2, 'Outside', 5, 5, 50, 50, 40, None, None, None, None, 81.3),
    (2, 'Outside', 5, 5, 100, 50, 40, None, None, None, None, 125),
    (2, 'Outside', 5, 5, 250, 50, 40, None, None, None, None, 273.6),
    (2, 'Outside', 10, 10, 50, 100, 40, None, None, None, None, 109.7),
    (2, 'Outside', 10, 10, 50, 150, 40, None, None, None, None, 155.4),
    (2, 'Outside', 10, 10, 50, 250, 40, None, None, None, None, 252.7),
    (2, 'Outside', 10, 10, 50, 100, 100, None, None, None, None, 153),
    (2, 'Outside', 10, 10, 50, 100, 200, None, None, None, None, 244.1),
    (2, 'Outside', 10, 10, 50, 50, 40, None, None, None, None, 72.9),
    (2, 'Outside', 10, 10, 100, 50, 40, None, None, None, None, 86.3),
    (2, 'Outside', 10, 10, 250, 50, 40, None, None, None, None, 93.5),
    (2, 'Outside', 5, 10, 50, 100, 40, None, None, None, None, 111.8),
    (2, 'Outside', 5, 10, 50, 150, 40, None, None, None, None, 156.6),
    (2, 'Outside', 5, 10, 50, 250, 40, None, None, None, None, 253.4),
    (2, 'Outside', 5, 10, 50, 100, 100, None, None, None, None, 156.7),
    (2, 'Outside', 5, 10, 50, 100, 200, None, None, None, None, 248.9),
    (2, 'Outside', 5, 10, 50, 50, 40, None, None, None, None, 79.2),
    (2, 'Outside', 5, 10, 100, 50, 40, None, None, None, None, 108.9),
    (2, 'Outside', 5, 10, 250, 50, 40, None, None, None, None, 187.4),
    (2, 'Outside', 10, 5, 50, 100, 40, None, None, None, None, 110),
    (2, 'Outside', 10, 5, 50, 150, 40, None, None, None, None, 155.5),
    (2, 'Outside', 10, 5, 50, 250, 40, None, None, None, None, 252.8),
    (2, 'Outside', 10, 5, 50, 100, 100, None, None, None, None, 153.3),
    (2, 'Outside', 10, 5, 50, 100, 200, None, None, None, None, 245),
    (2, 'Outside', 10, 5, 50, 50, 40, None, None, None, None, 74.7),
    (2, 'Outside', 10, 5, 100, 50, 40, None, None, None, None, 102.2),
    (2, 'Outside', 10, 5, 250, 50, 40, None, None, None, None, 179.4),
    (4, 'Outside', 5, 5, 50, 150, 30, 50, None, None, None, 155.6),
    (4, 'Outside', 5, 5, 50, 200, 30, 50, None, None, None, 203),
    (4, 'Outside', 5, 5, 50, 250, 30, 50, None, None, None, 252.1),
    (4, 'Outside', 5, 5, 50, 150, 60, 50, None, None, None, 173.2),
    (4, 'Outside', 5, 5, 50, 150, 100, 50, None, None, None, 204.5),
    (4, 'Outside', 5, 5, 50, 100, 30, 50, None, None, None, 117.6),
    (4, 'Outside', 5, 5, 100, 250, 30, 50, None, None, None, 253),
    (4, 'Outside', 5, 5, 50, 150, 30, 80, None, None, None, 160.4),
    (4, 'Outside', 5, 10, 50, 150, 30, 50, None, None, None, 155.5),
    (4, 'Outside', 5, 10, 50, 200, 30, 50, None, None, None, 202.8),
    (4, 'Outside', 5, 10, 50, 250, 30, 50, None, None, None, 251.9),
    (4, 'Outside', 5, 10, 50, 150, 60, 50, None, None, None, 172.6),
    (4, 'Outside', 5, 10, 50, 150, 100, 50, None, None, None, 203.5),
    (4, 'Outside', 5, 10, 50, 100, 30, 50, None, None, None, 113.5),
    (4, 'Outside', 5, 10, 100, 250, 30, 50, None, None, None, 252.8),
    (4, 'Outside', 5, 10, 50, 150, 30, 80, None, None, None, 159.8),
    (4, 'Outside', 10, 5, 50, 150, 30, 50, None, None, None, 153.4),
    (4, 'Outside', 10, 5, 50, 200, 30, 50, None, None, None, 201.7),
    (4, 'Outside', 10, 5, 50, 250, 30, 50, None, None, None, 251.1),
    (4, 'Outside', 10, 5, 50, 150, 60, 50, None, None, None, 169.3),
    (4, 'Outside', 10, 5, 50, 150, 100, 50, None, None, None, 198.7),
    (4, 'Outside', 10, 5, 50, 100, 30, 50, None, None, None, 109.4),
    (4, 'Outside', 10, 5, 100, 250, 30, 50, None, None, None, 251.7),
    (4, 'Outside', 10, 5, 50, 150, 30, 80, None, None, None, 156.8),
    (4, 'Outside', 10, 10, 50, 150, 30, 50, None, None, None, 153.1),
    (4, 'Outside', 10, 10, 50, 200, 30, 50, None, None, None, 201.5),
    (4, 'Outside', 10, 10, 50, 250, 30, 50, None, None, None, 250.9),
    (4, 'Outside', 10, 10, 50, 150, 60, 50, None, None, None, 168.4),
    (4, 'Outside', 10, 10, 50, 150, 100, 50, None, None, None, 198.2),
    (4, 'Outside', 10, 10, 50, 100, 30, 50, None, None, None, 105.7),
    (4, 'Outside', 10, 10, 100, 250, 30, 50, None, None, None, 251.5),
    (4, 'Outside', 10, 10, 50, 150, 30, 80, None, None, None, 156),
    (1, 'Inside', 5, 5, 50, 100, 40, None, None, None, None, 115.4),
    (1, 'Inside', 5, 5, 50, 50, 40, None, None, None, None, 91.3),
    (1, 'Inside', 5, 5, 50, 150, 100, None, None, None, None, 193),
    (1, 'Inside Angle', 5, 5, 50, None, None, 100, 30, None, None, 151.1),
    (1, 'Inside Angle', 5, 5, 50, None, None, 100, 45, None, None, 151.5),
    (1, 'Inside Angle', 5, 5, 50, None, None, 100, 90, None, None, 151.3),
    (1, 'Inside Angle', 5, 5, 50, None, None, 100, 135, None, None, 143),
    (1, 'Outside Angle', 5, 5, 50, None, None, 100, 30, None, None, 148.5),
    (1, 'Outside Angle', 5, 5, 50, None, None, 100, 45, None, None, 147.4),
    (1, 'Outside Angle', 5, 5, 50, None, None, 100, 90, None, None, 141.3),
    (1, 'Outside Angle', 5, 5, 50, None, None, 100, 135, None, None, 118.9),
    (2, 'Inside', 5, 5, 50, 100, 40, None, None, None, None, 115.4),
    (2, 'Inside', 5, 5, 50, 50, 40, None, None, None, None, 91.3),
    (2, 'Inside', 5, 5, 50, 150, 100, None, None, None, None, 193),
    (2, 'Inside Angle', 5, 5, 50, None, None, 100, 30, None, None, 151.1),
    (2, 'Inside Angle', 5, 5, 50, None, None, 100, 45, None, None, 151.5),
    (2, 'Inside Angle', 5, 5, 50, None, None, 100, 90, None, None, 151.3),
    (2, 'Inside Angle', 5, 5, 50, None, None, 100, 135, None, None, 143),
    (2, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (2, 'Outside', 5, 5, 50, 100, 40, None, None, None, None, 111.9),
    (2, 'Outside Angle', 5, 5, 50, None, None, 100, 30, None, None, 148.5),
    (2, 'Outside Angle', 5, 5, 50, None, None, 100, 45, None, None, 147.4),
    (2, 'Outside Angle', 5, 5, 50, None, None, 100, 90, None, None, 141.3),
    (2, 'Outside Angle', 5, 5, 50, None, None, 100, 135, None, None, 118.9),
    (4, 'Inside Angle', 5, 5, 50, 150, 30, None, 45, None, None, 161.4),
    (4, 'Inside Angle', 5, 5, 50, 150, 30, None, 60, None, None, 165.1),
    (4, 'Inside Angle', 5, 5, 50, 250, 30, None, 45, None, None, 261.4),
    (4, 'Inside Angle', 5, 5, 100, 150, 30, None, 45, None, None, 161.4),
    (4, 'Outside', 5, 5, 50, 150, 30, 50, None, None, None, 155.6),
    (4, 'Outside', 5, 5, 50, 150, 30, 50, None, None, None, 155.6),
    (4, 'Outside Angle', 5, 5, 50, 150, 30, None, 45, None, None, 159.3),
    (4, 'Outside Angle', 5, 5, 50, 150, 30, None, 60, None, None, 162.2),
    (4, 'Outside Angle', 5, 5, 50, 250, 30, None, 45, None, None, 259.3),
    (4, 'Outside Angle', 5, 5, 100, 150, 30, None, 45, None, None, 159.3),
    (5, 'Inside', 5, 5, None, None, 50, None, None, 40, 80, 172.6),
    (5, 'Inside', 5, 5, None, None, 100, None, None, 40, 80, 222.6),
    (5, 'Inside', 5, 5, None, None, 50, None, None, 40, 150, 242.6),
    (5, 'Inside', 5, 5, None, None, 50, None, None, 100, 80, 232.6),
    (5, 'Outside', 5, 5, None, None, 50, None, None, 40, 80, 152.6),
    (5, 'Outside', 5, 5, None, None, 100, None, None, 40, 80, 202.6),
    (5, 'Outside', 5, 5, None, None, 50, None, None, 40, 150, 222.6),
    (5, 'Outside', 5, 5, None, None, 50, None, None, 100, 80, 212.6),
]


def excel_row_mat(row):
    """EXCEL_REF satırının Mat.Length değerini bu dosyadaki formüllerle hesaplar."""
    code, dim, t, r, A, L, H, C, W, L1, L2, ref = row
    outer = dim.startswith("Outside")
    ang = dim.endswith("Angle")
    if code == 5:
        d = dict(A=L1, L=L2, H=H, C=0.0, W=0.0)
        return stage_calc(5, t, r, outer, "len", d)["mat"]
    if code in (1, 2):
        d = dict(A=A or 0.0, L=L or 0.0, H=H or 0.0, C=C or 0.0, W=W or 0.0)
        return stage_calc(code, t, r, outer, "angle" if ang else "len", d)["mat"]
    d = dict(A=A or 0.0, L=L, H=H, C=C or 0.0, W=W or 0.0)
    return stage_calc(4, t, r, outer, "angle" if ang else "len", d)["mat"]


def self_test():
    ok = True
    # U kanal (kod 5) yan dayama aralığı: ekran 120,7
    st = stage_calc(5, 10, 5, True, "len", dict(A=130, L=125, H=135, C=0, W=0))
    dd = st["bends"][0][0] - st["bends"][1][0]
    print(f"U kanal aralığı : {dd:.2f} (ekran: 120,7)")
    ok &= abs(dd - 120.7) < 0.1
    # tek Z (kod 4): ekran görüntülerinden kontrol edilen Mat.Length değerleri
    m843 = mat_length_kod4(8, 8, 826, 54, 45, True)
    print(f"Kod 4 / 843,2 örneği: {m843:.3f} (makine: 843,2)")
    ok &= abs(m843 - 843.2) < 0.1
    # tek Z (kod 4): üç ekran görüntüsü (Dış Açı, ±0,5 mm)
    for ad, (t, r, A, L, H, W, ref) in {
        "NEW.ZW      ": (10, 5, 112, 1299.5, 20, 28, 1301.4),
        "2U1-2U2.ZW  ": (5, 5, 82, 152, 95, 60.9, 202.2),
        "16.ZW       ": (12, 15, 100, 500, 206, 45, 577.8),
    }.items():
        s = stage_calc(4, t, r, True, "angle", dict(A=A, L=L, H=H, C=0, W=W))
        print(f"{ad} Mat.Len  : {s['mat']:.2f} (ekran: {ref})")
        ok &= abs(s["mat"] - ref) <= 0.6
    s = stage_calc(4, 10, 5, True, "angle", dict(A=112, L=1299.5, H=20, C=0, W=28))
    for i, (p, a) in enumerate(s["bends"], 1):
        print(f"  büküm {i}: yan dayama {p:.1f}  açı {a:+.1f}")
    # açı <-> C dönüşümü tutarlı mı (tek Z)
    W0 = 45.0
    C0 = 500 - 100 - z_run(12, 206, W0)
    ok &= abs(z_angle_from_len(12, 100, 500, 206, C0) - W0) < 1e-6
    # Excel doğrulama satırları
    errs = [excel_row_mat(row) - row[-1] for row in EXCEL_REF]
    rms = math.sqrt(sum(e * e for e in errs) / len(errs))
    mx = max(abs(e) for e in errs)
    print(f"Excel satırları : {len(errs)} adet, RMS {rms:.2f} mm, en büyük sapma {mx:.2f} mm")
    ok &= mx < 0.8 and rms < 0.25
    # ---- çok haneli kodlar: her hane kendi ölçü setiyle, toplam = hane Mat toplamı
    # 41 = Z(W=90°) + 90° büküm (5418933-2 / -3 / -5, t=5, r=5; ZW: 668,84 / 558,84 / 548,84)
    for ad, (Lz, Hz, A2, L2, ref) in {"5418933-2": (292, 100, 100, 198, 668.8429),
                                      "5418933-3": (232, 99, 100, 149, 558.8429),
                                      "5418933-5": (243, 142, 50, 135, 548.8429)}.items():
        st41 = [dict(ac=1, A=100, L=Lz, H=Hz, C=0, W=90), dict(ac=1, A=A2, L=0, H=0, C=L2, W=90)]
        m41 = calc_stages("41", 5, 5, 2, st41)["mat"]
        print(f"{ad} (41)  : {m41:.2f} (ZW: {ref})")
        ok &= abs(m41 - ref) < 0.5
    # 44 = Z + Z: ekran fotoğrafı (2243438) ve ZW kayıtları (Mat.Length, ±3,5 mm)
    zz = [("2243438 (ekran)", 10, 5, (2, 65, 200, 44, -60), (1, 152, 844, 29.5, 60), 1068.6),
          ("2243438 (ZW)   ", 10, 5, (2, 65, 200, 44, -60), (1, 152, 844, 29.5, 60), 1068.6),
          ("2242081 (ekran)", 6, 5, (2, 85, 200, 44, -60), (1, 127, 601, 21.5, 50), 826.1),
          ("2233532        ", 10, 5, (2, 75, 200, 44, -60), (1, 137, 822, 26.5, 54), 1044.8),
          ("2236402        ", 6, 5, (2, 85, 200, 44, -60), (1, 127, 691, 29, 60), 920.7),
          ("2238332        ", 12, 10, (2, 110, 200, 44, -60), (1, 120, 900, 24, 60), None),
          ("2235453        ", 6, 5, (2, 85, 200, 44, -60), (1, 107, 671, 25, 60), 898.9)]
    for ad, t_, r_, s1, s2, ref in zz:
        sts = [dict(ac=s[0], A=s[1], L=s[2], H=s[3], C=0, W=s[4]) for s in (s1, s2)]
        res = calc_stages("44", t_, r_, 2, sts)
        if ref is None:
            continue
        print(f"{ad} (44)  : {res['mat']:.2f} (makine: {ref})  fark {ref - res['mat']:+.2f}")
        ok &= abs(res["mat"] - ref) < 3.5
    # büküm tablosu: 44 -> 4 büküm, konumlar hane sırasıyla artar, Z'de uzak büküm önce
    r44 = calc_stages("44", 10, 5, 2, [dict(ac=2, A=65, L=200, H=44, C=0, W=-60),
                                       dict(ac=1, A=152, L=844, H=29.5, C=0, W=60)])
    print("44 büküm tablosu:", ", ".join(f"{i}) {p:.1f} / {a:+.0f}°"
                                        for i, (p, a) in enumerate(r44["bends"], 1)))
    ps = [p for p, _ in r44["bends"]]
    ok &= len(ps) == 4 and ps == sorted(ps, reverse=True)             # büyükten küçüğe
    ok &= min(ps[:2]) > max(ps[2:])                                   # önce 2. hane (makine ekranı)
    ok &= [a for _, a in r44["bends"]] == [-60, 60, 60, -60]          # ekran: -60/+60/+60/-60
    for (p_, a_), (pm, am) in zip(r44["bends"], [(390.6, -60), (369.2, 60), (148.2, 60), (110.0, -60)]):
        ok &= abs(p_ - pm) < 2.0 and a_ == am                         # 2243438.ZW ekran değerleri
    ok &= len(r44["bend_idx"]) == 4 and not r44["verified"]    # çoklu kod PLC onayı bilerek kapalı (VERIFIED_MULTI boş)
    # 3 haneli kod: 444 -> 6 büküm, ilk haneden sonra bükümler ileri gider
    s3 = [dict(ac=1, A=60, L=249.8, H=60, C=0, W=60)] * 3
    r444 = calc_stages("444", 5, 5, 2, s3)
    ok &= len(r444["bends"]) == 6 and not r444["verified"]
    # 14 (düz+eğik, sonra Z) -> 1 + 2 büküm
    r14 = calc_stages("14", 5, 5, 0, [dict(ac=1, A=50, L=100, H=40, C=0, W=0),
                                      dict(ac=1, A=50, L=250, H=40, C=60, W=0)])
    ok &= len(r14["bends"]) == 3
    # eski tek-set arayüzü hâlâ çalışıyor
    # 2. hane açısı 0: büküm yok, düz parça; 1. hane değişmez
    rz = calc_stages("44", 10, 5, 2, [dict(ac=2, A=65, L=200, H=44, C=0, W=-60),
                                      dict(ac=1, A=152, L=844, H=29.5, C=0, W=0)])
    z1 = calc_stages("4", 10, 5, 2, [dict(ac=2, A=65, L=200, H=44, C=0, W=-60)])
    print(f"44 (2. hane W=0): Mat={rz['mat']:.2f} = {z1['mat']:.2f} + 844 ; büküm sayısı {len(rz['bends'])}")
    ok &= len(rz["bends"]) == 2 and abs(rz["mat"] - (z1["mat"] + 844)) < 1e-6
    ok &= rz["bends"] == r44["bends"][2:] and rz["joints"] == [0.0] and not rz["verified"]   # = 44'ün 1. hanesi
    # 0 açı yalnız 2. ve sonraki hanede serbest; tek kodda hata vermeye devam eder
    try:
        calc_stages("4", 10, 5, 2, [dict(ac=1, A=65, L=200, H=44, C=0, W=0)])
        ok = False
    except ValueError:
        pass
    ok &= abs(calc("4", 10, 5, True, "angle", dict(A=112, L=1299.5, H=20, C=0, W=28))["mat"] - 1301.4) <= 0.6
    # negatif H / W = yön: Z (Dış, A/L/H/C), Z (Dış Açı), kod 1
    zp = calc_stages("4", 10, 5, 0, [dict(ac=1, A=100, L=500, H=44, C=0, W=0)])
    zn = calc_stages("4", 10, 5, 0, [dict(ac=1, A=100, L=500, H=-44, C=0, W=0)])
    ok_h = (abs(zp["mat"] - zn["mat"]) < 1e-9 and [p_ for p_, _ in zp["bends"]] == [p_ for p_, _ in zn["bends"]]
            and [a_ for _, a_ in zn["bends"]] == [-a_ for _, a_ in zp["bends"]]
            and min(y for _, y in zn["pts"]) < 0 <= min(y for _, y in zp["pts"]) and zn["verified"])
    ap = calc_stages("4", 10, 5, 2, [dict(ac=1, A=100, L=500, H=44, C=0, W=60)])
    an = calc_stages("4", 10, 5, 2, [dict(ac=1, A=100, L=500, H=44, C=0, W=-60)])
    ah = calc_stages("4", 10, 5, 2, [dict(ac=1, A=100, L=500, H=-44, C=0, W=60)])
    ok_w = (abs(ap["mat"] - an["mat"]) < 1e-9 and [a_ for _, a_ in an["bends"]] == [-a_ for _, a_ in ap["bends"]]
            and ah["bends"] == an["bends"])
    c1p = calc_stages("1", 10, 5, 0, [dict(ac=1, A=91, L=91, H=50, C=0, W=0)])
    c1n = calc_stages("1", 10, 5, 0, [dict(ac=1, A=91, L=91, H=-50, C=0, W=0)])
    ok_1 = (abs(c1p["mat"] - c1n["mat"]) < 1e-9 and c1n["bends"][0][1] == -c1p["bends"][0][1]
            and c1n["bends"][0][0] == c1p["bends"][0][0])
    print("Negatif H/W yönü :", "tamam" if ok_h and ok_w and ok_1 else f"HATALI (H {ok_h}, W {ok_w}, kod1 {ok_1})")
    ok &= ok_h and ok_w and ok_1
    ok &= parse("\u221295,5") == -95.5 and parse("+12") == 12.0
    # 2510984.ZW (kod 442, H=-45 dahil): formül, makine adım listesiyle karşılaştırılır (arama tablosu yok).
    # Makine sırası şerit ucundan: 75,6/496,3/133,3/111,3/36,3/125,6; model hane 1'den başlar (ters sıra).
    s442 = [dict(ac=1, A=130, L=185, H=-45, C=55, W=0), dict(ac=1, A=60, L=260, H=142, C=200, W=0),
            dict(ac=1, A=80, L=80, H=300, C=0, W=0)]
    r442 = calc_stages("442", 5, 5, 0, s442)
    m442 = [125.6, 36.3, 111.3, 133.3, 496.3, 75.6]                  # makine, hane 1'den başlayan sıra
    from editobj import table_from_result as _tfr
    o442 = [r_["obj"] for r_ in _tfr(r442).rows[:6]]
    dev = max(abs(a_ - b_) for a_, b_ in zip(o442, m442))
    ok_442 = (sorted(abs(a_) for _, a_ in r442["bends"]) == [90.0] * 5 and abs(r442["mat"] - 978.4) < 1.0 and dev < 5.0)
    print("2510984.ZW       :", "tamam" if ok_442 else "HATALI", f"Mat {r442['mat']:.2f} (makine 978,4); en büyük OBJ sapması {dev:.1f} mm")
    ok &= bool(ok_442)
    # Edit OBJ tablosu (makine düzeni): OP'deki Side-Stop listesinin ters sırası, OBJ >= 0, son satır = kalan düz parça
    try:
        from editobj import table_from_result
        tb = table_from_result(r44)
        rws = tb.rows[:tb.used()]
        ok_tb = (len(rws) == 5 and all(r_["obj"] >= 0 for r_ in rws)
                 and [r_["ang"] for r_ in rws] == [-60, 60, 60, -60, 0]
                 and all(rws[i]["exe"] < rws[i + 1]["exe"] for i in range(4))
                 and abs(rws[-1]["exe"] - r44["mat"]) < 0.06 and rws[-1]["stk"] == 999.0)
        print("Edit OBJ tablosu :", "tamam" if ok_tb else "HATALI", [round(r_["exe"], 1) for r_ in rws])
        ok &= ok_tb
    except ImportError:
        print("Edit OBJ tablosu : atlandı (editobj.py bulunamadı)")
    print("TEST", "GEÇTİ" if ok else "KALDI")
    return ok


# ----------------------------------------------------------------- arayüz
def parse(s):
    s = s.strip().replace("\u2212", "-").replace("\u2013", "-")      # −, – -> -
    if s.startswith("+"):
        s = s[1:]
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def fmt(v, thousands=False):
    s = f"{v:,.1f}" if thousands else f"{v:.1f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_signed(v):
    """İşaretli gösterim: +4,2 / −3,1 / 0,0"""
    s = fmt(abs(v))
    if round(abs(v), 1) == 0:
        return "0,0"
    return ("+" if v > 0 else "−") + s


def fit_side_stop(path):
    """Makine Side-Stop ölçümlerinden hane geçiş payını (işaretli) uydurur.

    path: JSON listesi. Her kayıt:
      {"code": "44", "t": 10, "r": 5, "dim": 2,   (side_stops: ekrandaki sırayla, büyükten küçüğe)
       "stages": [{"ac":2,"a":65,"l":200,"h":44,"ang":-60,"c":0}, {...}],
       "side_stops": [m1, m2, m3, m4]}          # makinedeki Side-Stop değerleri, tablo sırasıyla
    Modelde STAGE_JOINT geçici sıfırlanır; kalan fark  d = makine − model  yalnızca geçişten sonraki
    bükümlerde  d ≈ j0 + j1·(t − SS_T0)  olarak uydurulur (RMS'i en aza indiren en küçük kareler).
    Ayrıca her büküm için kalan ortalama sapma SS_BEND_CORR olarak önerilir."""
    global STAGE_JOINT, STAGE_JOINT_T, SS_BEND_CORR
    with open(path, encoding="utf-8") as f:
        recs = json.load(f)
    sj, sjt, sbc = STAGE_JOINT, STAGE_JOINT_T, SS_BEND_CORR
    STAGE_JOINT, STAGE_JOINT_T, SS_BEND_CORR = (
        {k: 0.0 for k in sj}, {}, {})
    rows, first = {}, []            # geçiş -> [(t, d, ...)] ; 1. hane sapmaları
    try:
        for rc in recs:
            code = rc["code"]
            stg = [dict(ac=int(x.get("ac", 1)), A=x["a"], L=x["l"], H=x["h"], C=x.get("c", 0.0),
                        W=x["ang"]) for x in rc["stages"]]
            res = calc_stages(code, rc["t"], rc["r"], rc.get("dim", 2), stg)
            for n, (m, (p, _), lab) in enumerate(zip(rc["side_stops"], res["bends"],
                                                     res["bend_label"])):
                k = int(lab.split("·")[0]) - 1          # bükümün hanesi (tablo büyükten küçüğe)
                if k >= 1:
                    tr = code[k - 1], code[k]
                    rows.setdefault(tr, []).append((rc["t"], m - p, code, n + 1))
                else:
                    first.append((rc["t"], m - p, code, n + 1))
    finally:
        STAGE_JOINT, STAGE_JOINT_T, SS_BEND_CORR = sj, sjt, sbc
    for tr, lst in rows.items():
        n = len(lst)
        ts = [x[0] - SS_T0 for x in lst]
        ds = [x[1] for x in lst]
        mt, md = sum(ts) / n, sum(ds) / n
        vt = sum((x - mt) ** 2 for x in ts)
        j1 = sum((x - mt) * (y - md) for x, y in zip(ts, ds)) / vt if vt > 1e-9 else 0.0
        j0 = md - j1 * mt
        rms0 = math.sqrt(sum(d * d for d in ds) / n)
        rms1 = math.sqrt(sum((d - (j0 + j1 * x)) ** 2 for x, d in zip(ts, ds)) / n)
        print(f"Geçiş {tr}: {n} ölçüm | STAGE_JOINT[{tr}] = {j0:+.2f}, STAGE_JOINT_T[{tr}] = {j1:+.3f}"
              f"  (RMS {rms0:.2f} -> {rms1:.2f} mm)")
    if first:
        print("1. hane bükümleri (geçiş payı yok) makine − model: " +
              ", ".join(f"{x[2]}#{x[3]} {x[1]:+.2f}" for x in first))
    print("Bu değerleri anasayfa.py'deki STAGE_JOINT ve STAGE_JOINT_T sözlüklerine yazın.")


def run_gui(start_program=None):
    import tkinter as tk
    from tkinter import ttk
    from ekran import olcek_uygula, tam_ekran
    BG, FIELD, WHITE = "#dfeaee", "#f7e3b4", "#ffffff"
    RED, BLUE, YELLOW = "#d11111", "#1a1acb", "#f2e200"
    GREEN = "#1a7a2a"
    ICONS = {
        1: [(10, 58), (50, 58), (86, 28)],
        2: [(12, 60), (48, 30), (90, 30)],
        3: [(10, 62), (44, 62), (68, 38), (68, 14)],      # düz kol, W eğik kol, dik kol (W + (90°−W))
        4: [(10, 62), (44, 62), (68, 36), (92, 36)],
        5: [(20, 22), (80, 22), (80, 60), (24, 60)],
    }
    SHAPES = {
        4: [(60, 100), (150, 100), (190, 60), (300, 60), (300, 76), (200, 76), (160, 116), (60, 116)],
        1: [(60, 100), (180, 100), (280, 40), (292, 54), (190, 116), (60, 116)],
        2: [(60, 116), (140, 116), (190, 60), (300, 60), (300, 76), (200, 76), (150, 132), (60, 132)],
        3: [(60, 120), (168, 120), (243, 64), (243, 30), (227, 30), (227, 56), (162, 104), (60, 104)],
        5: [(80, 30), (250, 30), (250, 120), (80, 120), (80, 104), (234, 104), (234, 46), (80, 46)],
    }

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.k = olcek_uygula(self)          # ekrana göre ölçek
            self._prev_args = None
            self.columnconfigure(2, weight=1)    # sağ sütun büyüsün
            self.rowconfigure(1, weight=1)
            self.title("Bara Büküm Programı")
            self.configure(bg=BG, padx=12, pady=10)
            self.type = 4                  # SEÇİLİ hanenin tipi
            self._mode = "angle"           # SEÇİLİ hanenin ölçü modu
            self.stages = [dict(ac="1", a="112,0", l="1.299,5", h="20,0", ang="28,0",
                                c="0,0", mode="angle")]   # hane başına ölçü seti
            self.sel = 0                   # imleç: seçili hane (0 tabanlı)
            self.ref = None
            self.vars, self.labels = {}, {}
            self._busy = False
            self._multi = False
            self._plc_busy = False      # PLC gönderimi sürerken ikinci gönderimi engeller
            self.result = None          # son hesaplanan sonuç (F9 kaydı / PLC için)
            self.unverified = False     # True: sonuç doğrulanmadı, PLC'ye gönderilmez
            self._code_confirmed = False      # F2 sonrası kod onaylandı mı
            self._bend_inputs_shown = False   # Sol yön tuşu ile alt ölçü alanları açıldı mı

            tk.Label(self, text="Current Program:", bg=BG, font=("Segoe UI", 12, "bold")
                     ).grid(row=0, column=1, sticky="w")
            self.prog = tk.Label(self, text="", bg=BG, fg=RED, font=("Segoe UI", 16, "bold"))
            self.prog.grid(row=0, column=2, sticky="w", padx=20)
            self.preset = ttk.Combobox(self, values=list(PRESETS), state="readonly", width=16)
            self.preset.grid(row=0, column=0, sticky="w")
            self.preset.bind("<<ComboboxSelected>>", lambda e: self.load_preset(self.preset.get()))

            left = tk.Frame(self, bg=BG)
            left.grid(row=1, column=0, rowspan=2, sticky="n", padx=(0, 10))
            self.type_canvas = {}
            for i in range(1, 6):
                c = tk.Canvas(left, width=100, height=80, bg=WHITE, highlightthickness=2,
                              highlightbackground="#999")
                c.pack(pady=3)
                c.create_text(10, 10, text=str(i), fill=RED, font=("Georgia", 13, "bold"))
                c.create_line(*sum(ICONS[i], ()), width=4, fill="#222", joinstyle="round")
                c.bind("<Button-1>", lambda e, n=i: self.set_type(n))
                self.type_canvas[i] = c

            mid = tk.Frame(self, bg=BG)
            mid.grid(row=1, column=1, sticky="nw")
            self.matlen = tk.StringVar(value="")
            tk.Label(mid, text="Mat.Length [mm]", bg=BG, font=("Segoe UI", 11),
                     anchor="w", width=20).grid(row=0, column=0, sticky="w", pady=2)
            self.mat_e = tk.Entry(mid, textvariable=self.matlen, state="readonly",
                                  justify="right", width=14, font=("Segoe UI", 11, "bold"),
                                  readonlybackground=WHITE)
            self.mat_e.grid(row=0, column=1, sticky="e")
            self.mat_e.bind("<KeyRelease>", lambda e: self.render())
            self.add_row(mid, 1, "Thickness [mm]", "t", "10,0", FIELD)
            self.add_row(mid, 2, "Width [mm]", "w", "80,0", FIELD)
            self.add_row(mid, 3, "Tool [mm]", "tool", "63,0", FIELD)
            self.add_row(mid, 4, "Radius [mm]", "r", "5,0", FIELD)
            self.add_row(mid, 5, "Mat. [1..5]", "mat", "1", FIELD, width=4)
            self.matlabel = tk.Label(mid, bg=BG, text="1– bakır")
            self.matlabel.grid(row=5, column=2, sticky="w", padx=6)

            self.diagram = tk.Canvas(mid, width=int(420 * self.k), height=int(150 * self.k),
                                     bg=WHITE, highlightthickness=1, highlightbackground="#999")
            self.diagram.grid(row=6, column=0, columnspan=3, pady=8, sticky="w")

            self.add_row(mid, 7, "A or C (1/2)", "ac", "1", WHITE, width=4)
            self.add_row(mid, 8, "Length A/C[mm]", "a", "112,0", WHITE)
            self.add_row(mid, 9, "Length L[mm]", "l", "1.299,5", WHITE)
            self.add_row(mid, 10, "Height H[mm]", "h", "20,0", WHITE)
            self.add_row(mid, 11, "Angle W[deg]", "ang", "28,0", WHITE)
            self.add_row(mid, 12, "Length C[mm]", "c", "0,0", WHITE)

            right = tk.Frame(self, bg=BG)
            right.grid(row=1, column=2, sticky="nsew", padx=20)
            tk.Label(right, text="Code String", bg=BG, font=("Segoe UI", 11)).pack(anchor="w")
            self.code = tk.StringVar(value="4")
            self.code_entry = tk.Entry(right, textvariable=self.code, width=12, fg=BLUE,
                                       font=("Segoe UI", 13, "bold"), state="normal")
            self.code_entry.pack(anchor="w", pady=(0, 8))
            self.code_entry.bind("<Return>", self._on_code_enter)
            self.code_entry.bind("<KP_Enter>", self._on_code_enter)
            self.code.trace_add("write", self.on_code)
            hf = tk.Frame(right, bg=BG)
            hf.pack(anchor="w", pady=(0, 6))
            tk.Label(hf, text="Stage (cursor):", bg=BG, font=("Segoe UI", 10)).pack(side="left")
            self.chips = tk.Frame(hf, bg=BG)
            self.chips.pack(side="left", padx=4)
            tk.Label(hf, text="[Alt+←/→]", bg=BG, fg="#555", font=("Segoe UI", 9)
                     ).pack(side="left")

            box = tk.LabelFrame(right, text="Dim Type", bg=BG, fg="#1a2a7a",
                                font=("Segoe UI", 10, "bold"), padx=8, pady=2)
            box.pack(anchor="w", fill="x")
            self.dim = tk.IntVar(value=2)
            self.dim.trace_add("write", self.on_dim)
            for i, name in enumerate(["Outside [ALT+1]", "Inside [ALT+2]", "Outside Angle [ALT+3]", "Inside Angle [ALT+4]"]):
                tk.Radiobutton(box, text=name, variable=self.dim, value=i, bg=BG,
                               activebackground=BG, font=("Segoe UI", 10)).pack(anchor="w")
                self.bind(f"<Alt-Key-{i+1}>", lambda e, v=i: self.dim.set(v))

            # Bend Table arayüzden kaldırıldı (bükümler Edit OBJ ekranında). self.table görünmez bir
            # Treeview olarak kalır; render() içindeki mevcut doldurma kodu değişmeden çalışır.
            self.table = ttk.Treeview(right, columns=("no", "obj", "exe", "ang", "stk"), show="headings", height=1)

            tk.Label(right, text="Preview", bg=BG, font=("Segoe UI", 11)).pack(anchor="w", pady=(8, 0))
            self.prev = tk.Canvas(right, width=int(420 * self.k), height=int(260 * self.k),
                                  bg=WHITE, highlightthickness=1, highlightbackground="#999")
            self.prev.pack(anchor="w", fill="both", expand=True)
            self.prev.bind("<Configure>", lambda e: self._prev_args and self.draw_preview(*self._prev_args))

            self.msg = tk.Label(right, text="", bg=BG, fg=RED, font=("Segoe UI", 10, "bold"),
                                wraplength=420, justify="left")
            self.msg.pack(anchor="w", pady=4)

            tk.Label(self, bg=BG, fg=BLUE, justify="left", font=("Segoe UI", 10, "bold"),
                     text="[Left][Right]Move cursor,[1..5] Insert code, [Del] Delete code\n"
                          "[F1]Edit General Dimension,[F2]Edit Code String,[F3]Edit Bend Dimension,\n"
                          "[Alt+Left/Right]Select stage,[F9]Confirm and exit (save + send to PLC),[ESC]Abort"
                     ).grid(row=3, column=1, columnspan=2, sticky="w", pady=8)

            self.bind("<F1>", lambda e: self.focus_field("t"))
            self.bind("<F2>", lambda e: self.edit_code())
            self.bind("<F3>", lambda e: self.focus_field("a"))
            self.bind("<F9>", lambda e: self.confirm_exit())
            self.bind("<Alt-Left>", lambda e: self.move_cursor(-1))
            self.bind("<Alt-Right>", lambda e: self.move_cursor(1))
            # [Left]/[Right]: kod onaylandıktan sonra Sol yön tuşu sol alttaki ölçü alanlarını açar;
            # sonraki basışlarda odak bir giriş kutusunda değilse hane değiştirir.
            self.bind("<Left>", self._on_left_key)
            self.code_entry.bind("<Left>", self._on_left_key)
            self.bind("<Right>", lambda e: self.move_cursor(1) if self._not_typing(e) else None)
            self.bind("<Escape>", lambda e: self.abort_exit())
            if start_program and start_program in PRESETS:
                self.load_preset(start_program)
                self._code_confirmed = True
                self._bend_inputs_shown = True
            else:
                self.start_blank_screen()
            self._sync_programs()
            tam_ekran(self)

        def _sync_programs(self):
            """Diğer pencerelerden kaydedilen programları diskten yenile (kayıt değişince, 0,5 sn'de bir yoklanır)."""
            try:
                sig = programs_signature()
                if sig != getattr(self, "_prog_sig", None):
                    self._prog_sig = sig
                    saved = load_saved(recompute=False)
                    for name, data in saved.items():
                        PRESETS[name] = data["inputs"]
                    self.preset.config(values=list(PRESETS))
            finally:
                self.after(500, self._sync_programs)

        # ---- yardımcılar
        def add_row(self, parent, row, label, key, value, color, width=14):
            lbl = tk.Label(parent, text=label, bg=BG, font=("Segoe UI", 11), anchor="w", width=20)
            lbl.grid(row=row, column=0, sticky="w", pady=2)
            self.labels[key] = lbl
            var = tk.StringVar(value=value)
            e = tk.Entry(parent, textvariable=var, bg=color, justify="right", width=width,
                         font=("Segoe UI", 11))
            e.grid(row=row, column=1, pady=2, sticky="e")
            var.trace_add("write", lambda *a: self.render())
            self.vars[key] = (var, e)

        def val(self, key):
            return parse(self.vars[key][0].get())

        def note(self, text, color):
            self.msg.config(text=text, fg=color)

        def _not_typing(self, e):
            return e.widget.winfo_class() not in ("Entry", "TEntry", "TCombobox")

        # ---- hane (kod hanesi) verisi
        def store_sel(self):
            """Ekrandaki alanları seçili hanenin kaydına yazar."""
            if 0 <= self.sel < len(self.stages):
                for k in STAGE_KEYS:
                    self.stages[self.sel][k] = self.vars[k][0].get()

        def load_sel(self):
            """Seçili hanenin kaydını ekrandaki alanlara yükler."""
            was, self._busy = self._busy, True
            for k in STAGE_KEYS:
                self.vars[k][0].set(self.stages[self.sel][k])
            self._busy = was

        def build_chips(self):
            for w in self.chips.winfo_children():
                w.destroy()
            for i, ch in enumerate(self.code.get()):
                on = i == self.sel
                tk.Button(self.chips, text=ch, width=3, font=("Segoe UI", 11, "bold"),
                          bg=BLUE if on else WHITE, fg=WHITE if on else "#222",
                          relief="sunken" if on else "raised",
                          command=lambda i=i: self.select_stage(i)).pack(side="left", padx=1)

        def select_stage(self, i):
            code = self.code.get()
            if not 0 <= i < len(code) or i == self.sel:
                return
            self.store_sel()
            self.sel = i
            self.type = int(code[i])
            self.load_sel()
            self.apply_code(convert=False, store=False)

        def move_cursor(self, step):
            self.select_stage(min(max(self.sel + step, 0), len(self.code.get()) - 1))

        def visible_keys(self, typ=None, mode=None):
            """Hane tipi ve moda göre ekranda çıkan ölçü alanları (varsayılan: seçili hane)."""
            typ = self.type if typ is None else typ
            mode = self._mode if mode is None else mode
            if typ in (1, 2):
                return ("a", "l", "h") if mode == "len" else ("ac", "a", "c", "ang")
            if typ == 3:
                return ("ac", "a", "l", "h", "ang")
            if typ == 4:
                return ("a", "l", "h", "c") if mode == "len" else ("ac", "a", "l", "h", "ang")
            return ("a", "l", "h")                       # 5: L1 (A), L2 (L), H

        def save(self):
            """F9: girdileri ve hesaplanan sonucu programs/ADI.json dosyasına yazar.
            Başarılıysa True döner (çıkış için kullanılır). Çok haneli kodlarda tüm hanelerin
            ölçü setleri "stages" altında saklanır; üst düzey a/l/h/ang/c/ac 1. haneyi gösterir."""
            if not self.result:
                self.note("Hatalı değer var, kaydedilemedi.", RED)
                return False
            from tkinter import simpledialog
            name = simpledialog.askstring("Kaydet", "Program adı:",
                                          initialvalue=self.prog.cget("text"), parent=self)
            if not name:
                return False
            name = "".join(ch for ch in name.strip().upper() if ch not in '\\/:*?"<>|')
            if name.upper().endswith(".ZW"):
                name = name[:-3].rstrip()
            if not name:
                self.note("Program adı boş olamaz.", RED)
                return False
            try:
                self.store_sel()
                stages = [{k: parse(sd[k]) for k in STAGE_KEYS} for sd in self.stages]
                for s_ in stages:
                    s_["ac"] = int(s_["ac"])
                inputs = {k: self.val(k) for k in ("t", "w", "tool", "r")}
                inputs.update({k: stages[0][k] for k in ("a", "l", "h", "ang", "c")})
                inputs.update(mat=int(self.val("mat")), ac=stages[0]["ac"],
                              code=self.code.get(), dim=self.dim.get(),
                              ref=self.result["mat_len"], stages=stages)
                save_program(name, inputs, self.result)
            except (ValueError, OSError, RuntimeError) as e:
                self.note(f"Kaydedilemedi: {e}", RED)
                return False
            PRESETS[name] = inputs
            self.preset.config(values=list(PRESETS))
            self.preset.set(name)
            self.prog.config(text=name)
            self.note(f"{name} kaydedildi.", GREEN)
            return True

        def confirm_exit(self):
            """F9: kaydet, ardından PLC'ye gönder. Kayıt başarısızsa veya ad girilmezse
            PLC'ye gönderilmez. Pencere EXIT_AFTER_SAVE'e göre kapanır ya da açık kalır."""
            if self.save():
                self.send_to_plc(saved_name=self.prog.cget("text"))

        def abort_exit(self):
            """ESC: kaydetmeden çık."""
            self.destroy()

        def focus_field(self, key):
            self.code_entry.config(state="readonly")
            e = self.vars[key][1]
            e.focus_set()
            e.select_range(0, "end")

        def edit_code(self):
            self.code_entry.config(state="normal")
            self.code_entry.focus_set()

        # ---- PLC haberleşmesi
        def build_plc_values(self):
            """(isim, node_id, değer) listesi üretir.
            Girdi alanları 1. hanenin değerleriyle (PLC'de tek ölçü seti var), malzeme boyu
            tüm hanelerin toplamı olarak yazılır. Kod 5'te L1 -> "a", L2 -> "l" düğümüne gider."""
            r = self.result
            self.store_sel()
            s0 = self.stages[0]
            v = [("mat_len", PLC_NODES["mat_len"], r["mat_len"])]
            for key in ("t", "w", "tool", "r", "mat"):
                v.append((key, PLC_NODES[key], self.val(key)))
            for key in ("ac", "a", "l", "h", "c", "ang"):
                v.append((key, PLC_NODES[key], parse(s0[key])))
            v.append(("dim", PLC_NODES["dim"], self.dim.get()))
            if PLC_WRITE_BEND1 and r["bends"]:
                pos, ang = r["bends"][0]
                v.append(("b1_pos", PLC_BEND1_NODES["pos"], pos))
                v.append(("b1_ang", PLC_BEND1_NODES["ang"], ang))
            return v

        def send_to_plc(self, saved_name=None):
            """F9: değerleri ayrı thread'de PLC'ye yazar (arayüz donmaz).
            saved_name verilirse (F9) mesajlar kayıt bilgisini de içerir."""
            if self._plc_busy:
                self.note("Önceki PLC gönderimi sürüyor, bekleyin.", RED)
                return
            if not self.result:
                self.note("Önce geçerli ölçüleri hesaplayın.", RED)
                return
            if self.unverified:
                self.note("Bu sonuç doğrulanmamış formülden geldi; PLC'ye gönderilmedi "
                          "(kayıt yapıldı).", RED)
                return
            if len(self.code.get()) > 1 and not PLC_ALLOW_MULTI:
                self.note("Çok haneli program kaydedildi; PLC'de tek ölçü seti olduğundan "
                          "gönderilmedi (PLC_ALLOW_MULTI=False).", RED)
                return
            try:
                veriler = self.build_plc_values()
            except Exception as e:
                self.note(f"PLC verisi hazırlanamadı: {e}", RED)
                return

            self._plc_busy = True
            self.note("PLC'ye gönderiliyor...", BLUE)

            def done(text, color):
                self._plc_busy = False
                self.note(text, color)

            def on_ok():
                if saved_name:
                    done(f"{saved_name} kaydedildi ve PLC'ye gönderildi.", GREEN)
                    if EXIT_AFTER_SAVE:
                        self.destroy()
                else:
                    done("PLC'ye veriler başarıyla gönderildi.", GREEN)

            def worker():
                try:
                    asyncio.run(plc_yaz_async(veriler))
                    self.after(0, on_ok)
                except Exception as e:
                    if saved_name:
                        msg = f"{saved_name} kaydedildi, ama PLC gönderim hatası: {e}"
                    else:
                        msg = f"PLC gönderim hatası: {e}"
                    self.after(0, lambda: done(msg, RED))

            threading.Thread(target=worker, daemon=True).start()

        # ---- kod dizisi
        def load_preset(self, name):
            p = PRESETS[name]
            self._busy = True
            self.preset.set(name)
            self.prog.config(text=name)
            for k in ("t", "w", "tool", "r", "mat"):
                self.vars[k][0].set(fmt(p[k]) if k != "mat" else str(p[k]))
            self.ref = p.get("ref")
            self.matlen.set(fmt(p["ref"]))
            code = p["code"]
            self.code.set(code)
            self.dim.set(p["dim"])
            # hane başına ölçü seti: "stages" varsa onlar; yoksa (eski kayıt) 1. set eksik hanelere kopyalanır
            src_st = p.get("stages") or [p]
            self.stages = []
            for i, ch in enumerate(code):
                s_ = src_st[i] if i < len(src_st) else src_st[-1]
                mode = stage_mode(int(ch), p["dim"])
                c_ = s_.get("c", 0)
                if i >= len(src_st) and int(ch) in (1, 2) and mode == "angle" and not c_:
                    c_ = s_["l"]              # kayıtta olmayan hane: C ilk setin L değerinden alınır
                self.stages.append(dict(
                    ac=str(int(s_.get("ac", 1))), a=fmt(s_["a"]), l=fmt(s_["l"]),
                    h=fmt(s_["h"]), ang=fmt(s_["ang"]), c=fmt(c_), mode=mode))
            self.sel = 0
            self._busy = False
            # Hazır/kayıtlı program seçilince sol alttaki ölçü alanları (A/C, L, H, W, C) hemen görünsün
            self._code_confirmed = True
            self._bend_inputs_shown = True
            self.apply_code(convert=False, store=False)
            self.update_layout()
            if len(code) > 1 and not p.get("stages"):
                self.note((self.msg.cget("text") + " Bu kayıtta yalnızca 1. hane ölçüsü var; diğer "
                           "haneler ondan kopyalandı: [Alt+←/→] ile haneleri seçip ölçüleri girin."
                           ).strip(), RED)

        def set_type(self, n):
            """[1..5]: seçili hanenin kodunu değiştirir (imleç hanesi)."""
            s = self.code.get() or "4"
            i = min(self.sel, len(s) - 1)
            self.code.set(s[:i] + str(n) + s[i + 1:])

        def on_code(self, *a):
            if self._busy:
                return
            s = self.code.get()
            if not s:
                return
            if not all(ch in "12345" for ch in s):
                self.fail("Kod dizisi yalnızca 1–5 rakamlarından oluşmalı.")
                return
            if len(s) > 1 and (s[0] not in MULTI_FIRST or
                               any(ch not in MULTI_CODES for ch in s[1:])):
                self.fail("Çoklu kodda yalnızca 1, 2 ve 4 kullanılabilir.")
                return
            self.apply_code()

        def apply_code(self, convert=True, store=True):
            s = self.code.get()
            if store:
                self.store_sel()
            n = len(s)
            while len(self.stages) < n:                  # yeni hane: önceki haneden kopyalanır
                self.stages.append(dict(self.stages[-1]))
            del self.stages[n:]
            self.sel = min(self.sel, n - 1)
            self.type = int(s[self.sel])
            for i, c in self.type_canvas.items():
                first, used = (i == self.type), (str(i) in s)
                c.config(highlightbackground=BLUE if used else "#999",
                         highlightthickness=4 if first else (3 if used else 2))
            was_multi = self._multi
            self._multi = n > 1
            if convert and self._multi and not was_multi:
                self.matlen.set("")              # eski/bayat değer kalmasın
            self.build_chips()
            self.load_sel()
            self.sync_mode(convert)

        def on_dim(self, *a):
            if not self._busy:
                self.sync_mode(True)

        def want_mode(self, typ=None):
            """Alanları hane tipi ve Dim Type belirler.
            Kod 1/2/4: Dış/İç -> uzunluk modu (A, L, H[, C]) ; Açı tipleri -> açı modu.
            Kod 3: her zaman açı modu.  Kod 5: her zaman uzunluk modu (L1, L2, H)."""
            return stage_mode(self.type if typ is None else typ, self.dim.get())

        def sync_mode(self, convert=True):
            """Her hanenin ölçü modunu (uzunluk/açı) kod ve Dim Type'a göre günceller; mod
            değiştiyse o hanenin değerlerini mevcut ölçülerden türetir."""
            code, dim = self.code.get(), self.dim.get()
            if convert:
                self.store_sel()
            for i, ch in enumerate(code):
                want = stage_mode(int(ch), dim)
                sd = self.stages[i]
                if convert and sd.get("mode") and sd["mode"] != want:
                    self.convert_stage(i, want)
                sd["mode"] = want
            self.load_sel()
            self._mode = self.stages[self.sel]["mode"]
            self.update_layout()
            self.render()

        def convert_stage(self, i, mode):
            """Mod değişince hanenin diğer değerlerini mevcut ölçülerden türet (C <-> W, H/L <-> C/W)."""
            sd = self.stages[i]
            code = self.code.get()
            typ = int(code[i])
            try:
                t = parse(self.vars["t"][0].get())
                a, L, H = (parse(sd[k]) for k in ("a", "l", "h"))
                if typ in (1, 2):
                    if mode == "angle":                           # A, L, H -> A, C, W
                        sd["ang"] = fmt(math.degrees(math.atan2(H, L - a)))
                        sd["c"] = fmt(math.hypot(L - a, H))
                    else:                                         # A, C, W -> L, H
                        W = math.radians(abs(parse(sd["ang"])))
                        c = parse(sd["c"])
                        sd["l"] = fmt(a + c * math.cos(W))
                        sd["h"] = fmt(c * math.sin(W))
                elif typ == 4:
                    multi = len(code) > 1
                    if mode == "len":
                        W0 = abs(parse(sd["ang"]))
                        run = z_run(t, H, W0, calibrated_multi=multi)
                        if sd["ac"].strip() == "2":               # girilen değer C idi
                            c, a = a, L - a - run
                            sd["a"] = fmt(a)
                            sd["ac"] = "1"
                        else:
                            c = L - a - run
                        sd["c"] = fmt(c)
                    else:
                        sd["ang"] = fmt(z_angle_from_len(t, a, L, H, parse(sd["c"]), multi))
            except (ValueError, ZeroDivisionError):
                pass
            sd["mode"] = mode

        def start_blank_screen(self):
            """Üst sol genel parametreler açık, kod ve alt büküm girişleri boş/gizli başlatır."""
            self._busy = True
            self._code_confirmed = False
            self._bend_inputs_shown = False
            self.result = None
            self.ref = None
            self.prog.config(text="")
            self.preset.set("")
            self.matlen.set("")
            self.code.set("")
            self.stages = [dict(ac="", a="", l="", h="", ang="", c="", mode=self._mode)]
            self.sel = 0
            for k in ("t", "w", "tool", "r", "mat"):
                self.vars[k][0].set("")
            self.matlabel.config(text="")
            self.diagram.delete("all")
            self.prev.delete("all")
            self.table.delete(*self.table.get_children())
            self.note("Genel parametreleri girin; ardından F2 ile kod yazıp Enter'a basın ve "
                      "Sol yön tuşu (←) ile ölçü alanlarını açın.", BLUE)
            self._busy = False
            self.update_layout()
            self.focus_field("t")

        def _on_code_enter(self, event=None):
            """F2 ile girilen kodu onaylar ve Sol yön tuşuna hazırlar."""
            s = self.code.get().strip()
            if not s:
                self.fail("Önce bir Code String (örn. 4 veya 44) girin.")
                return "break"
            self._code_confirmed = True
            self.code_entry.config(state="readonly")
            self.focus_set()
            self.note("Kod onaylandı. Sol alttaki ölçü alanlarını açmak için Sol yön tuşuna (←) basın.", BLUE)
            return "break"

        def _on_left_key(self, event=None):
            """Sol ok: kod girilmişse alt büküm ölçülerini aç; sonrasında hane değiştir."""
            # Normal bir ölçü girişinde metin imlecinin sol hareketini bozma.
            if event is not None and event.widget.winfo_class() in ("Entry", "TEntry", "TCombobox"):
                if event.widget is not self.code_entry:
                    return None

            if not self._bend_inputs_shown:
                code = self.code.get().strip()
                if not code:
                    self.note("Önce Code String alanına büküm kodunu girin.", RED)
                    self.edit_code()
                    return "break"
                if (any(ch not in "12345" for ch in code)
                        or (len(code) > 1 and
                            (code[0] not in MULTI_FIRST or
                             any(ch not in MULTI_CODES for ch in code[1:])))):
                    self.note("Geçersiz kod: yalnızca desteklenen 1–5 şekil kodlarını girin.", RED)
                    self.edit_code()
                    return "break"
                self._code_confirmed = True
                self.code_entry.config(state="readonly")
                self._bend_inputs_shown = True
                self.update_layout()
                self.render()
                keys = self.visible_keys()
                first = next((key for key in keys if key in self.vars), None)
                if first:
                    self.focus_field(first)
                self.note("Büküm ölçü alanları açıldı. Değerleri girebilirsiniz.", BLUE)
                return "break"

            if event is None or self._not_typing(event) or event.widget is self.code_entry:
                self.move_cursor(-1)
                return "break"
            return None

        def update_layout(self):
            keys = self.visible_keys() if self._bend_inputs_shown else ()
            for k in ("ac", "a", "l", "h", "ang", "c"):
                on = k in keys
                lbl, ent = self.labels[k], self.vars[k][1]
                (lbl.grid if on else lbl.grid_remove)()
                (ent.grid if on else ent.grid_remove)()
            if self.type == 5:
                self.labels["a"].config(text="Length L1[mm]")
                self.labels["l"].config(text="Length L2[mm]")
                self.labels["h"].config(text="Height H[mm]")
            else:
                self.labels["a"].config(
                    text="Length A/C[mm]" if "ac" in keys and self.type not in (1, 2) else "Length A[mm]")
                self.labels["l"].config(text="Length L[mm]")
                self.labels["h"].config(text="Height H[mm]")
            self.labels["ac"].config(text="C or H (1/2)" if self.type in (1, 2) and self._mode == "angle" else "A or C (1/2)")
            self.labels["c"].config(text="Length C/H[mm]" if self.type in (1, 2) and self._mode == "angle" else "Length C[mm]")
            self.mat_e.config(state="readonly")

        # ---- çizim
        def draw_diagram(self):
            """Şekil yalnızca ilk haneye (ana şekil) göre çizilir; devam hanelerinden etkilenmez."""
            d = self.diagram
            d.delete("all")
            sc = 1.0
            X = lambda x: 60 + (x - 60) * sc
            d.create_polygon(*sum([(X(x), y) for x, y in SHAPES[self.type]], ()),
                             fill=YELLOW, outline="#222")
            if self.type == 3:
                # Kod 3: düz kol (A) + W açılı eğik kol + dik kol; 2. büküm açısı 90° − W (toplam 90°)
                d.create_rectangle(14, 102, 36, 120, fill="#667", outline="#222")
                d.create_line(40, 22, X(243), 22, fill="#c22")                  # L (toplam yatay)
                d.create_line(X(256), 30, X(256), 120, fill="#c22")             # H (toplam dikey)
                d.create_line(60, 138, X(168), 138, fill="#c22")                # A (düz kol)
                for txt, x, y in [("L", 150, 12), ("H", 272, 76), ("A", 110, 130),
                                  ("W", 196, 112), ("90°−W", 196, 46), ("C", 214, 32)]:
                    d.create_text(X(x), y, text=txt, fill="#a00", font=("Georgia", 11, "bold"))
                d.scale("all", 0, 0, self.k, self.k)
                return
            d.create_rectangle(14, 98, 36, 116, fill="#667", outline="#222")
            d.create_line(40, 24, X(310), 24, fill="#c22")
            d.create_line(X(308), 60, X(308), 120, fill="#c22")
            d.create_line(60, 138, X(150), 138, fill="#c22")
            for txt, x, y in [("L", 170, 14), ("C", 236, 40), ("H", 322, 92),
                              ("A", 100, 132), ("W", 214, 110), ("S", 60, 90)]:
                d.create_text(X(x), y, text=txt, fill="#a00", font=("Georgia", 11, "bold"))
            # Ölçekleme döngünün DIŞINDA, tüm öğeler çizildikten sonra bir kez yapılır
            d.scale("all", 0, 0, self.k, self.k)

        def draw_preview(self, pts, bend_idx=None):
            """Profil önizlemesi (tüm haneler tek çizgi). Her ana segmentin uzunluğu mavi yazılır,
            bükümler nokta ile işaretlenir (bend_idx: pts içindeki büküm indisleri)."""
            self._prev_args = (pts, bend_idx)
            p = self.prev
            p.delete("all")
            CW = p.winfo_width() if p.winfo_width() > 50 else p.winfo_reqwidth()
            CH = p.winfo_height() if p.winfo_height() > 50 else p.winfo_reqheight()
            PAD = 38
            for gx in range(0, CW, 40):
                p.create_line(gx, 0, gx, CH, fill="#eef2f4")
            for gy in range(0, CH, 40):
                p.create_line(0, gy, CW, gy, fill="#eef2f4")

            xs, ys = [x for x, _ in pts], [y for _, y in pts]
            w = (max(xs) - min(xs)) or 1
            h = (max(ys) - min(ys)) or 1
            k = min((CW - 2 * PAD) / w, (CH - 2 * PAD) / h)       # eşit ölçek, tam sığdır
            ox = (CW - w * k) / 2
            oy = (CH + h * k) / 2
            P = [(ox + (x - min(xs)) * k, oy - (y - min(ys)) * k) for x, y in pts]
            n = len(P)
            p.create_line(*sum(P, ()), fill=RED, width=5, joinstyle="round", capstyle="round")

            for i in range(n - 1):                                # segment uzunlukları
                (x1, y1), (x2, y2) = P[i], P[i + 1]
                px = math.hypot(x2 - x1, y2 - y1)
                real = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                if px < 30 or real < 1e-6:
                    continue
                nx, ny = (y2 - y1) / px, -(x2 - x1) / px           # normal (ekran)
                p.create_text((x1 + x2) / 2 + nx * 13, (y1 + y2) / 2 + ny * 13,
                              text=fmt(real), fill=BLUE, font=("Segoe UI", 9, "bold"))
            for i in (bend_idx if bend_idx is not None else range(1, n - 1)):   # büküm noktaları
                x, y = P[i]
                p.create_oval(x - 4, y - 4, x + 4, y + 4, fill="white", outline="#111", width=2)
            x0, y0 = P[0]
            p.create_rectangle(x0 - 7, y0 - 7, x0 + 7, y0 + 7, fill="#1a3cff", outline="")

        def fail(self, text):
            self.result = None
            self.matlen.set("—")
            self.prev.delete("all")
            self.note(text, RED)
            self._prev_args = None

        # ---- hesap
        def render(self):
            if self._busy:
                return
            code = self.code.get().strip()
            if not code:
                self.result = None
                self.matlen.set("")
                self.table.delete(*self.table.get_children())
                return
            self.store_sel()
            self.note("", RED)
            self.result = None
            try:
                n = int(self.vars["mat"][0].get())
                self.matlabel.config(text=f"{n}– {MATS.get(n, '?')}")
            except ValueError:
                self.matlabel.config(text="?")
            self.draw_diagram()
            self.table.delete(*self.table.get_children())

            code, dim = self.code.get(), self.dim.get()
            multi = len(code) > 1
            try:
                t, r = self.val("t"), self.val("r")
            except ValueError:
                return self.fail("Kalınlık / yarıçap alanına sayı girin (örn. 10,0).")
            if multi and (code[0] not in MULTI_FIRST or any(ch not in MULTI_CODES for ch in code[1:])):
                return self.fail(
                    f"Kod {code}: çok haneli kodda ilk hane 1, 2 veya 4; devamı yalnızca "
                    "1, 2, 4 rakamlarından oluşmalı (3 ve 5 için formül yok).")

            # ---- her hane kendi ölçü setiyle
            stages = []
            for i, ch in enumerate(code):
                typ = int(ch)
                keys = self.visible_keys(typ, stage_mode(typ, dim))
                names = {"ac": "A veya C", "a": "Uzunluk A/C", "l": "Uzunluk L", "h": "Yükseklik H",
                         "ang": "Açı W", "c": "Uzunluk C"}
                if typ == 5:
                    names.update(a="Uzunluk L1", l="Uzunluk L2")
                tag = f"Hane {i + 1}: " if multi else ""
                vals = {}
                for key in keys:
                    try:
                        vals[key] = parse(self.stages[i][key])
                    except ValueError:
                        return self.fail(f"{tag}'{names[key]}' alanına sayı girin (örn. 112,0).")
                ac = int(vals["ac"]) if "ac" in vals else 1
                if "ac" in vals and ac not in (1, 2):
                    return self.fail(f"{tag}A veya C alanı 1 ya da 2 olmalı.")
                stages.append(dict(ac=ac, A=vals.get("a", 0.0), L=vals.get("l", 0.0),
                                   H=vals.get("h", 0.0), C=vals.get("c", 0.0),
                                   W=vals.get("ang", 0.0)))
            try:
                res = calc_stages(code, t, r, dim, stages)
            except (ValueError, ZeroDivisionError) as e:
                return self.fail(str(e) or "Geçersiz ölçü değeri.")

            self.unverified = not res["verified"]
            warn = res["warn"]
            self.matlen.set(fmt(res["mat"]))
            # F9 kaydı ve PLC için sonucu sakla (büküm sırası: 1. hane, sonra devam haneleri)
            self.result = dict(mat_len=res["mat"], bends=[[p, a] for p, a in res["bends"]])
            # Edit OBJ ile aynı tablo modeli ve aynı profil çizimi.
            # Side-Stop hesapları self.result içinde ayrı tutulur; OBJ/EXE ile karıştırılmaz.
            from editobj import load_table, table_from_result, obj_path, fmt1, MODES
            current_name = self.preset.get().strip()
            if current_name and os.path.isfile(obj_path(current_name)):
                obj_table, obj_mode, _ = load_table(current_name)
            else:
                obj_table = table_from_result(self.result)
                obj_mode = MODES[0]
            self.table.configure(displaycolumns=("no", "obj", "exe", "ang")
                                 if obj_mode == MODES[1] else ("no", "obj", "exe", "ang", "stk"))
            for no, obj, exe, angle, stroke in obj_table.text_rows():
                self.table.insert("", "end", values=(no, obj, exe, angle, stroke))
            # Orijinal geometrik profil: kod hanelerinin gerçek hesaplanan noktaları.
            self.draw_preview(res["pts"], res["bend_idx"])
            if code not in KNOWN_CODES:
                warn += f" Kod {code} henüz doğrulanmadı; alanlar hane tipine ve Dim Type'a göre."
            if multi and res["verified"]:
                parts = " + ".join(fmt(m) for m in res["stage_mats"])
                self.note(f"Toplam boy = hane toplamı ({parts}). Yan dayama: hane geçiş payı "
                          f"{fmt_signed(sum(res['joints']))} mm dahil.", BLUE)
            else:
                self.note(warn.strip(), RED)

    app = App()
    app.mainloop()


if __name__ == "__main__":
    if "--test-mat" in sys.argv:
        cases = [(8, 8, 826, 54, 45, 843.2),
                 (5, 5, 152, 95, 60.9, 202.2),
                 (12, 15, 500, 206, 45, 577.8)]
        for t, r, L, H, W, machine in cases:
            calc = mat_length_kod4(t, r, L, H, W)
            print(f"t={t:g} r={r:g} L={L:g} H={H:g} W={W:g} | "
                  f"Hesap={calc:.3f} Makine={machine:.1f} Hata={calc-machine:+.3f} mm")
        sys.exit(0)
    if "--fit" in sys.argv:
        fit_side_stop(sys.argv[sys.argv.index("--fit") + 1])
        sys.exit(0)
    if "--test" in sys.argv:
        sys.exit(0 if self_test() else 1)
    # op.py "Edit OBJ" düğmesi program adını ilk argüman olarak gönderir
    run_gui(sys.argv[1] if len(sys.argv) > 1 else None)