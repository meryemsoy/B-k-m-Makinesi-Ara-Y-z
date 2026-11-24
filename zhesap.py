"""Z büküm açınım ve büküm ekseni hesabı (tkinter'dan bağımsız, saf fonksiyon).

Girdiler (mm, derece):
    t  kalınlık          r  iç radius        k  K-faktörü
    A  alt kol           L  toplam boy       H  yükseklik       W  açı

Formüller:
    eğik boy     = (H - t) / sin W
    yatay        = (H - t) / tan W
    C            = L - A - yatay
    dış setback  = (r + t) * tan(W/2)
    iç setback   = r * tan(W/2)
    büküm payı   = W * pi/180 * (r + k*t)
    açınım       = A + eğik + C - 2*(dış + iç) + 2*BA
    eksen 1      = A - dış + BA/2
    eksen 2      = A - dış + BA + (eğik - dış - iç) + BA/2

Eksen konumları açınımın A ucundan ölçülür. Makinenin side-stop değeri parmak
ve kalıp ofsetini de içerdiği için bu değerlerden farklı olabilir.
"""

import math


def z_hesapla(t, r, k, A, L, H, W):
    """Hesap sonuçlarını sözlük olarak döndürür. Geçersiz girdide ValueError."""
    if t <= 0 or r < 0 or k < 0:
        raise ValueError("Kalınlık > 0, radius ve k >= 0 olmalı.")
    if not (0 < W < 90):
        raise ValueError("Açı 0° ile 90° arasında olmalı.")
    if H <= t:
        raise ValueError("H, kalınlıktan büyük olmalı.")

    th = math.radians(W)
    dh = H - t
    egik = dh / math.sin(th)
    yatay = dh / math.tan(th)
    C = L - A - yatay
    if C < 0:
        raise ValueError("Üst kol C negatif çıktı; L, A ve H uyumsuz.")

    dis = (r + t) * math.tan(th / 2)
    ic = r * math.tan(th / 2)
    ba = th * (r + k * t)
    acinim = A + egik + C - 2 * (dis + ic) + 2 * ba

    e1 = A - dis + ba / 2
    e2 = A - dis + ba + (egik - dis - ic) + ba / 2
    if e2 <= e1:
        raise ValueError("Eğik kol çok kısa; iki büküm ekseni çakışıyor.")

    return dict(
        mat_len=acinim, egik=egik, yatay=yatay, C=C,
        dis_setback=dis, ic_setback=ic, ba=ba, eksen1=e1, eksen2=e2,
    )


def k_geri_hesapla(hedef_acinim, t, r, A, L, H, W):
    """Makinedeki Mat. Length değerine uyan K-faktörünü bulur."""
    s = z_hesapla(t, r, 0.0, A, L, H, W)
    th = math.radians(W)
    return (hedef_acinim - s["mat_len"]) / (2 * th * t)


def z_bukumler(sonuc, W):
    """Büküm tablosu satırları (side-stop, açı): uzak uçtan başlayarak.

    Mevcut programlardaki düzene uyar: konumlar azalan sırada, ilk büküm
    negatif, ikincisi pozitif açı.
    """
    return [(sonuc["eksen2"], -abs(W)), (sonuc["eksen1"], abs(W))]


if __name__ == "__main__":
    s = z_hesapla(t=5, r=5, k=0.4, A=82, L=152, H=95, W=60.9)
    for key, val in s.items():
        print(f"{key:12s} {val:9.3f}")
    print("k (makine 202,2):", round(k_geri_hesapla(202.2, 5, 5, 82, 152, 95, 60.9), 3))
