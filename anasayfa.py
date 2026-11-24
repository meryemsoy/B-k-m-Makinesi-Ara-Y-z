#!/usr/bin/env python3
"""Bara büküm programı arayüzü (Tkinter) + büküm formülleri + PLC (OPC UA) yazma.

Çalıştırma : python anasayfa.py [PROGRAM_ADI]
Formül testi: python anasayfa.py --test

PROGRAM_ADI verilirse (örn. NEW.ZW) o program açılışta yüklenir
(op.py içindeki "Edit OBJ" düğmesi bu şekilde çağırır).

F9  : program kaydedilir (programs/ADI.ZW.json: girdiler + hesaplanan sonuç) ve
      hesaplanan değerler OPC UA ile PLC'ye yazılır. ESC: çıkış.
op.py'deki Load düğmesi bu kayıtları listeler.

Code String = aşama dizisi. İlk hane ana şekli (1-5), sonraki haneler devam
aşamalarını belirtir (41 = Z + 1, 42 = Z + 2, 44 = Z + Z, 444, 14, 21, 114 ...).
Çok haneli kodlarda ilk hane 1, 2 veya 4; devam haneleri yalnızca 1, 2, 4 olabilir.
ÇOK HANELİ KODLARDA büküm sırası: önce İLK hanenin bükümleri, sonra sırayla
sonraki hanelerin bükümleri (Büküm Tablosu bu sırayla numaralanır).

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
    Kod 3       Excel verisi formülü belirlemeye yetmedi (bkz. stage_calc) -> doğrulanmadı.
------------------------------------------------------------------------------
"""
import asyncio
import json
import math
import os
import sys
import threading

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
# Çok haneli kodlar (41/42/44): toplam boydaki büküm düşümü, tek büküm modelindeki BD'nin MULTI_BD katı
# alınır (k=2 için 1,18 / 1,22 ; k=4 için 1,29 -> ortalama 1,22). Eski K_Z/K_Z4 sabitlerinin yerine geçer.
# Yalnızca t=5–6 mm ve Dış/Dış Açı ölçülü 3 ekran örneğiyle kalibre edildi (±1,1 mm).
MULTI_BD = 1.22
MULTI_T_RANGE = (4.0, 7.0)    # kalibrasyonun geçerli olduğu kalınlık aralığı [mm]
MULTI_CODES = {"1", "2", "4"}   # çok haneli kodda izin verilen DEVAM haneleri
MULTI_FIRST = {"1", "2", "4"}   # çok haneli kodda izin verilen İLK haneler
# Çok haneli kodlarda Dış/İç ölçü modunda makine L−A−C farkına ~0,5 mm ekliyor
# (5418933-2: L=292, A+C=292 -> W≈89,7°, Mat=668,8). Yalnızca 41/42/44 için kullanılır.
RUN_OFF = 0.5
MATS = {1: "bakır", 2: "alüminyum", 3: "pirinç", 4: "çelik", 5: "diğer"}

# Doğrulanmış kodlar (diğerleri uyarı verir ve PLC'ye gönderilmez). 3: Excel verisi yetersiz.
KNOWN_CODES = {"1", "2", "4", "5", "41", "42", "44"}


def multi_k(tail):
    """Toplam boy formülündeki k (büküm sayısı katsayısı) — ilk hane 4 olan kodlar için.
    Kalibre edilen örnekler: 41/42 -> k=2, 44 -> k=4.
    Genelleme: temel k=2, devam hanelerindeki her Z (4) iki büküm daha ekler.
        41, 42 -> 2 | 44, 414, 441 -> 4 | 444 -> 6 | 4444 -> 8
    UYARI: 41/42/44 dışındakiler EKSTRAPOLASYONdur, makinede doğrulanmadı."""
    return 2 + 2 * tail.count("4")


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
# (çok haneli kodlarda bu, İLK hanenin ilk bükümüdür).
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

# F9 sonrası pencere kapansın mı? False: açık kalır (arka arkaya birden fazla kayıt yapılabilir).
# True: kayıt + PLC gönderimi başarılıysa pencere kapanır.
EXIT_AFTER_SAVE = False

# Örnekler (dim = Dim Type, ref = makinedeki Mat.Length)
PRESETS = {
    "NEW.ZW": dict(code="4", t=10, w=80, tool=63, r=5, mat=1, ac=1, a=112, l=1299.5,
                   h=20, ang=28, c=0, dim=2, ref=1301.4),
    "5418933-2.ZW": dict(code="41", t=5, w=40, tool=63, r=5, mat=1, ac=1, a=135, l=292,
                         h=100, ang=90, c=157, dim=0, ref=668.8),
    "2242081.ZW": dict(code="44", t=6, w=70, tool=32, r=5, mat=1, ac=2, a=85, l=200,
                       h=44, ang=-60, c=0, dim=2, ref=826.1),
    "2514771.ZW": dict(code="42", t=5, w=50, tool=63, r=5, mat=1, ac=1, a=60, l=249.8,
                       h=180, ang=74, c=0, dim=2, ref=570.2),
    "2U1-2U2.ZW": dict(code="4", t=5, w=60, tool=32, r=5, mat=1, ac=1, a=82, l=152,
                       h=95, ang=60.9, c=0, dim=2, ref=202.2),
    "16.ZW": dict(code="4", t=12, w=16, tool=90, r=15, mat=1, ac=1, a=100, l=500,
                  h=206, ang=45, c=0, dim=2, ref=577.8),
    # Aşağıdakiler formülle HENÜZ uyuşmuyor (bkz. self_test çıktısı):
    "119-7594 ÜÇ BÜKÜM.ZW": dict(code="41", t=10, w=25, tool=63, r=10, mat=1, ac=1, a=55,
                                 l=500, h=87.9, ang=60, c=0, dim=2, ref=741.5),
    "2237734--.ZW": dict(code="44", t=10, w=80, tool=40, r=5, mat=1, ac=1, a=80, l=200,
                         h=34, ang=-60, c=0, dim=3, ref=942.3),
}

# ------------------------------------------------------------ kayıt / yükleme
PROG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "programs")


def save_program(name, inputs, result):
    """programs/ADI.json dosyasına girdileri ve hesaplanan sonucu yazar."""
    os.makedirs(PROG_DIR, exist_ok=True)
    with open(os.path.join(PROG_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(dict(name=name, inputs=inputs, result=result),
                  f, ensure_ascii=False, indent=2)


def load_saved():
    """programs/*.json -> {ad: {'name', 'inputs', 'result'}}"""
    out = {}
    if os.path.isdir(PROG_DIR):
        for fn in sorted(os.listdir(PROG_DIR)):
            if fn.lower().endswith(".json"):
                try:
                    with open(os.path.join(PROG_DIR, fn), encoding="utf-8") as f:
                        d = json.load(f)
                    out[d["name"]] = d
                except (OSError, ValueError, KeyError):
                    pass
    return out


PRESETS.update({n: d["inputs"] for n, d in load_saved().items()})


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
    if t <= 0 or r < 0 or L <= 0 or H <= t or not (0 < W <= 90):
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
    if H <= t:
        raise ValueError(f"H ({H:g}) kalınlıktan ({t:g}) büyük olmalı.")
    if d0 < -0.05:
        raise ValueError("L, A ile C toplamından küçük olamaz (L ≥ A + C).")
    return math.degrees(math.atan2(H - t, max(d0, 0.0) + off))


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
            if A <= 0 or L <= 0 or H <= 0:
                raise ValueError("A, L ve H sıfırdan büyük olmalı.")
            th = math.atan2(H, L - A)               # 90°'yi aşabilir (L < A)
            leg = math.hypot(L - A, H)
        else:
            if A <= 0 or C <= 0:
                raise ValueError("A ve C sıfırdan büyük olmalı.")
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
        return dict(mat=mat, bends=[(pos, -math.degrees(th))], pts=pts, warn=warn,
                    W=math.degrees(th), A=A, flat_last=flat, along=0.0, heading=heading)

    if code == 3:
        # Excel'de kod 3 için yalnızca A=50 (hiç değişmedi), L=150/250 (yalnız W=45°) ve
        # W=45/60° ile 3 satır var; Mat'in A, L, H, W'ya bağlılığı bu veriyle çözülemedi.
        # Eski tek bükümlü formül (A + L − BD) kullanılır, DOĞRULANMADI.
        if A <= 0 or L <= 0:
            raise ValueError("A ve L sıfırdan büyük olmalı.")
        if not 0 < W <= 180:
            raise ValueError("Açı 0–180° arasında olmalı.")
        th = math.radians(W)
        ba = bend_allowance(t, r, th)
        sb = bend_setback(t, r, th, outer)
        mat = A + L - (2 * sb - ba)
        pts = [(0, 0), (A, 0), (A + L * math.cos(th), L * math.sin(th))]
        warn = ("UYARI: Kod 3 formülü DOĞRULANMADI (Excel'deki 6 satırla 7-70 mm sapıyor). "
                "Bu sonucu PLC'ye göndermeyin; kod 3 için ek deney gerekli.")
        return dict(mat=mat, bends=[(A - sb + ba / 2, -W)], pts=pts, warn=warn, W=W, A=A,
                    flat_last=L, along=0.0, heading=W)

    if code == 4:
        if H <= t:
            raise ValueError(f"H ({H:g}) kalınlıktan ({t:g}) büyük olmalı.")
        if A <= 0 or L <= 0:
            raise ValueError("A ve L sıfırdan büyük olmalı.")
        if mode == "len":
            W = z_angle_from_len(t, A, L, H, C, calibrated_multi)
        else:
            if not 0 < W <= 90:
                raise ValueError("Z büküm için açı 0–90° arasında olmalı.")
            if ac == 2:                              # girilen değer C ise A'yı türet
                if calibrated_multi:
                    A = L - A - (H - t) / math.tan(math.radians(W))
                else:
                    A = L - A - dx_exact(t, H, W)
            elif ac != 1:
                raise ValueError("A veya C alanı 1 ya da 2 olmalı.")
        th = math.radians(W)
        # Excel: Mat = L + (H + e·t)·tan(W/2) − 2·BD,  e = 1 (Dış), 2 (İç). A'dan bağımsız.
        mat = mat_length_kod4(t, r, L, H, W, outer=outer)
        # yan dayama konumları (şerit boyunca)
        ossb = bend_setback(t, r, th, True)
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
        return dict(mat=mat, bends=[(pos2, -W), (pos1, W)], pts=pts, warn=warn, W=W, A=A,
                    flat_last=max(flat, 0.0), along=along, heading=0.0)

    if code == 5:
        # Arayüzde  A = L1,  L = L2,  H = gövde (Excel: Mat = H + L1 + L2 − 2·BD(90°))
        if A <= 0 or L <= 0 or H <= 0:
            raise ValueError("L1, L2 ve H sıfırdan büyük olmalı.")
        th = math.pi / 2
        mat = A + H + L - 2 * bend_deduct(t, r, th, outer)
        rho = r + K_FACTOR * t                       # yan dayama: ekrandan doğrulanmış düzen
        c = 2 - math.pi / 2
        pos1 = (A - t / 2) - rho + math.pi * rho / 4
        pos2 = pos1 + (H - t) - rho * c
        pts = [(0, 0), (A, 0), (A, H), (A - L, H)]
        return dict(mat=mat, bends=[(pos2, -90.0), (pos1, -90.0)], pts=pts, warn=warn,
                    W=90.0, A=A, flat_last=L, along=H, heading=180.0)

    raise ValueError("Bilinmeyen büküm tipi.")


def calc(code, t, r, outer, mode, d, ac=1, calibrated_multi=False):
    """Tek ölçü grubu ile tekli/çoklu kod hesapla.

    Dönen sözlükte mat, bends, pts, warn, verified alanları bulunur.
    Çoklu kodlarda devam bükümleri ve toplam boy deneysel/yaklaşıktır.
    """
    code = str(code).strip()
    if not code or any(ch not in "12345" for ch in code):
        raise ValueError("Kod dizisi yalnızca 1–5 rakamlarından oluşmalı.")
    if len(code) > 1 and (code[0] not in MULTI_FIRST or
                          any(ch not in MULTI_CODES for ch in code[1:])):
        raise ValueError("Çoklu kod: ilk ve devam haneleri 1, 2 veya 4 olmalı.")
    first, tail = int(code[0]), code[1:]
    st = stage_calc(first, t, r, outer, mode, d, ac=ac,
                    calibrated_multi=bool(tail and first == 4))
    result = dict(st)
    result["verified"] = not tail and first in (1, 2, 4, 5)
    if not tail:
        return result
    tbends, strip = chain_tail(tail, t, r, st, d)
    result["bends"] = list(st["bends"]) + tbends
    result["strip"] = strip
    if first == 4:
        c = d["C"] if mode == "len" else None
        result["mat"] = multi_total(t, r, st["A"], d["L"], d["H"],
                                    st["W"], k=multi_k(tail), C=c)
    else:
        result["mat"] = tail_total(t, r, st, tail)
    result["warn"] = (st["warn"] + " Çoklu kod: malzeme boyu ve devam bükümlerinin "
                      "yan dayama değerleri deneysel/yaklaşıktır; makinede doğrulanmadan "
                      "PLC'ye gönderilmez.").strip()
    return result


def chain_tail(tail, t, r, st, d):
    """İlk aşamadan sonraki hanelerin bükümleri (şerit sırasıyla).

    Her sonraki hane, bir önceki aşamanın son düz kısmından (flat_last) sonra bükülür;
    ardışık yan dayama konumları:  pos = önceki_pos + düz_boy − (BD_önceki + BD_yeni)/2.
    Açılar: 1/2/3 -> −W ; 4 -> (−W, +W) ; 5 -> (−90, −90).  W = ilk aşamanın açısı.
    Excel'de çok haneli satır olmadığından konumlar YAKLAŞIKTIR.
    Döner: (bends, strip) -- bends tablo sırasında, strip şerit sırasında [(pos, açı, sonraki_boy|None)].
    """
    last_pos, last_ang = max(st["bends"], key=lambda b: b[0])
    prev_bd = bend_deduct(t, r, math.radians(min(abs(last_ang), 179.0)), True)
    gap = st["flat_last"]
    W = st["W"]
    # Z'nin ara (eğim) boyu: ilk hane 1/2 ise st["along"] = 0 olur, bu durumda flat_last kullanılır
    spec = {"1": ([-W], 0.0), "2": ([-W], 0.0), "3": ([-W], 0.0),
            "4": ([-W, W], st["along"] or st["flat_last"]), "5": ([-90.0, -90.0], d["H"])}
    bends, strip = [], []
    for dg in tail:
        if dg not in spec:
            raise ValueError(f"Bilinmeyen kod hanesi: {dg}")
        angs, inter = spec[dg]
        stage = []
        for i, a in enumerate(angs):
            bdi = bend_deduct(t, r, math.radians(min(abs(a), 179.0)), True)
            pos = last_pos + gap - (prev_bd + bdi) / 2
            stage.append((pos, a, inter if i < len(angs) - 1 else None))
            last_pos, prev_bd, gap = pos, bdi, inter
        gap = st["flat_last"]
        strip += stage
        # Z ve U hanelerinde, ilk aşamadaki gibi uzak büküm tabloda önce gelir
        bends += [(p, a) for p, a, _ in (stage[::-1] if len(stage) > 1 else stage)]
    return bends, strip


def multi_total(t, r, A, L, H, W, k=2, C=None):
    """Çok haneli kodlarda (ilk hane 4) toplam malzeme boyu (ekran görüntülerinden çıkarıldı):
        Mat.Length = k·(A + C) + (k−1)·H + t − MULTI_BD·k·BD(W)
        k = 2 : kod 41, 42 (Z + tek büküm)      -> 668,8 / 570,2 ekranlarına ±1 mm
        k = 4 : kod 44 (Z + Z)                  -> 826,1 ekranına ±1,1 mm (tek örnek)
        k = 6, 8 ... : 444, 4444 ... (multi_k ile ekstrapolasyon, DOĞRULANMADI)
    C = L − A − (H−t)/tan(W) ;  BD = bend_deduct (Dış ölçü, tek büküm modeliyle aynı K-faktörü)
    UYARI: yalnızca t=5-6 mm örnekleriyle doğrulandı; t=10 mm örnekleri
    (119-7594 kod 41 -> 741,5 ; 2237734 kod 44 -> 942,3) bu yapıyla UYUŞMUYOR."""
    th = math.radians(W)
    xe = (H - t) / math.tan(th) if W < 90 else 0.0
    if C is None:                      # Dış/İç modda C doğrudan girilmiş olur
        C = L - A - xe
    bd = bend_deduct(t, r, th, True)
    return k * (A + C) + (k - 1) * H + t - MULTI_BD * k * bd


def tail_total(t, r, st, tail):
    """İlk hane 1/2 olan çok haneli kodlarda toplam boy (geometrik ekleme modeli).
    Her devam hanesi: sonraki düz boy (Z'de eğim boyu da) eklenir, hanenin BD'leri düşülür.
        1, 2 -> 1 büküm (W)   |   4 -> 2 büküm (W, W) + eğim boyu
    UYARI: makinede doğrulanmadı (ekran örneği yok), PLC'ye gönderilmez."""
    total = st["mat"]
    W = math.radians(min(st["W"], 179.0))
    bd1 = bend_deduct(t, r, W, True)
    for dg in tail:
        if dg == "4":
            total += (st["along"] or st["flat_last"]) + st["flat_last"] - 2 * bd1
        else:
            total += st["flat_last"] - bd1
    return total


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
    # 41: arayüzdeki gibi W, girilen C=157'den türetilir (Dış ölçü modu)
    W41 = math.degrees(math.atan2(100 - 5, 292 - 135 - 157 + RUN_OFF))
    m41 = multi_total(5, 5, 135, 292, 100, W41, C=157)
    print(f"5418933-2 (41)  : {m41:.2f}  W={W41:.2f} (ekran: 668,8)")
    ok &= abs(m41 - 668.8) < 1.5
    m42 = multi_total(5, 5, 60, 249.8, 180, 74)
    print(f"2514771   (42)  : {m42:.2f} (ekran: 570,2)")
    ok &= abs(m42 - 570.2) < 1.5
    A44 = 200 - 85 - (44 - 6) / math.tan(math.radians(60))
    m44 = multi_total(6, 5, A44, 200, 44, 60, k=4)
    print(f"2242081   (44)  : {m44:.2f} (ekran: 826,1)  [tek örnek]")
    ok &= abs(m44 - 826.1) < 1.5
    # çok haneli büküm sırası: önce ilk hane (Z: 2 büküm), sonra sonraki hane
    s1 = stage_calc(4, 5, 5, True, "angle", dict(A=60, L=249.8, H=180, C=0, W=74),
                    calibrated_multi=True)
    tb, strip = chain_tail("2", 5, 5, s1, dict(A=60, L=249.8, H=180, C=0, W=74))
    seq = s1["bends"] + tb
    print("42 büküm sırası :", ", ".join(f"{i}) {p:.1f} / {a:+.0f}°" for i, (p, a) in enumerate(seq, 1)))
    ok &= len(seq) == 3 and tb[0][0] > max(p for p, _ in s1["bends"])
    # genelleştirilmiş çok haneli kodlar
    ok &= multi_k("1") == 2 and multi_k("4") == 4 and multi_k("44") == 6
    tb44, _ = chain_tail("44", 5, 5, s1, dict(A=60, L=249.8, H=180, C=0, W=74))
    ok &= len(s1["bends"] + tb44) == 6                 # 444 -> 2 + 2 + 2 büküm
    s2 = stage_calc(1, 5, 5, True, "len", dict(A=50, L=100, H=40, C=0, W=0))
    tb2, _ = chain_tail("4", 5, 5, s2, dict(A=50, L=100, H=40, C=0, W=0))
    ok &= len(s2["bends"] + tb2) == 3                  # 14 -> 1 + 2 büküm
    ok &= tail_total(5, 5, s2, "1") > s2["mat"]
    print("--- bilinen uyuşmazlıklar (t=10 mm, yapı henüz çözülemedi; teste dahil değil)")
    mb = multi_total(10, 10, 55, 500, 87.9, 60)
    print(f"119-7594  (41)  : {mb:.2f} (ekran: 741,5)")
    me = multi_total(10, 5, 80, 200, 34, 60, k=4)
    print(f"2237734   (44)  : {me:.2f} (ekran: 942,3)  [İç Açı, iç ölçü uygulanmadı]")
    print("TEST", "GEÇTİ" if ok else "KALDI")
    return ok


# ----------------------------------------------------------------- arayüz
def parse(s):
    s = s.strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def fmt(v, thousands=False):
    s = f"{v:,.1f}" if thousands else f"{v:.1f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


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
        3: [(14, 20), (62, 20), (62, 58), (14, 58)],
        4: [(10, 62), (44, 62), (68, 36), (92, 36)],
        5: [(20, 22), (80, 22), (80, 60), (24, 60)],
    }
    SHAPES = {
        4: [(60, 100), (150, 100), (190, 60), (300, 60), (300, 76), (200, 76), (160, 116), (60, 116)],
        1: [(60, 100), (180, 100), (280, 40), (292, 54), (190, 116), (60, 116)],
        2: [(60, 116), (140, 116), (190, 60), (300, 60), (300, 76), (200, 76), (150, 132), (60, 132)],
        3: [(60, 40), (250, 40), (250, 120), (234, 120), (234, 56), (60, 56)],
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
            self.type = 4
            self._mode = "angle"
            self.ref = None
            self.vars, self.labels = {}, {}
            self._busy = False
            self._multi = False
            self._plc_busy = False      # PLC gönderimi sürerken ikinci gönderimi engeller
            self.result = None          # son hesaplanan sonuç (F9 kaydı / PLC için)
            self.unverified = False     # True: sonuç doğrulanmadı, PLC'ye gönderilmez

            tk.Label(self, text="Mevcut Program:", bg=BG, font=("Segoe UI", 12, "bold")
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
            tk.Label(mid, text="Malzeme Boyu [mm]", bg=BG, font=("Segoe UI", 11),
                     anchor="w", width=20).grid(row=0, column=0, sticky="w", pady=2)
            self.mat_e = tk.Entry(mid, textvariable=self.matlen, state="readonly",
                                  justify="right", width=14, font=("Segoe UI", 11, "bold"),
                                  readonlybackground=WHITE)
            self.mat_e.grid(row=0, column=1, sticky="e")
            self.mat_e.bind("<KeyRelease>", lambda e: self.render())
            self.add_row(mid, 1, "Kalınlık [mm]", "t", "10,0", FIELD)
            self.add_row(mid, 2, "Genişlik [mm]", "w", "80,0", FIELD)
            self.add_row(mid, 3, "Takım [mm]", "tool", "63,0", FIELD)
            self.add_row(mid, 4, "Yarıçap [mm]", "r", "5,0", FIELD)
            self.add_row(mid, 5, "Malzeme [1..5]", "mat", "1", FIELD, width=4)
            self.matlabel = tk.Label(mid, bg=BG, text="1– bakır")
            self.matlabel.grid(row=5, column=2, sticky="w", padx=6)

            self.diagram = tk.Canvas(mid, width=int(420 * self.k), height=int(150 * self.k),
                                     bg=WHITE, highlightthickness=1, highlightbackground="#999")
            self.diagram.grid(row=6, column=0, columnspan=3, pady=8, sticky="w")

            self.add_row(mid, 7, "A veya C (1/2)", "ac", "1", WHITE, width=4)
            self.add_row(mid, 8, "Uzunluk A/C [mm]", "a", "112,0", WHITE)
            self.add_row(mid, 9, "Uzunluk L [mm]", "l", "1.299,5", WHITE)
            self.add_row(mid, 10, "Yükseklik H [mm]", "h", "20,0", WHITE)
            self.add_row(mid, 11, "Açı W [derece]", "ang", "28,0", WHITE)
            self.add_row(mid, 12, "Uzunluk C [mm]", "c", "0,0", WHITE)

            right = tk.Frame(self, bg=BG)
            right.grid(row=1, column=2, sticky="nsew", padx=20)
            tk.Label(right, text="Kod Dizisi", bg=BG, font=("Segoe UI", 11)).pack(anchor="w")
            self.code = tk.StringVar(value="4")
            self.code_entry = tk.Entry(right, textvariable=self.code, width=12, fg=BLUE,
                                       font=("Segoe UI", 13, "bold"), state="normal")
            self.code_entry.pack(anchor="w", pady=(0, 8))
            self.code.trace_add("write", self.on_code)

            box = tk.LabelFrame(right, text="Ölçü Tipi", bg=BG, fg="#1a2a7a",
                                font=("Segoe UI", 10, "bold"), padx=8, pady=2)
            box.pack(anchor="w", fill="x")
            self.dim = tk.IntVar(value=2)
            self.dim.trace_add("write", self.on_dim)
            for i, name in enumerate(["Dış [ALT+1]", "İç [ALT+2]", "Dış Açı [ALT+3]", "İç Açı [ALT+4]"]):
                tk.Radiobutton(box, text=name, variable=self.dim, value=i, bg=BG,
                               activebackground=BG, font=("Segoe UI", 10)).pack(anchor="w")
                self.bind(f"<Alt-Key-{i+1}>", lambda e, v=i: self.dim.set(v))

            tk.Label(right, text="Büküm Tablosu (işlem sırası)", bg=BG, font=("Segoe UI", 11)
                     ).pack(anchor="w", pady=(8, 0))
            self.table = ttk.Treeview(right, columns=("no", "ss", "ang"), show="headings", height=6)
            for col, txt, w in (("no", "NO.", 50), ("ss", "Side-Stop", 130), ("ang", "Angle", 100)):
                self.table.heading(col, text=txt)
                self.table.column(col, width=w, anchor="center")
            self.table.pack(anchor="w")

            tk.Label(right, text="Önizleme", bg=BG, font=("Segoe UI", 11)).pack(anchor="w", pady=(8, 0))
            self.prev = tk.Canvas(right, width=int(420 * self.k), height=int(260 * self.k),
                                  bg=WHITE, highlightthickness=1, highlightbackground="#999")
            self.prev.pack(anchor="w", fill="both", expand=True)
            self.prev.bind("<Configure>", lambda e: self._prev_args and self.draw_preview(*self._prev_args))

            self.msg = tk.Label(right, text="", bg=BG, fg=RED, font=("Segoe UI", 10, "bold"),
                                wraplength=420, justify="left")
            self.msg.pack(anchor="w", pady=4)

            tk.Label(self, bg=BG, fg=BLUE, justify="left", font=("Segoe UI", 10, "bold"),
                     text="[F1] Genel Ölçüleri Düzenle, [F2] Kod Dizisini Düzenle, "
                          "[F3] Büküm Ölçüsünü Düzenle,\n"
                          "[F9] Kaydet + PLC'ye Gönder, [ESC] Çık"
                     ).grid(row=3, column=1, columnspan=2, sticky="w", pady=8)

            self.bind("<F1>", lambda e: self.focus_field("t"))
            self.bind("<F2>", lambda e: self.edit_code())
            self.bind("<F3>", lambda e: self.focus_field("a"))
            self.bind("<F9>", lambda e: self.confirm_exit())
            self.bind("<Escape>", lambda e: self.abort_exit())
            # op.py'den program adı geldiyse onu yükle, yoksa NEW.ZW
            self.load_preset(start_program if start_program in PRESETS else "NEW.ZW")
            tam_ekran(self)

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

        def is_single(self):
            """Tek Z (kod 4, 41/42/44 devam hanesi yok): Excel'den çıkan tek Z formülü."""
            return not (self.type == 4 and self.code.get()[1:2] in ("1", "2", "3", "4"))

        def visible_keys(self):
            """Aktif kod ve moda göre ekranda çıkan ölçü alanları."""
            typ, mode = self.type, self._mode
            if typ in (1, 2):
                return ("a", "l", "h") if mode == "len" else ("a", "c", "ang")
            if typ == 3:
                return ("ac", "a", "l", "h", "ang")
            if typ == 4:
                return ("a", "l", "h", "c") if mode == "len" else ("ac", "a", "l", "h", "ang")
            return ("a", "l", "h")                       # 5: L1 (A), L2 (L), H

        def save(self):
            """F9: girdileri ve hesaplanan sonucu programs/ADI.json dosyasına yazar.
            Başarılıysa True döner (çıkış için kullanılır)."""
            if not self.result:
                self.note("Hatalı değer var, kaydedilemedi.", RED)
                return False
            from tkinter import simpledialog
            name = simpledialog.askstring("Kaydet", "Program adı:",
                                          initialvalue=self.prog.cget("text"), parent=self)
            if not name:
                return False
            name = "".join(ch for ch in name.strip().upper() if ch not in '\\/:*?"<>|')
            if not name.endswith(".ZW"):
                name += ".ZW"
            try:
                inputs = {k: self.val(k) for k in ("t", "w", "tool", "r", "a", "l", "h", "ang", "c")}
                inputs.update(mat=int(self.val("mat")), ac=int(self.val("ac")),
                              code=self.code.get(), dim=self.dim.get(),
                              ref=self.result["mat_len"])
                save_program(name, inputs, self.result)
            except (ValueError, OSError) as e:
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
            Girdi alanları arayüzdeki değerleriyle, malzeme boyu hesaplanan sonuçla yazılır.
            Kod 5'te L1 -> "a" düğümüne, L2 -> "l" düğümüne yazılır."""
            r = self.result
            v = [("mat_len", PLC_NODES["mat_len"], r["mat_len"])]
            for key in ("t", "w", "tool", "r", "mat", "ac", "a", "l", "h", "c", "ang"):
                v.append((key, PLC_NODES[key], self.val(key)))
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
            for k in ("t", "w", "tool", "r", "mat", "ac", "a", "l", "h", "ang", "c"):
                self.vars[k][0].set(fmt(p[k]) if k not in ("mat", "ac") else str(p[k]))
            self.ref = p.get("ref")
            self.matlen.set(fmt(p["ref"]))
            self.code.set(p["code"])
            self.dim.set(p["dim"])
            self._busy = False
            self.apply_code(convert=False)

        def set_type(self, n):
            self.code.set(str(n) + self.code.get()[1:])

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

        def apply_code(self, convert=True):
            s = self.code.get()
            self.type = int(s[0])
            for i, c in self.type_canvas.items():
                first, used = (i == self.type), (str(i) in s)
                c.config(highlightbackground=BLUE if used else "#999",
                         highlightthickness=4 if first else (3 if used else 2))
            was_multi = self._multi
            self._multi = len(s) > 1
            if convert and self._multi and not was_multi:
                self.matlen.set("")              # eski/bayat değer kalmasın
            self.sync_mode(convert)

        def on_dim(self, *a):
            if not self._busy:
                self.sync_mode(True)

        def want_mode(self):
            """Alanları kod ve Dim Type belirler.
            Kod 1/2/4: Dış/İç -> uzunluk modu (A, L, H[, C]) ; Açı tipleri -> açı modu.
            Kod 3: her zaman açı modu.  Kod 5: her zaman uzunluk modu (L1, L2, H)."""
            if self.type == 5:
                return "len"
            if self.type == 3:
                return "angle"
            return "len" if self.dim.get() in (0, 1) else "angle"

        def sync_mode(self, convert=True):
            mode = self.want_mode()
            if convert and mode != self._mode:
                self.convert(mode)
            self._mode = mode
            self.update_layout()
            self.render()

        def convert(self, mode):
            """Mod değişince diğer değerleri mevcut ölçülerden türet (C <-> W, H/L <-> C/W)."""
            self._busy = True
            try:
                t, a, L, H = (self.val(k) for k in ("t", "a", "l", "h"))
                if self.type in (1, 2):
                    if mode == "angle":                           # A, L, H -> A, C, W
                        self.vars["ang"][0].set(fmt(math.degrees(math.atan2(H, L - a))))
                        self.vars["c"][0].set(fmt(math.hypot(L - a, H)))
                    else:                                         # A, C, W -> L, H
                        W = math.radians(abs(self.val("ang")))
                        c = self.val("c")
                        self.vars["l"][0].set(fmt(a + c * math.cos(W)))
                        self.vars["h"][0].set(fmt(c * math.sin(W)))
                elif self.type == 4:
                    hc = H - t
                    single = self.is_single()
                    if mode == "len":
                        W0 = abs(self.val("ang"))
                        run = z_run(t, H, W0, calibrated_multi=not single)
                        if self.vars["ac"][0].get().strip() == "2":   # girilen değer C idi
                            c, a = a, L - a - run
                            self.vars["a"][0].set(fmt(a))
                            self.vars["ac"][0].set("1")
                        else:
                            c = L - a - run
                        self.vars["c"][0].set(fmt(c))
                    else:
                        W = z_angle_from_len(t, a, L, H, self.val("c"), not single)
                        self.vars["ang"][0].set(fmt(W))
            except (ValueError, ZeroDivisionError):
                pass
            self._busy = False

        def update_layout(self):
            keys = self.visible_keys()
            for k in ("ac", "a", "l", "h", "ang", "c"):
                on = k in keys
                lbl, ent = self.labels[k], self.vars[k][1]
                (lbl.grid if on else lbl.grid_remove)()
                (ent.grid if on else ent.grid_remove)()
            if self.type == 5:
                self.labels["a"].config(text="Uzunluk L1 [mm]")
                self.labels["l"].config(text="Uzunluk L2 [mm]")
                self.labels["h"].config(text="Yükseklik H [mm]")
            else:
                self.labels["a"].config(
                    text="Uzunluk A/C [mm]" if "ac" in keys else "Uzunluk A [mm]")
                self.labels["l"].config(text="Uzunluk L [mm]")
                self.labels["h"].config(text="Yükseklik H [mm]")
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
            d.create_rectangle(14, 98, 36, 116, fill="#667", outline="#222")
            d.create_line(40, 24, X(310), 24, fill="#c22")
            d.create_line(X(308), 60, X(308), 120, fill="#c22")
            d.create_line(60, 138, X(150), 138, fill="#c22")
            for txt, x, y in [("L", 170, 14), ("C", 236, 40), ("H", 322, 92),
                              ("A", 100, 132), ("W", 214, 110), ("S", 60, 90)]:
                d.create_text(X(x), y, text=txt, fill="#a00", font=("Georgia", 11, "bold"))
            # Ölçekleme döngünün DIŞINDA, tüm öğeler çizildikten sonra bir kez yapılır
            d.scale("all", 0, 0, self.k, self.k)

        def draw_preview(self, pts, nmain=None, tail_cut=False):
            """Profil önizlemesi: ilk hane kırmızı, devam haneleri (kuyruk) kesikli gri.
            Her ana segmentin uzunluğu mavi yazılır, bükümler nokta ile işaretlenir."""
            self._prev_args = (pts, nmain, tail_cut)
            p = self.prev
            p.delete("all")
            CW = p.winfo_width() if p.winfo_width() > 50 else p.winfo_reqwidth()
            CH = p.winfo_height() if p.winfo_height() > 50 else p.winfo_reqheight()
            PAD = 38
            n = nmain or len(pts)
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

            if n < len(P):                                        # kuyruk: kesikli
                p.create_line(*sum(P[n - 1:], ()), fill="#777", width=3, dash=(7, 4),
                              capstyle="round")
            p.create_line(*sum(P[:n], ()), fill=RED, width=5, joinstyle="round",
                          capstyle="round")

            for i in range(n - 1):                                # segment uzunlukları
                (x1, y1), (x2, y2) = P[i], P[i + 1]
                px = math.hypot(x2 - x1, y2 - y1)
                real = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                if px < 30 or real < 1e-6:
                    continue
                nx, ny = (y2 - y1) / px, -(x2 - x1) / px           # normal (ekran)
                p.create_text((x1 + x2) / 2 + nx * 13, (y1 + y2) / 2 + ny * 13,
                              text=fmt(real), fill=BLUE, font=("Segoe UI", 9, "bold"))
            for x, y in P[1:n - 1]:                               # büküm noktaları
                p.create_oval(x - 4, y - 4, x + 4, y + 4, fill="white", outline="#111", width=2)
            x0, y0 = P[0]
            p.create_rectangle(x0 - 7, y0 - 7, x0 + 7, y0 + 7, fill="#1a3cff", outline="")
            if tail_cut:
                p.create_text(CW - 8, CH - 8, anchor="se", fill="#555", font=("Segoe UI", 8),
                              text="kesikli: devam aşaması (kısaltılmış, yaklaşık)")

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
            self.note("", RED)
            self.result = None
            try:
                n = int(self.vars["mat"][0].get())
                self.matlabel.config(text=f"{n}– {MATS.get(n, '?')}")
            except ValueError:
                self.matlabel.config(text="?")
            self.draw_diagram()
            self.table.delete(*self.table.get_children())

            keys = self.visible_keys()
            names = {"t": "Kalınlık", "r": "Yarıçap", "ac": "A veya C", "a": "Uzunluk A/C",
                     "l": "Uzunluk L", "h": "Yükseklik H", "ang": "Açı W", "c": "Uzunluk C"}
            if self.type == 5:
                names.update(a="Uzunluk L1", l="Uzunluk L2")
            vals = {}
            for key in ("t", "r") + tuple(keys):
                try:
                    vals[key] = self.val(key)
                except ValueError:
                    return self.fail(f"'{names[key]}' alanına sayı girin (örn. 112,0).")

            code = self.code.get()
            tail = code[1:]
            outer = self.dim.get() in (0, 2)
            mode = self._mode
            t, r = vals["t"], vals["r"]
            d = dict(A=vals.get("a", 0.0), L=vals.get("l", 0.0), H=vals.get("h", 0.0),
                     C=vals.get("c", 0.0), W=vals.get("ang", 0.0))
            sg = 1
            if mode == "angle" and "ang" in vals:
                sg = -1 if d["W"] < 0 else 1       # negatif açı: basamak aşağı
                d["W"] = abs(d["W"])
            ac = int(vals["ac"]) if "ac" in vals else 1
            if "ac" in vals and ac not in (1, 2):
                return self.fail("A veya C alanı 1 ya da 2 olmalı.")
            # İlk hane 4 (41/42/44/444...): toplam boy ekranlardan kalibre edilmiş formülle (multi_total)
            cal_multi = self.type == 4 and tail != ""
            self.unverified = bool(tail)  # Tüm çoklu kodlar: büküm pozisyonları doğrulanmadı
            if tail and not (code[0] in MULTI_FIRST and all(ch in MULTI_CODES for ch in tail)):
                return self.fail(
                    f"Kod {code}: çok haneli kodda ilk hane 1, 2 veya 4; devamı yalnızca "
                    "1, 2, 4 rakamlarından oluşmalı (3 ve 5 için formül yok).")

            try:
                st = stage_calc(self.type, t, r, outer, mode, d, ac=ac,
                                calibrated_multi=cal_multi)
            except (ValueError, ZeroDivisionError) as e:
                return self.fail(str(e) or "Geçersiz ölçü değeri.")
            warn = st["warn"]
            if self.type == 3 or "3" in tail:
                self.unverified = True             # kod 3 formülü doğrulanmadı
            mat1 = st["mat"]
            A = st["A"]
            W = st["W"]
            pts = list(st["pts"])
            bends = list(st["bends"])          # 1. hanenin bükümleri
            total = mat1
            nmain = None
            tail_cut = False

            if tail:
                # ---- sonraki hanelerin bükümleri: 1. hane bitince sırayla uygulanır
                try:
                    tbends, strip = chain_tail(tail, t, r, st, d)
                except (ValueError, ZeroDivisionError) as e:
                    return self.fail(str(e) or "Geçersiz kod dizisi.")
                bends += tbends
                if cal_multi:
                    k = multi_k(tail)
                    total = multi_total(t, r, A, d["L"], d["H"], W, k=k,
                                        C=vals["c"] if mode == "len" else None)
                    ext = max(total - mat1, 0.0)             # kuyruk boyu (gerçek)
                    warn += " Önizlemede kuyruk yönü yaklaşıktır."
                    if code not in KNOWN_CODES:
                        self.unverified = True               # ekstrapolasyon: PLC'ye gitmez
                        warn += (f" UYARI: Kod {code} 41/42/44 örneklerinden ekstrapole edildi"
                                 f" (k={k}); makinede doğrulanmadı, PLC'ye gönderilmez.")
                    elif tail[0] == "4":
                        warn += " Kod 44: tek ekran örneğiyle (826,1) kalibre edildi."
                    if not MULTI_T_RANGE[0] <= t <= MULTI_T_RANGE[1] or self.dim.get() in (1, 3):
                        self.unverified = True
                        warn += (" UYARI: Çok haneli kod formülü yalnızca t=5–6 mm, Dış/Dış Açı"
                                 " ölçüsüyle doğrulandı; bu ölçüler için sonuç GÜVENİLMEZ,"
                                 " PLC'ye gönderilmez.")
                else:
                    # ilk hane 1/2: geometrik ekleme modeli (doğrulanmadı)
                    total = tail_total(t, r, st, tail)
                    ext = st["flat_last"]
                    self.unverified = True
                    warn += (f" UYARI: Kod {code} için ekran örneği yok; toplam boy geometrik modelle"
                             " tahmin edildi, PLC'ye gönderilmez.")
                warn += (" Devam hanelerinin yan dayama konumları yaklaşıktır; "
                         "makineyle doğrulanana kadar PLC aktarımı engellendi.")
                # önizleme: 1. hanenin ucundan devam haneleri
                bx = [x for x, _ in pts]
                by = [y for _, y in pts]
                cap = 0.5 * max(max(bx) - min(bx), max(by) - min(by), 1.0)
                x, y = pts[-1]
                head = st["heading"]
                n_free = sum(1 for _, _, nxt in strip if nxt is None) or 1
                for i, (_, ang, nxt) in enumerate(strip):
                    head += ang
                    seg = nxt if nxt is not None else (ext / n_free if cal_multi else ext)
                    if seg > cap:
                        seg, tail_cut = cap, True
                    x += seg * math.cos(math.radians(head))
                    y += seg * math.sin(math.radians(head))
                    pts.append((x, y))
                nmain = len(st["pts"])
            self.matlen.set(fmt(total))

            bends = [(p, a * sg) for p, a in bends]
            pts = [(x, y * sg) for x, y in pts]
            # F9 kaydı ve PLC için sonucu sakla (büküm sırası: 1. hane, sonra devam haneleri)
            self.result = dict(mat_len=total, bends=[[p, a] for p, a in bends])
            for i, (pos, ang) in enumerate(bends, 1):
                self.table.insert("", "end", values=(i, fmt(pos), fmt(ang)))
            self.draw_preview(pts, nmain if tail else None, tail_cut)
            if code not in KNOWN_CODES:
                warn += f" Kod {code} henüz doğrulanmadı; alanlar ilk haneye ve Dim Type'a göre."
            if self.dim.get() in (1, 3) and self.type == 4 and mode == "len":
                warn += " Kod 4 İç ölçü (A, L, H, C) Excel'de ölçülmedi; İç Açı kuralı uygulandı."
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
    if "--test" in sys.argv:
        sys.exit(0 if self_test() else 1)
    # op.py "Edit OBJ" düğmesi program adını ilk argüman olarak gönderir
    run_gui(sys.argv[1] if len(sys.argv) > 1 else None)