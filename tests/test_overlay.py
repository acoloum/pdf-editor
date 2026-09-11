from pathlib import Path
from PIL import Image
import pymupdf
from pdf_editor.assets import AssetStore
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.model import Overlay

def test_copy_and_flatten_preserves_alpha(tmp_path, pdf_bytes):
    source = tmp_path / "stamp.png"
    im = Image.new("RGBA", (80, 40), (0,0,0,0))
    for x in range(10, 30):
        for y in range(10, 30):
            im.putpixel((x,y),(255,0,0,255))
    im.save(source)
    path = AssetStore(tmp_path / "assets").import_png(source, True)
    source.unlink()
    layer = Overlay("1", 0, str(path), (200, 200, 280, 240), 0)
    out = flatten_overlays(pdf_bytes, (layer,))
    with pymupdf.open(stream=out) as doc:
        pix = doc[0].get_pixmap()
        assert pix.pixel(215, 215)[:3] == (255,0,0)
        assert pix.pixel(205, 205)[:3] == (255,255,255)

def test_repeated_save_does_not_duplicate(tmp_path, source_path):
    from pdf_editor.document.session import DocumentSession
    from pdf_editor.document.save import save_as
    path = tmp_path / "stamp.png"
    Image.new("RGBA",(20,20),(255,0,0,100)).save(path)
    with DocumentSession.open(source_path) as s:
        s.set_overlays((Overlay("1",0,str(path),(200,200,240,240),45),))
        save_as(s,tmp_path/"a.pdf")
        save_as(s,tmp_path/"b.pdf")
        with pymupdf.open(tmp_path/"a.pdf") as a, pymupdf.open(tmp_path/"b.pdf") as b:
            assert a[0].get_pixmap().samples == b[0].get_pixmap().samples
        assert len(s.overlays) == 1

