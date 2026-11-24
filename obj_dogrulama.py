"""Makine referansı ile mevcut hesaplamayı karşılaştırır; PLC'ye komut göndermez."""
from editobj import compare_machine_reference, program_data

REFERENCES = {
    '2243438.ZW': {
        'obj': [110.0, 38.2, 221.0, 21.4, 678.0],
        'exe': [110.0, 148.2, 369.2, 390.6, 1068.6],
    }
}

if __name__ == '__main__':
    for name, ref in REFERENCES.items():
        try:
            _, result = program_data(name)
            report = compare_machine_reference(result, ref['obj'], ref['exe'])
            print(f'\n{name}: {"PASS" if report["passed"] else "FAIL"}')
            for row in report['rows']:
                print(f"{row['row']:2} OBJ: {row['obj_pred']:8.1f} / {row['obj_real']:8.1f} "
                      f"hata {row['obj_error']:+6.1f} | EXE: {row['exe_pred']:8.1f} / "
                      f"{row['exe_real']:8.1f} hata {row['exe_error']:+6.1f}")
        except (KeyError, ValueError, ImportError) as exc:
            print(f'{name}: Kayıt yüklenemedi: {exc}')
