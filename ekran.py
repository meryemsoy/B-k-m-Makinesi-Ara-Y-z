#!/usr/bin/env python3
"""Ortak ekran yardımcıları: ekran boyutuna göre ölçekleme ve tam ekran.

op.py, anasayfa.py (ve isterseniz load.py) bu dosyayı kullanır.
  olcek_uygula(root)  -> yazı/bileşen ölçeğini ekrana göre ayarlar, katsayıyı (k) döner
  tam_ekran(pencere)  -> pencereyi ekranı kaplayacak şekilde büyütür;
                         F11 ile gerçek tam ekran açılıp kapanır
"""
import tkinter as tk

BASE_W, BASE_H = 1300, 950      # arayüzün rahat sığdığı tasarım boyutu (piksel)
K_MIN, K_MAX = 0.7, 2.5         # ölçek katsayısı sınırları

_k = None                       # uygulama başına bir kez hesaplanır


def olcek_uygula(root):
    """Tk ölçeğini ekran boyutuna göre ayarlar. Punto ile yazılan fontlar ve
    karakter cinsinden genişlikler (width=...) buna göre büyür/küçülür."""
    global _k
    if _k is not None:
        return _k
    k = min(root.winfo_screenwidth() / BASE_W,
            root.winfo_screenheight() / BASE_H)
    k = max(K_MIN, min(k, K_MAX))
    try:
        piksel_pt = root.winfo_fpixels("1i") / 72.0
        root.tk.call("tk", "scaling", piksel_pt * k)
    except tk.TclError:
        pass
    _k = k
    return k


def tam_ekran(win, fullscreen=False):
    """Pencereyi büyütür (başlık çubuğu kalır). F11: gerçek tam ekran aç/kapat.
    fullscreen=True verilirse doğrudan tam ekran açılır."""
    durum = {"fs": False}

    def buyut():
        try:
            win.state("zoomed")                  # Windows
        except tk.TclError:
            try:
                win.attributes("-zoomed", True)  # Linux
            except tk.TclError:
                pass

    def ayarla(fs):
        durum["fs"] = fs
        try:
            win.attributes("-fullscreen", fs)
        except tk.TclError:
            pass
        if not fs:
            buyut()

    win.bind("<F11>", lambda e: ayarla(not durum["fs"]))
    ayarla(fullscreen)