import zipfile
import struct
import os
import pandas as pd

zip_yolu = "program.zip"
cikti_excel = "zw_program_listesi.xlsx"

ozet_satirlar = []
adim_satirlar = []

with zipfile.ZipFile(zip_yolu, 'r') as z:
    for info in z.infolist():
        if info.is_dir() or not info.filename.lower().endswith('.zw'):
            continue
            
        data = z.read(info.filename)
        dosya_adi = os.path.basename(info.filename)
        program_kodu = os.path.splitext(dosya_adi)[0]
        boyut = len(data)
        
        # Standart yapı dışındaki dosyaları (ör. 24576 baytlık 2237200.ZW) işaretle
        if boyut < 1405 or boyut > 2000:
            ozet_satirlar.append({
                "Dosya Adı": dosya_adi,
                "Program Kodu": program_kodu,
                "Dosya Boyutu (Bayt)": boyut,
                "Durum": "Farklı Format / Özel Dosya",
                "Parametre 1 (Boy)": None,
                "Parametre 2": None,
                "Parametre 3": None,
                "Parametre 4": None,
                "Parametre 5": None,
                "Büküm Sayısı": None
            })
            continue

        # Başlık bilgilerini (Header) oku
        mod_tipi = struct.unpack_from('<i', data, 0)[0]
        p1, p2, p3, p4, p5 = struct.unpack_from('<5d', data, 4)
        
        # Dosya boyutuna göre adım sayısını hesapla (1405 -> 1 adım, 1453 -> 2 adım, vb.)
        adim_sayisi = (boyut - 1357) // 48
        
        ozet_satirlar.append({
            "Dosya Adı": dosya_adi,
            "Program Kodu": program_kodu,
            "Dosya Boyutu (Bayt)": boyut,
            "Durum": "Standart",
            "Parametre 1 (Boy)": round(p1, 3),
            "Parametre 2": round(p2, 3),
            "Parametre 3": round(p3, 3),
            "Parametre 4": round(p4, 3),
            "Parametre 5": round(p5, 3),
            "Büküm Sayısı": adim_sayisi
        })
        
        # Her bir büküm adımının blok verilerini oku (128. bayttan itibaren 48 baytlık bloklar)
        for i in range(adim_sayisi):
            offset = 128 + (i * 48)
            if offset + 48 <= boyut:
                adim_kodu = struct.unpack_from('<i', data, offset)[0]
                d1, d2, d3, d4, d5 = struct.unpack_from('<5d', data, offset + 8)
                adim_satirlar.append({
                    "Program Kodu": program_kodu,
                    "Adım No": i + 1,
                    "Adım Kodu": adim_kodu,
                    "Değer 1": round(d1, 3) if abs(d1) < 1e6 else None,
                    "Değer 2": round(d2, 3) if abs(d2) < 1e6 else None,
                    "Değer 3": round(d3, 3) if abs(d3) < 1e6 else None,
                    "Değer 4": round(d4, 3) if abs(d4) < 1e6 else None,
                    "Değer 5": round(d5, 3) if abs(d5) < 1e6 else None,
                })

# Excel'e yazdır
df_ozet = pd.DataFrame(ozet_satirlar)
df_adimlar = pd.DataFrame(adim_satirlar)

with pd.ExcelWriter(cikti_excel, engine='openpyxl') as writer:
    df_ozet.to_excel(writer, sheet_name='Program Listesi', index=False)
    df_adimlar.to_excel(writer, sheet_name='Büküm Adımları', index=False)

print(f"Tamamlandı! Toplam {len(df_ozet)} program '{cikti_excel}' dosyasına aktarıldı.")