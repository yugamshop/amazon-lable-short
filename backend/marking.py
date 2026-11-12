import re
import fitz
BLUE=(30/255,127/255,216/255)
def blank(page,rect):
    if not page.rect.contains(rect):return False
    pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),clip=rect,colorspace=fitz.csGRAY,alpha=False)
    return bool(pix.samples) and min(pix.samples)>=248
def badge(page,quantity,invoice=False):
    text=f'QTY: {quantity}';size=26;width=fitz.get_text_length(text,fontname='hebo',fontsize=size)+16
    if invoice:
        signatures=page.search_for('Authorised Signatory')+page.search_for('Authorized Signatory')
        start=max((r.y1 for r in signatures),default=page.rect.height*.75)+10
        candidates=[fitz.Rect((page.rect.width-width)/2,y,(page.rect.width+width)/2,y+40) for y in range(int(start),int(page.rect.height-48),5)]
    else:candidates=[fitz.Rect(page.rect.width-width-12,y,page.rect.width-12,y+40) for y in (8,16,26,40,54,68)]
    rect=next((r for r in candidates if blank(page,r)),None)
    if rect is None:return False
    page.draw_rect(rect,color=BLUE,fill=(1,1,.65),width=1.2)
    if invoice:
        page.insert_text((rect.x0+8,rect.y0+29),text,fontname='hebo',fontsize=size,color=BLUE)
    return True
def mark_order(document,indices,quantity):
    notes=[];count=0
    if quantity<2:return notes,count
    invoice_indices=[i for i in indices if re.search(r'tax\s*invoice|invoice\s*/\s*bill',document[i].get_text(),re.I)]
    label=document[indices[0]]
    if indices[0] not in invoice_indices or re.search(r'\bAWB\b|Ship\s*To',label.get_text(),re.I):
        if badge(label,quantity):count+=1
        else:notes.append('Label quantity skipped: no safe blank top-right area')
    if invoice_indices:
        invoice=document[invoice_indices[-1]]
        if badge(invoice,quantity,True):count+=1
        else:notes.append('Invoice quantity skipped: no safe blank space below signature')
        words=invoice.get_text('words');headers=[w for w in words if w[4].strip().lower()=='qty']
        for header in headers:
            for word in words:
                if word[4]==str(quantity) and word[1]>header[3] and word[1]<header[3]+100 and abs((word[0]+word[2]-header[0]-header[2])/2)<14:
                    annotation=invoice.add_highlight_annot(fitz.Rect(word[:4]));annotation.set_colors(stroke=(1,.85,0));annotation.update();break
    else:notes.append('Invoice quantity skipped: invoice not identified')
    return notes,count
def apply_marks(output,orders,quantities=True):
    reports=[];marks=0
    with fitz.open(output) as doc:
        offset=0
        for shipment in orders:
            indices=list(range(offset,offset+len(shipment.pages)));offset+=len(shipment.pages)
            if not quantities or shipment.issues():continue
            notes,count=mark_order(doc,indices,sum(item.quantity for item in shipment.items));marks+=count
            if notes:reports.append({'order':shipment.order,'output_pages':[i+1 for i in indices],'notes':notes})
        total=len(doc)
        for i in range(total):
            page=doc[i];text=f'Page {i+1}';width=fitz.get_text_length(text,fontname='hebo',fontsize=9)
            rect=fitz.Rect(page.rect.width-width-15,page.rect.height-17,page.rect.width-6,page.rect.height-3)
            if not blank(page,rect):
                if page.rotation:page.remove_rotation()
                old=page.mediabox;page.set_mediabox(fitz.Rect(old.x0,old.y0,old.x1,old.y1+22));page.set_cropbox(page.mediabox)
            page.insert_text((page.rect.width-width-10,page.rect.height-7),text,fontname='hebo',fontsize=9,color=(.15,.15,.15))
        doc.saveIncr()
    return reports,marks
