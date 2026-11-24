"""Geometrik OBJ/EXE kontrolü. Gerçek makine doğruluğunu garanti etmez."""
import math
from editobj import table_from_result

def test():
    cases = [
        {'mat':1000.0,'bends':[(110.0,-60),(148.2,60),(369.2,60),(390.6,-60)]},
        {'mat':800.0,'bends':[(75.0,-45),(220.0,45)]},
        {'mat':1200.0,'bends':[(210.0,-90),(440.0,90),(900.0,-45)]},
    ]
    for c in cases:
        tb=table_from_result(c)
        used=tb.rows[:tb.used()]
        assert all(x['obj']>=0 for x in used)
        assert math.isclose(sum(x['obj'] for x in used),c['mat'],abs_tol=1e-6)
        assert math.isclose(used[-1]['exe'],c['mat'],abs_tol=1e-6)
        print('OK: ',len(c['bends']),'büküm, toplam',used[-1]['exe'])
    print('Geometrik tutarlılık başarılı; makine doğruluğu ayrı doğrulanmalı.')

if __name__=='__main__':test()
