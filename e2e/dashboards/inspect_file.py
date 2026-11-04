"""Print the readable content of a downloaded dashboard file (xlsx cells / pdf text). Runs in the odoo image (openpyxl, PyPDF2)."""
import io
import sys
import zipfile

path = sys.argv[1]
data = open(path, 'rb').read()
if data[:4] == b'%PDF':
    import PyPDF2
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    print('PDF pages=%d' % len(reader.pages))
    for page in reader.pages:
        print(page.extract_text())
elif data[:2] == b'PK':
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(data))
    print('XLSX sheets=%s' % book.sheetnames)
    formulas = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.startswith('xl/worksheets/') and b'<f>' in z.read(n):
                formulas += 1
    print('FORMULA_SHEETS=%d' % formulas)
    for ws in book.worksheets:
        for row in ws.iter_rows():
            vals = ['%s' % c.value for c in row if c.value is not None]
            if vals:
                print(' | '.join(vals))
else:
    print('NOT A PDF OR XLSX', data[:20])
