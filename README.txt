FinTrack v4 (fresh)
--------------------------------
Estructura esperada:

fintrack_v4_fresh/
├─ app.py
├─ requirements.txt
├─ templates/
│  ├─ base.html
│  ├─ dashboard.html
│  ├─ transactions.html
│  └─ import_csv.html
├─ static/
└─ uploads/   (carpeta para comprobantes)

Cómo ejecutar (Windows):
1) Abrí PowerShell en esta carpeta.
2) python -m venv .venv
3) .\.venv\Scripts\Activate
4) pip install -r requirements.txt
5) python 1.py  (también podés usar `python app.py` si preferís)
6) Navegá a http://127.0.0.1:8080

Notas:
- Si no instalás pandas/openpyxl, igual podés importar CSV.
- La carpeta 'uploads' puede estar vacía al inicio (se crea igual).
