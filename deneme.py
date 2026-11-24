"""ZW dosyalarinin ilk geometri ve ek büküm alanlarini analiz eder; PLC'ye baglanmaz.
Kullanim: python zw_derin_analiz.py 'Bara Bükme Makinası.zip'
"""
import csv,struct,zipfile,sys,collections,math
archive=sys.argv[1] if len(sys.argv)>1 else 'Bara Bükme Makinası.zip'
fields={4:'kayitli_uzunluk',12:'genislik',20:'kalinlik',28:'yaricap',36:'takim',137:'AC_secimi',145:'AC',153:'L',161:'H',169:'W',185:'boyut_tipi',193:'ek_193',201:'ek_201',209:'ek_209',217:'ek_217'}
rows=[]
with zipfile.ZipFile(archive) as z:
 for name in z.namelist():
  if not name.lower().endswith('.zw') or '/zwj/program/' not in name.lower():continue
  b=z.read(name)
  if len(b)<225:continue
  try:
   d={label:round(struct.unpack_from('<d',b,pos)[0],5) for pos,label in fields.items()}
   d['bukumsayisi_adayi_181']=struct.unpack_from('<i',b,181)[0]
   if any(not math.isfinite(v) or abs(v)>1e7 for v in (d[x] for x in ['kayitli_uzunluk','genislik','kalinlik','yaricap','takim'])):continue
   d['dosya']=name;d['bayt']=len(b);rows.append(d)
  except (struct.error,OverflowError):pass
with open('zw_genisletilmis.csv','w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=['dosya','bayt']+list(fields.values())+['bukumsayisi_adayi_181']);w.writeheader();w.writerows(rows)
base=['genislik','kalinlik','yaricap','takim','AC_secimi','AC','L','H','W','boyut_tipi']
extra=['bukumsayisi_adayi_181','ek_193','ek_201','ek_209','ek_217']
def conflicts(keys):
 g=collections.defaultdict(list)
 for r in rows:
  if r['bayt']!=1453:continue
  g[tuple(r[k] for k in keys)].append(r['kayitli_uzunluk'])
 return sum(len(v)>1 and max(v)-min(v)>1 for v in g.values()),sum(len(v) for v in g.values() if len(v)>1 and max(v)-min(v)>1)
print('Okunan program:',len(rows));print('Ilk alanlarla celiski (grup,kayit):',conflicts(base));print('Ek alanlarla celiski (grup,kayit):',conflicts(base+extra));print('Rapor: zw_genisletilmis.csv')
