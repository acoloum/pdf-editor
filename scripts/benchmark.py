from pathlib import Path
import time
import threading
import json
import platform
import hashlib
import psutil
import pymupdf
from pdf_editor.engine.render import render_page
from pdf_editor.engine.text import extract_runs,replace_text
from pdf_editor.engine.fonts import default_font
from pdf_editor.model import TextReplacement
from pdf_editor.document.save import write_pdf

def main():
    output=Path("work/benchmark")
    output.mkdir(parents=True,exist_ok=True)
    with pymupdf.open() as doc:
        page=doc.new_page()
        page.insert_font(fontname="noto",fontfile=str(default_font()))
        page.insert_text((50,100),"品質檢驗 ABC 123",fontname="noto",fontsize=16)
        doc.subset_fonts(fallback=True)
        source=doc.tobytes(garbage=4,deflate=True)
    results=[]
    for count in [1,20,100]:
        with pymupdf.open() as doc, pymupdf.open(stream=source) as origin:
            for _ in range(count):
                doc.insert_pdf(origin)
            data=doc.tobytes(garbage=4,deflate=True)
        peak=[0]
        stop=threading.Event()
        def sample():
            process=psutil.Process()
            while not stop.is_set():
                try:
                    peak[0]=max(peak[0],sum(p.memory_info().rss for p in [process]+process.children(recursive=True)))
                except psutil.Error:
                    pass
                stop.wait(0.05)
        worker=threading.Thread(target=sample)
        worker.start()
        t=time.perf_counter()
        with pymupdf.open(stream=data) as doc:
            pages=doc.page_count
        opened=time.perf_counter()-t
        t=time.perf_counter()
        render_page(data,0,1.25,pixel_ratio=2.0)
        render=time.perf_counter()-t
        run=extract_runs(data,0)[0]
        req=TextReplacement(hashlib.sha256(data).hexdigest(),0,run.id,
            "檢測完成",(50,70,350,120),str(default_font()),16,(0,0,0))
        t=time.perf_counter()
        changed=replace_text(data,req)
        edit=time.perf_counter()-t
        t=time.perf_counter()
        write_pdf(changed,output/f"{count}.pdf",True)
        save=time.perf_counter()-t
        stop.set()
        worker.join()
        results.append({"pages":pages,"input_bytes":len(data),"open_seconds":opened,
            "render_seconds":render,"edit_seconds":edit,"save_seconds":save,"peak_rss_bytes":peak[0]})
    info={"os":platform.platform(),"python":platform.python_version(),"cpu":platform.processor(),
        "physical_memory_bytes":psutil.virtual_memory().total,"sample_interval_seconds":0.05,
        "method":"單程序引擎測量；頁面以 125% 縮放及 2 倍像素密度渲染；主程序及子程序 RSS 每 50 毫秒採樣，不含其他應用程式。",
        "results":results}
    (output/"results.json").write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(results))
if __name__=="__main__":
    main()
