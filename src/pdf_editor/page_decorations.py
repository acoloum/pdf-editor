import io
import math
from pathlib import Path

import pymupdf
from PIL import Image,ImageDraw,ImageFont

from pdf_editor.errors import EditorError


POSITIONS={
    "top_left":("top",0),
    "top_center":("top",1),
    "top_right":("top",2),
    "bottom_left":("bottom",0),
    "bottom_center":("bottom",1),
    "bottom_right":("bottom",2),
}


def _selected_pages(doc,pages):
    selected=tuple(sorted(pages))
    if (not selected or len(selected)!=len(set(selected)) or
            any(not isinstance(page,int) or not 0<=page<doc.page_count for page in selected)):
        raise EditorError("RANGE","選取頁碼無效。")
    return selected


def _number(value,name,minimum=None,maximum=None):
    try:
        result=float(value)
    except (TypeError,ValueError) as exc:
        raise EditorError("RANGE",f"{name}不是有效數值。") from exc
    if not math.isfinite(result) or minimum is not None and result<minimum or maximum is not None and result>maximum:
        raise EditorError("RANGE",f"{name}超出允許範圍。")
    return result


def crop_pages(pdf,pages,margins):
    if len(margins)!=4:
        raise EditorError("CROP","請提供左、上、右、下四個裁切邊距。")
    left,top,right,bottom=(_number(value,"裁切邊距",0) for value in margins)
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        for index in _selected_pages(doc,pages):
            page=doc[index]
            current=page.cropbox
            target=pymupdf.Rect(current.x0+left,current.y0+top,
                current.x1-right,current.y1-bottom)
            if target.width<10 or target.height<10:
                raise EditorError("CROP","裁切後頁面寬度與高度至少需保留 10 點。")
            page.set_cropbox(target)
        return doc.tobytes(garbage=4,deflate=True)


def add_page_numbers(pdf,pages,start,prefix,suffix,position,font_size,font_path):
    if position not in POSITIONS:
        raise EditorError("POSITION","頁碼位置無效。")
    if not isinstance(start,int):
        raise EditorError("RANGE","起始頁碼必須是整數。")
    size=_number(font_size,"頁碼字級",4,72)
    font=Path(font_path)
    if not font.is_file():
        raise EditorError("FONT_INVALID","找不到頁碼字型檔。")
    vertical,alignment=POSITIONS[position]
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_selected_pages(doc,pages)
        for sequence,index in enumerate(selected):
            page=doc[index]
            page.insert_font(fontname="localpdfnumber",fontfile=str(font))
            width,height=page.cropbox.width,page.cropbox.height
            box_height=max(20,size*2.4)
            top=8 if vertical=="top" else height-box_height-8
            rect=pymupdf.Rect(12,top,width-12,top+box_height)
            text=f"{prefix}{start+sequence}{suffix}"
            remaining=page.insert_textbox(rect,text,fontname="localpdfnumber",
                fontsize=size,color=(0,0,0),align=alignment,overlay=True)
            if remaining<0:
                raise EditorError("PAGE_NUMBER","頁碼文字太長，請縮小字級或縮短文字。")
        return doc.tobytes(garbage=4,deflate=True)


def _png_bytes(image):
    buffer=io.BytesIO()
    image.save(buffer,format="PNG")
    return buffer.getvalue()


def _insert_centered(page,image,width):
    page_width,page_height=page.cropbox.width,page.cropbox.height
    target_width=min(width,page_width*0.9)
    target_height=target_width*image.height/image.width
    if target_height>page_height*0.9:
        target_height=page_height*0.9
        target_width=target_height*image.width/image.height
    x=(page_width-target_width)/2
    y=(page_height-target_height)/2
    page.insert_image((x,y,x+target_width,y+target_height),stream=_png_bytes(image),
        keep_proportion=True,overlay=True)


def add_text_watermark(pdf,pages,text,font_size,opacity,angle,font_path):
    if not str(text).strip():
        raise EditorError("WATERMARK","請輸入浮水印文字。")
    size=_number(font_size,"浮水印字級",8,200)
    alpha=_number(opacity,"浮水印透明度",0.01,1)
    rotation=_number(angle,"浮水印角度",-180,180)
    try:
        scale=4
        font=ImageFont.truetype(str(font_path),round(size*scale))
        probe=Image.new("RGBA",(1,1),(0,0,0,0))
        bounds=ImageDraw.Draw(probe).textbbox((0,0),str(text),font=font)
        padding=round(size)
        image=Image.new("RGBA",(bounds[2]-bounds[0]+padding*2,
            bounds[3]-bounds[1]+padding*2),(0,0,0,0))
        ImageDraw.Draw(image).text((padding-bounds[0],padding-bounds[1]),str(text),
            font=font,fill=(70,78,78,round(255*alpha)))
        image=image.rotate(rotation,expand=True,resample=Image.Resampling.BICUBIC)
    except Exception as exc:
        raise EditorError("WATERMARK","無法建立文字浮水印。") from exc
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        for index in _selected_pages(doc,pages):
            _insert_centered(doc[index],image,image.width/scale)
        return doc.tobytes(garbage=4,deflate=True)


def add_image_watermark(pdf,pages,image_path,width_percent,opacity,angle):
    width_ratio=_number(width_percent,"圖片寬度",1,95)/100
    alpha=_number(opacity,"浮水印透明度",0.01,1)
    rotation=_number(angle,"浮水印角度",-180,180)
    path=Path(image_path)
    if not path.is_file():
        raise EditorError("IMAGE","找不到浮水印圖片。")
    try:
        with Image.open(path) as source:
            image=source.convert("RGBA")
        channel=image.getchannel("A").point(lambda value:round(value*alpha))
        image.putalpha(channel)
        image=image.rotate(rotation,expand=True,resample=Image.Resampling.BICUBIC)
    except Exception as exc:
        raise EditorError("IMAGE","無法讀取浮水印圖片。") from exc
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        for index in _selected_pages(doc,pages):
            page=doc[index]
            _insert_centered(page,image,page.cropbox.width*width_ratio)
        return doc.tobytes(garbage=4,deflate=True)
