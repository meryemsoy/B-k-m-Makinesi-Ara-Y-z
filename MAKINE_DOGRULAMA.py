"""Makine ekranındaki değerleri koddan bağımsız doğrulama aracı.

Çalıştırma: python MAKINE_DOGRULAMA.py
Programın giriş ölçülerini anasayfa1.PRESETS veya kayıtlı programlardan okur.
"""
from editobj import program_data, compare_machine_reference

# Fotoğraftaki program; burada yalnızca TEST verisi vardır, formül katsayısı yoktur.
REFERENCES = {
    "2243438.ZW": {
        "obj": [110.0, 38.2, 221.0, 21.4, 678.0],
        "exe": [110.0, 148.2, 369.2, 390.6, 1068.6],
    }
}

for name, ref in REFERENCES.items():
    try:
        _, result = program_data(name)
        report = compare_machine_reference(result, ref["obj"], ref["exe"])
        print(f"\n{name}: {'GECTI' if report['passed'] else 'KALDI'} (tolerans +/-0.5 mm)")
        if report.get('reason'):
            print(report['reason'], 'Hesaplanan:', report['predicted_rows'], 'Makine:', report['machine_rows'])
        for row in report['rows']:
            print(f"{row['row']}: OBJ {row['obj_pred']:.2f}/{row['obj_real']:.2f} "
                  f"hata {row['obj_error']:+.2f} | EXE {row['exe_pred']:.2f}/{row['exe_real']:.2f} "
                  f"hata {row['exe_error']:+.2f} | {'OK' if row['passed'] else 'HATA'}")
    except Exception as exc:
        print(f"{name}: hesaplama yapılamadı: {exc}")
