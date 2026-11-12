"""Offline OCR, with conservative recognition of the Amazon label footer."""
from pathlib import Path
import re
import shutil

def executable():
    candidates=[Path(__file__).resolve().parent.parent/'ocr/tesseract.exe',
                Path('C:/Program Files/Tesseract-OCR/tesseract.exe'),
                Path.home()/'AppData/Local/Programs/Tesseract-OCR/tesseract.exe']
    return next((str(p) for p in candidates if p.is_file()),shutil.which('tesseract'))

def read_page(path,index):
    import fitz,pytesseract
    from PIL import Image
    engine=executable()
    if not engine:raise RuntimeError('Offline OCR is missing. Use the app ZIP with bundled OCR or install Tesseract.')
    pytesseract.pytesseract.tesseract_cmd=engine
    with fitz.open(path) as doc:
        pix=doc[index].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
    image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    def lines(crop):
        data=pytesseract.image_to_data(crop,config='--psm 6',output_type=pytesseract.Output.DICT,timeout=30)
        groups={}
        for i,word in enumerate(data['text']):
            if not word.strip():continue
            key=(data['block_num'][i],data['par_num'][i],data['line_num'][i])
            groups.setdefault(key,[]).append((word,float(data['conf'][i])))
        return [(' '.join(w for w,c in words),min(c for w,c in words)) for words in groups.values()]
    footer=lines(image.crop((0,int(image.height*.83),image.width,image.height)))
    products=[(text,confidence) for text,confidence in footer if re.fullmatch(r'.+?\s*\[\d{1,6}\]',text)]
    if products and any('amazon.in' in text.lower() for text,_ in footer):
        top=lines(image.crop((0,0,int(image.width*.55),int(image.height*.23))))
        awbs=[(re.search(r'\bAWB\s*[:#]?\s*(\d{12})\b',text,re.I),confidence) for text,confidence in top]
        awbs=[(m[1],c) for m,c in awbs if m]
        if len(awbs)==1 and awbs[0][1]>=80 and all(c>=80 for _,c in products):
            return 'Sold on: www.amazon.in\nAWB '+awbs[0][0]+'\n'+'\n'.join(t for t,_ in products),True
    return pytesseract.image_to_string(image,timeout=60),False
