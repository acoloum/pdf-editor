from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from conftest import pdf_bytes, FONT
from test_text import request_for
from pdf_editor.engine.text import replace_text
import pymupdf

root = Path('work/prototype')
root.mkdir(parents=True, exist_ok=True)
data = pdf_bytes.__wrapped__()
result = replace_text(data, request_for(data, str(FONT)))
for name, content in [('before', data), ('after', result)]:
    (root / (name + '.pdf')).write_bytes(content)
    with pymupdf.open(stream=content) as doc:
        doc[0].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(root / (name + '.png'))
