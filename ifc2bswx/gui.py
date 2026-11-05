"""Desktop window for the IFC -> BSWX converter (tkinter)."""
import json, multiprocessing, os, queue, sys, threading, time, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = 'IFC to BSWX Converter'
VERSION = '1.0.0'
SETTINGS = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'IFCtoBSWX', 'settings.json')
QUALITY = {'Fine (0.2 mm)': 0.2, 'Normal (0.5 mm)': 0.5, 'Coarse (1.0 mm)': 1.0}


def resource(name):
    base = getattr(sys, '_MEIPASS', None)
    if base: return os.path.join(base, name)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'packaging', name)


class App(tk.Tk):
    def __init__(self, files=()):
        super().__init__()
        self.title(f'{APP_NAME} {VERSION}')
        self.minsize(720, 560)
        try: self.iconbitmap(resource('app.ico'))
        except Exception: pass
        self.q = queue.Queue(); self.worker = None; self.cancel_flag = False; self.last_output = None
        s = self._load_settings()
        self.out_mode = tk.StringVar(value=s.get('out_mode', 'same'))
        self.out_dir = tk.StringVar(value=s.get('out_dir', ''))
        self.quality = tk.StringVar(value=s.get('quality', 'Normal (0.5 mm)'))
        self.extra = tk.BooleanVar(value=s.get('extra', True))
        self.colours = tk.BooleanVar(value=s.get('colours', False))
        cores = multiprocessing.cpu_count()
        self.threads = tk.IntVar(value=min(s.get('threads', min(8, cores)), cores))
        self.last_dir = s.get('last_dir', '')
        self._build(cores)
        for f in files: self._add(f)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(100, self._poll)

    # ------------------------------------------------------------------ layout
    def _build(self, cores):
        pad = dict(padx=8, pady=4)
        fr = ttk.LabelFrame(self, text='IFC files'); fr.pack(fill='both', expand=True, **pad)
        self.files = tk.Listbox(fr, selectmode='extended', height=6)
        self.files.pack(side='left', fill='both', expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(fr, command=self.files.yview); sb.pack(side='left', fill='y', pady=8)
        self.files.config(yscrollcommand=sb.set)
        bf = ttk.Frame(fr); bf.pack(side='left', fill='y', padx=8, pady=8)
        ttk.Button(bf, text='Add files…', command=self._browse_files).pack(fill='x')
        ttk.Button(bf, text='Remove', command=self._remove).pack(fill='x', pady=4)
        ttk.Button(bf, text='Clear', command=lambda: self.files.delete(0, 'end')).pack(fill='x')

        of = ttk.LabelFrame(self, text='Output'); of.pack(fill='x', **pad)
        ttk.Radiobutton(of, text='Same folder as each IFC file', variable=self.out_mode, value='same',
                        command=self._toggle_out).grid(row=0, column=0, columnspan=3, sticky='w', padx=8, pady=(6, 0))
        ttk.Radiobutton(of, text='This folder:', variable=self.out_mode, value='folder',
                        command=self._toggle_out).grid(row=1, column=0, sticky='w', padx=8, pady=6)
        self.out_entry = ttk.Entry(of, textvariable=self.out_dir)
        self.out_entry.grid(row=1, column=1, sticky='ew', pady=6)
        self.out_btn = ttk.Button(of, text='Browse…', command=self._browse_out)
        self.out_btn.grid(row=1, column=2, padx=8, pady=6)
        of.columnconfigure(1, weight=1); self._toggle_out()

        op = ttk.LabelFrame(self, text='Options'); op.pack(fill='x', **pad)
        ttk.Label(op, text='Curved surfaces:').grid(row=0, column=0, sticky='w', padx=8, pady=6)
        ttk.Combobox(op, textvariable=self.quality, values=list(QUALITY), state='readonly', width=16
                     ).grid(row=0, column=1, sticky='w', pady=6)
        ttk.Label(op, text='CPU workers:').grid(row=0, column=2, sticky='e', padx=(24, 8))
        ttk.Spinbox(op, from_=1, to=cores, textvariable=self.threads, width=5).grid(row=0, column=3, sticky='w')
        ttk.Checkbutton(op, text='Copy all IFC properties as attributes', variable=self.extra
                        ).grid(row=1, column=0, columnspan=2, sticky='w', padx=8, pady=(0, 6))
        ttk.Checkbutton(op, text='Use IFC colours (default: grey steel, blue bolts)', variable=self.colours
                        ).grid(row=1, column=2, columnspan=3, sticky='w', padx=(24, 8), pady=(0, 6))

        pf = ttk.Frame(self); pf.pack(fill='x', **pad)
        self.status = ttk.Label(pf, text='Add one or more IFC files, then press Convert.')
        self.status.pack(fill='x')
        self.bar = ttk.Progressbar(pf, maximum=1000); self.bar.pack(fill='x', pady=4)
        bb = ttk.Frame(pf); bb.pack(fill='x')
        self.convert_btn = ttk.Button(bb, text='Convert', command=self._start); self.convert_btn.pack(side='left')
        self.cancel_btn = ttk.Button(bb, text='Cancel', command=self._cancel, state='disabled')
        self.cancel_btn.pack(side='left', padx=6)
        self.open_btn = ttk.Button(bb, text='Open output folder', command=self._open_output, state='disabled')
        self.open_btn.pack(side='right')

        lf = ttk.LabelFrame(self, text='Log'); lf.pack(fill='both', expand=True, **pad)
        self.log = tk.Text(lf, height=10, wrap='none', state='disabled', font=('Consolas', 9))
        ls = ttk.Scrollbar(lf, command=self.log.yview); self.log.config(yscrollcommand=ls.set)
        self.log.pack(side='left', fill='both', expand=True, padx=(8, 0), pady=8); ls.pack(side='left', fill='y', pady=8)

    # ------------------------------------------------------------------ helpers
    def _toggle_out(self):
        st = 'normal' if self.out_mode.get() == 'folder' else 'disabled'
        self.out_entry.config(state=st); self.out_btn.config(state=st)

    def _add(self, path):
        path = os.path.abspath(path)
        if path.lower().endswith('.ifc') and path not in self.files.get(0, 'end'):
            self.files.insert('end', path)

    def _browse_files(self):
        fs = filedialog.askopenfilenames(title='Select IFC files', initialdir=self.last_dir or None,
                                         filetypes=[('IFC files', '*.ifc'), ('All files', '*.*')])
        for f in fs: self._add(f)
        if fs: self.last_dir = os.path.dirname(fs[0])

    def _remove(self):
        for i in reversed(self.files.curselection()): self.files.delete(i)

    def _browse_out(self):
        d = filedialog.askdirectory(title='Output folder', initialdir=self.out_dir.get() or self.last_dir or None)
        if d: self.out_dir.set(d)

    def _write(self, text):
        self.log.config(state='normal'); self.log.insert('end', text + '\n'); self.log.see('end')
        self.log.config(state='disabled')

    def _open_output(self):
        if self.last_output: os.startfile(self.last_output)

    def _load_settings(self):
        try:
            with open(SETTINGS, encoding='utf-8') as f: return json.load(f)
        except Exception: return {}

    def _save_settings(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            with open(SETTINGS, 'w', encoding='utf-8') as f:
                json.dump(dict(out_mode=self.out_mode.get(), out_dir=self.out_dir.get(), quality=self.quality.get(),
                               extra=self.extra.get(), colours=self.colours.get(), threads=self.threads.get(),
                               last_dir=self.last_dir), f, indent=1)
        except Exception: pass

    # ------------------------------------------------------------------ conversion
    def _start(self):
        files = list(self.files.get(0, 'end'))
        if not files: messagebox.showinfo(APP_NAME, 'Add at least one IFC file first.'); return
        if self.out_mode.get() == 'folder':
            out = self.out_dir.get().strip()
            if not out: messagebox.showinfo(APP_NAME, 'Choose an output folder.'); return
            os.makedirs(out, exist_ok=True)
        jobs = []
        for f in files:
            folder = self.out_dir.get().strip() if self.out_mode.get() == 'folder' else os.path.dirname(f)
            jobs.append((f, os.path.join(folder, os.path.splitext(os.path.basename(f))[0] + '.bswx')))
        exist = [d for _, d in jobs if os.path.exists(d)]
        if exist and not messagebox.askyesno(APP_NAME, f'{len(exist)} output file(s) already exist and will be '
                                             f'replaced:\n\n' + '\n'.join(exist[:5]) + '\n\nContinue?'):
            return
        self._save_settings()
        self.cancel_flag = False
        self.convert_btn.config(state='disabled'); self.cancel_btn.config(state='normal')
        self.open_btn.config(state='disabled'); self.bar['value'] = 0
        settings = dict(deflection=QUALITY.get(self.quality.get(), 0.5), extra=self.extra.get(),
                        colours=self.colours.get(), threads=max(1, int(self.threads.get())))
        self.worker = threading.Thread(target=self._run, args=(jobs, settings), daemon=True)
        self.worker.start()

    def _run(self, jobs, st):
        from .convert import Options, Converter, Cancelled
        ok = 0
        for i, (src, dst) in enumerate(jobs):
            name = os.path.basename(src)
            self.q.put(('status', f'File {i + 1} of {len(jobs)}: {name} — opening…'))
            opts = Options()
            opts.linear_deflection = st['deflection']; opts.extra_properties = st['extra']
            opts.ifc_colours = st['colours']; opts.threads = st['threads']
            opts.log = lambda *a: self.q.put(('log', ' '.join(map(str, a))))
            opts.progress = lambda n, t, i=i, name=name: self.q.put(
                ('progress', (i + n / max(t, 1) * 0.9) / len(jobs), f'File {i + 1} of {len(jobs)}: {name} — {n}/{t} elements'))
            opts.cancelled = lambda: self.cancel_flag
            tmp = dst + '.part'
            try:
                Converter(src, opts).run(tmp)
                if os.path.exists(dst): os.remove(dst)
                os.replace(tmp, dst)
                ok += 1; self.q.put(('done_file', dst))
            except Cancelled:
                self.q.put(('log', 'Cancelled.'))
                if os.path.exists(tmp): os.remove(tmp)
                break
            except MemoryError:
                self.q.put(('log', f'ERROR: not enough memory for {name}. Close other programs or lower "CPU workers".'))
            except Exception as ex:
                self.q.put(('log', f'ERROR converting {name}: {ex}\n{traceback.format_exc()}'))
            finally:
                if os.path.exists(tmp):
                    try: os.remove(tmp)
                    except OSError: pass
            self.q.put(('progress', (i + 1) / len(jobs), None))
        self.q.put(('finished', ok, len(jobs)))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'log': self._write(msg[1])
                elif kind == 'status': self.status.config(text=msg[1])
                elif kind == 'progress':
                    self.bar['value'] = msg[1] * 1000
                    if msg[2]: self.status.config(text=msg[2])
                elif kind == 'done_file':
                    self.last_output = os.path.dirname(msg[1]); self.open_btn.config(state='normal')
                elif kind == 'finished':
                    ok, total = msg[1], msg[2]
                    text = f'Finished: {ok} of {total} file(s) converted.'
                    self.status.config(text=text); self._write(text)
                    self.convert_btn.config(state='normal'); self.cancel_btn.config(state='disabled')
                    if ok == total: self.bar['value'] = 1000
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _cancel(self):
        self.cancel_flag = True; self.status.config(text='Cancelling…'); self.cancel_btn.config(state='disabled')

    def _close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, 'A conversion is running. Cancel it and quit?'): return
            self.cancel_flag = True
        self._save_settings(); self.destroy()


def run(files=()):
    try:                                   # sharp text on high-DPI screens
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    App(files).mainloop()
