"""Offline workbook validation. No PLC communication, no training on target values."""
import csv, sys, math
from collections import Counter, defaultdict
from pathlib import Path
import openpyxl
import types
# Offline only: prevent importing PLC client when dependency is absent.
if "asyncua" not in sys.modules:
    stub=types.ModuleType("asyncua"); stub.Client=None; stub.ua=None; sys.modules["asyncua"]=stub
import anasayfa1 as a

def main(path):
    w=openpyxl.load_workbook(path,data_only=True)
    steps=defaultdict(list)
    for name,no,obj,exe,angle,stroke in list(w['Adimlar'].values)[1:]:
        steps[name].append((no,obj,exe,angle))
    dims={'Outside':0,'Inside':1,'Outside Angle':2,'Inside Angle':3}
    output=[]; stats=defaultdict(list)
    for row in list(w['Programlar'].values)[1:]:
        name,n,code,width,t,r,tool,material,recorded=row[:9]; code=str(code)
        rec=dict(program=name,kod=code,kayit_mat=recorded,exe_son='',obj_toplam='',hesap_mat='',fark='',durum='')
        try:
            st=sorted(steps[name]); ids=[z[0] for z in st]
            if not st or len(set(ids))!=len(ids): raise ValueError('Adım yok veya aynı program adı/adım numarası tekrarlı')
            rec['exe_son']=st[-1][2]; rec['obj_toplam']=sum(z[1] for z in st)
            if abs(rec['exe_son']-rec['obj_toplam'])>.05: raise ValueError('EXE ve OBJ toplamı uyumsuz')
            stages=[]
            for j,ch in enumerate(code):
                digit,dim,ac,A,L,H,C,W=row[9+j*8:17+j*8]
                if str(digit)!=ch: raise ValueError('Hane kodu uyumsuz')
                dim=dims[dim]; A,L,H,C,W=[float(v or 0) for v in (A,L,H,C,W)]
                if ch in '12' and dim in (2,3): C,W,H=L,H,0
                if ch in '1234' and dim in (2,3) and ac not in (1,2): raise ValueError('A/C veya C/H seçimi geçersiz')
                stages.append(dict(ac=int(ac or 1),A=A,L=L,H=H,C=C,W=W,dim=dim))
            res=a.calc_stages(code,t,r,stages[0]['dim'],stages)
            rec['hesap_mat']=res['mat']; rec['fark']=res['mat']-rec['exe_son']
            expected=sorted(abs(v) for _,v in res['bends'] if abs(v)>.01)
            actual=sorted(abs(z[3]) for z in st if z[3] is not None and abs(z[3])>.01)
            if len(expected)!=len(actual) or any(abs(x-y)>1 for x,y in zip(expected,actual)):
                rec['durum']='Açı uyumsuz';
            else:
                rec['durum']='Tolerans içinde' if abs(rec['fark'])<=.5 else 'Tolerans dışında';stats[code].append(abs(rec['fark']))
        except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError) as e: rec['durum']=str(e)
        output.append(rec)
    dest=Path(__file__).with_name('dogrulama.csv')
    with dest.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(output[0]),delimiter=';');writer.writeheader();writer.writerows(output)
    print('Toplam:',len(output),'Açı uyumlu:',sum(map(len,stats.values())))
    for code,errs in sorted(stats.items()): print(code,len(errs),round(sum(errs)/len(errs),3),sum(e<=.5 for e in errs))
    print('Durum:',Counter(x['durum'] for x in output))
if __name__=='__main__': main(sys.argv[1])
