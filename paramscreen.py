import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import json, math
from pathlib import Path

BG='white'; BLUE='#2222bb'; RED='#e01010'; CYAN='#dceef5'
STORE=Path(__file__).with_name('as_table.json')
KEYS='0.0x0.0x0.0 8.0x5.0x32.1 8.0x5.0x40.0 8.0x5.0x60.1 10.0x15.0x60.0 10.0x5.0x32.0 10.0x5.0x32.1 10.0x5.0x40.0 10.0x5.0x60.0 10.0x5.0x60.1 10.0x15.0x60.0 10.0x15.0x90.0 10.0x15.0x90.1 12.0x15.0x90.0 15.0x15.0x60.1 15.0x15.0x90.0 15.0x15.0x90.1 20.0x15.0x90.0 5.0x5.0x32.0 5.0x5.0x32.1 5.0x5.0x40.0 5.0x5.0x63.0 6.0x5.0x32.0 6.0x5.0x32.1 6.0x5.0x40.1 8.0x5.0x32.0'.split()

def window(parent,title):
    w=tk.Toplevel(parent); w.title(title); w.configure(bg=BG)
    try: w.state('zoomed')
    except tk.TclError: w.geometry('1100x720')
    w.bind('<Escape>',lambda e:w.destroy()); return w

def menu(owner):
    w=window(owner,'Param'); bar=tk.Frame(w,bg=BG); bar.pack(side='bottom',fill='x',padx=10,pady=10)
    def pending(): messagebox.showinfo('Param','Bu ekran için orijinal ekran bilgisi gerekli.',parent=w)
    def correction():
        value=simpledialog.askstring('Angle Correction','Açı düzeltmesi (deg) — yerel değer',initialvalue=owner.v['corr'].get(),parent=w)
        if value is not None:
            try:
                n=float(value.replace(',','.')); assert math.isfinite(n)
                owner.v['corr'].set(f'{n:.1f}'.replace('.',','))
            except (ValueError,AssertionError): messagebox.showerror('Hata','Geçersiz sayı',parent=w)
    for i,(label,cmd) in enumerate([('Password',pending),('Machine\nparameter',owner.open_plc_settings),('Bend\nfactor',pending),('Set\ndefault',pending),('AS-table',lambda:as_table(w)),('Angle\nCorrection',correction),('Machine\ntest',lambda:machine_test(w,owner)),('Back',w.destroy)]):
        bar.columnconfigure(i,weight=1); tk.Button(bar,text=label,command=cmd,bg=RED if i==4 else CYAN,fg='white' if i==4 else 'black',font=('Segoe UI',14),height=2).grid(row=0,column=i,sticky='ew',padx=3)

def as_table(parent):
    w=window(parent,'AS-table'); data={k:[] for k in KEYS}
    if STORE.exists():
        try: data=json.loads(STORE.read_text(encoding='utf-8'))
        except (OSError,ValueError): messagebox.showerror('AS-table','Kayıt okunamadı.',parent=w); w.destroy(); return
    def save():
        try: STORE.write_text(json.dumps(data,indent=2),encoding='utf-8')
        except OSError as e: messagebox.showerror('AS-table',str(e),parent=w)
    body=tk.Frame(w,bg=BG); body.pack(fill='both',expand=True,padx=30,pady=25)
    left=tk.Frame(body,bg=BG); left.pack(side='left',fill='both',expand=True)
    right=tk.Frame(body,bg=BG); right.pack(side='right',fill='both',expand=True,padx=30)
    tk.Label(left,text='AStable list (Thickness / Radius / Tool)',bg=BG,font=('Segoe UI',16)).pack(anchor='w')
    lists=[tk.Listbox(left,font=('Segoe UI',15),exportselection=False) for _ in range(2)]
    for lb in lists: lb.pack(side='left',fill='both',expand=True)
    current=[None]; vars=[tk.StringVar(value='0,0') for _ in range(3)]
    for name,var in zip(('Thickness (mm)','Radius (mm)','Tool (mm)'),vars):
        row=tk.Frame(right,bg=BG); row.pack(fill='x'); tk.Label(row,text=name,bg=BG,font=('Segoe UI',14)).pack(side='left'); tk.Entry(row,textvariable=var,state='readonly',justify='right',width=12).pack(side='right')
    tk.Label(right,text='AStable data',bg=BG,font=('Segoe UI',15)).pack(pady=10)
    table=ttk.Treeview(right,columns=('angle','stroke'),show='headings',height=15)
    for c,t in [('angle','Angle (deg)'),('stroke','Stroke (mm)')]: table.heading(c,text=t); table.column(c,anchor='center',width=150)
    table.pack(fill='both',expand=True)
    def show():
        table.delete(*table.get_children())
        for i,row in enumerate(data.get(current[0],[])): table.insert('', 'end',iid=str(i),values=tuple(f'{v:.1f}'.replace('.',',') for v in row))
    def select(lb):
        if not lb.curselection(): return
        current[0]=lb.get(lb.curselection()[0]);
        for other in lists:
            if other!=lb: other.selection_clear(0,'end')
        for var,val in zip(vars,current[0].split('x')): var.set(val.replace('.',','))
        show()
    def refresh():
        keys=list(data); split=(len(keys)+1)//2
        for lb,ks in zip(lists,(keys[:split],keys[split:])):
            lb.delete(0,'end')
            for k in ks: lb.insert('end',k)
        show()
    def new():
        if w.focus_get()==table and current[0]: edit(True); return
        vals=[]
        for label in ('Thickness','Radius','Tool'):
            v=simpledialog.askfloat('New',label,parent=w,minvalue=0)
            if v is None: return
            vals.append(v)
        key='x'.join(f'{v:.1f}' for v in vals); data.setdefault(key,[]); current[0]=key; save(); refresh()
    def edit(add=False):
        if not current[0]: return
        rows=data[current[0]]; sel=table.selection(); i=int(sel[0]) if sel and not add else len(rows)
        old=rows[i] if i<len(rows) else (0,0)
        a=simpledialog.askfloat('Edit','Angle (deg)',initialvalue=old[0],parent=w)
        if a is None: return
        s=simpledialog.askfloat('Edit','Stroke (mm)',initialvalue=old[1],parent=w)
        if s is None or not all(map(math.isfinite,(a,s))): return
        if i<len(rows): rows[i]=[a,s]
        else: rows.append([a,s])
        save(); show()
    def delete():
        if not current[0]: return
        if w.focus_get()==table and table.selection():
            if messagebox.askyesno('Delete','Satır silinsin mi?',parent=w): del data[current[0]][int(table.selection()[0])]; save(); show()
        elif messagebox.askyesno('Delete','Kalibrasyon kaydı silinsin mi?',parent=w): del data[current[0]]; current[0]=None; save(); refresh()
    for lb in lists: lb.bind('<<ListboxSelect>>',lambda e:select(e.widget))
    table.bind('<Double-1>',lambda e:edit()); w.bind('<Insert>',lambda e:new()); w.bind('<Delete>',lambda e:delete()); w.bind('<Return>',lambda e:edit())
    bar=tk.Frame(w,bg=BG); bar.pack(fill='x',padx=30,pady=10)
    for label,cmd in [('New',new),('Add row',lambda:edit(True)),('Edit',edit),('Delete',delete),('Exit',w.destroy)]: tk.Button(bar,text=label,command=cmd,bg=CYAN).pack(side='left',padx=4)
    tk.Label(w,text='<Insert> New, <Delete> Delete, <Enter> Edit, <ESC> Exit',fg=RED,bg=BG,font=('Segoe UI',16,'bold')).pack(anchor='w',padx=30,pady=10)
    refresh()

def machine_test(parent,owner):
    w=window(parent,'Machine test'); body=tk.Frame(w,bg=BG); body.pack(fill='both',expand=True,padx=35,pady=20)
    outputs=['F1--YV1','F2--YV2','F3--YV3','F4--YV4','F5--YV5','F6--YV6','F7--Servo Enable','F8--Speed Enable','No use','No use']+['']*6
    inputs=['B1','B2','B3','B4','Side-Stop -','Side-Stop +','Side-Stop HLimit+','Side-Stop HLimit-','Foot Switch Left','Foot Switch Right','Emergency stop','Side-Stop Ref Switch','Tool HLimit','Servo Ready','Oil pressure SW','Gas Pressure SW']
    for title,names in [('Output Signal',outputs),('Input Signal',inputs)]:
        col=tk.Frame(body,bg=BG); col.pack(side='left',fill='both',expand=True)
        tk.Label(col,text=title,fg=BLUE,bg=BG,font=('Segoe UI',18,'bold')).pack()
        for i,name in enumerate(names):
            row=tk.Frame(col,bg=BG); row.pack(anchor='w',pady=2); tk.Label(row,text=f'{i:02}',fg=BLUE,bg=BG,font=('Segoe UI',14,'bold')).pack(side='left'); c=tk.Canvas(row,width=28,height=25,bg=BG,highlightthickness=0); c.pack(side='left'); c.create_oval(3,3,24,24,fill='#aaaaaa',outline='#777777'); tk.Label(row,text=name,bg=BG,font=('Segoe UI',13)).pack(side='left')
    col=tk.Frame(body,bg=BG); col.pack(side='left',fill='both',expand=True)
    fields={}
    for label,key in [('D/A Output','da'),('Side-Stop Position (mm)','side'),('Tool Position (mm)','toolpos'),('Angle (deg)','ang')]:
        tk.Label(col,text=label,fg=BLUE,bg=BG,font=('Segoe UI',14,'bold')).pack(pady=(20,2)); fields[key]=tk.StringVar(value='—'); tk.Label(col,textvariable=fields[key],fg=RED,bg='#efefe9',width=15,font=('Segoe UI',18,'bold'),relief='sunken').pack()
    status=tk.StringVar(); tk.Label(col,textvariable=status,bg=BG,wraplength=250).pack(pady=20)
    def update():
        vals={}; ok=False
        if owner.plc is not None:
            try: vals,_,ok=owner.plc.snapshot()
            except Exception: pass
        for k,v in fields.items(): v.set(str(vals[k]).replace('.',',') if ok and k in vals and k!='da' else '—')
        status.set('Encoder: PLC bağlı' if ok else 'PLC bağlantısı yok'); w.after(500,update)
    update(); tk.Label(w,text='I/O ve D/A adresleri doğrulanmadı — gri: bilinmiyor. Salt okunur izleme.',bg=BG,fg=BLUE).pack(); tk.Label(w,text='Caution! Only trained operators are allowed to perform manual operation!',fg=BLUE,bg=BG,font=('Segoe UI',15,'bold')).pack(pady=12); tk.Button(w,text='Back',command=w.destroy,bg=CYAN).pack(pady=5)
