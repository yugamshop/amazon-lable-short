"""Shared pick totals and compact offline PDF / Excel exports."""
import math
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Alignment,Font,PatternFill
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.utils import get_column_letter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from .database import connection,key
from .inventory import get_product
from .label_parser import identity

def build_pick_list(shipments,paths,separate=False):
    groups={};excluded=0
    with connection(paths) as conn:
        for index,shipment in enumerate(shipments):
            if shipment.issues():excluded+=1;continue
            for item in shipment.items:
                product=None
                for code in (item.sku,item.asin,item.fsn):
                    match=conn.execute('SELECT product_id FROM identifiers WHERE code_key=?',(key(code),)).fetchone() if code else None
                    if match:product=get_product(conn,match['product_id']);break
                groupkey=(shipment.marketplace if separate else '',('inventory',product['id']) if product else identity(item))
                row=groups.setdefault(groupkey,dict(sku=product['sku'] if product else item.sku,asin=product['asin'] if product else item.asin,fsn=product['fsn'] if product else item.fsn,name=product['name'] if product else item.name,rack_id=product['rack_id'] if product else '',quantity=0,order_ids=set(),markets=set(),inventory_id=product['id'] if product else None,image=product['photos'][0]['filename'] if product and product['photos'] else None,image_url=product['main_image'] if product else None))
                row['quantity']+=item.quantity;row['order_ids'].add((shipment.marketplace,shipment.order or shipment.awb or str(index)));row['markets'].add(shipment.marketplace)
    rows=[]
    for row in groups.values():
        row['orders']=len(row.pop('order_ids'));row['marketplace']=', '.join(sorted(row.pop('markets')));rows.append(row)
    rows.sort(key=lambda row:((row['rack_id'] or '\uffff').casefold(),(row['sku'] or row['asin'] or row['name']).casefold()))
    return {'rows':rows,'excluded_orders':excluded,'total_quantity':sum(r['quantity'] for r in rows),'products':len(rows),'orders':len(shipments)-excluded}

def export_pick_list(pick,paths,stem):
    rows=pick['rows'];stamp=datetime.now().astimezone().strftime('%d %b %Y, %I:%M %p')
    pdf=paths.output/f'pick-list-{stem}.pdf';xlsx=paths.output/f'pick-list-{stem}.xlsx'
    widths=[16,46,78,164,45,36,36,138]
    # Fit all eight columns exactly inside A4 portrait, with 18pt margins.
    widths[-1]=A4[0]-36-sum(widths[:-1])
    headers=['','Photo','SKU / ASIN','Product','Rack','Qty','Orders','Market']
    c=canvas.Canvas(str(pdf),pagesize=A4);c.setTitle('LabelDesk pick list')
    perpage=20;pages=max(1,math.ceil(len(rows)/perpage));rowheight=35
    def draw_text(value,x,y,width,maxlines=3,color='#172b32',size=7):
        words=str(value or '').split();lines=[];line=''
        for word in words:
            while stringWidth(word,'Helvetica',size)>width:
                length=max(1,int(len(word)*width/stringWidth(word,'Helvetica',size)))
                if line:lines.append(line);line=''
                lines.append(word[:length]);word=word[length:]
            if stringWidth((line+' '+word).strip(),'Helvetica',size)>width:lines.append(line);line=word
            else:line=(line+' '+word).strip()
        if line:lines.append(line)
        if len(lines)>maxlines:lines=lines[:maxlines];lines[-1]=lines[-1][:-3]+'...'
        c.setFillColor(color);c.setFont('Helvetica',size)
        for n,line in enumerate(lines):c.drawString(x,y-n*(size+2),line)
    for page in range(pages):
        c.setFillColor('#163f3b');c.setFont('Helvetica-Bold',16);c.drawString(18,A4[1]-28,'Pick list')
        c.setFont('Helvetica',8);c.drawRightString(A4[0]-18,A4[1]-25,f'Page {page+1} of {pages}')
        c.drawString(18,A4[1]-43,stamp+f"  |  {pick['orders']} orders  |  {pick['total_quantity']} units  |  {pick['excluded_orders']} orders excluded: Needs Check")
        c.setFont('Helvetica',7);c.drawString(18,A4[1]-55,'Red product text = not in inventory. Quantities are picking totals; inventory stock is not deducted.')
        top=A4[1]-67;c.setFillColor('#e6f1ed');c.rect(18,top-19,A4[0]-36,19,fill=1,stroke=0)
        x=18
        for heading,width in zip(headers,widths):draw_text(heading,x+3,top-12,width-6,maxlines=1,size=7);x+=width
        for i,row in enumerate(rows[page*perpage:(page+1)*perpage]):
            y=top-19-i*rowheight;x=18
            c.setStrokeColor('#d6dfe0');c.setLineWidth(.35);c.line(18,y-rowheight,A4[0]-18,y-rowheight)
            c.setStrokeColor('#52656a');c.rect(x+3,y-20,8,8,stroke=1,fill=0);x+=widths[0]
            if row['image'] and (paths.images/row['image']).exists():c.drawImage(ImageReader(str(paths.images/row['image'])),x+3,y-32,width=39,height=29,preserveAspectRatio=True,anchor='c',mask='auto')
            x+=widths[1]
            values=[(row['sku']+' '+row['asin']).strip() or row['fsn'],row['name'],row['rack_id'],row['quantity'],row['orders'],row['marketplace']]
            for j,(value,width) in enumerate(zip(values,widths[2:])):
                draw_text(value,x+3,y-10,width-6,color='#b13d32' if row['inventory_id'] is None and j in (0,1) else '#172b32');x+=width
        if not rows:draw_text('No verified product rows. Correct the orders listed in Needs Check and generate again.',21,top-40,A4[0]-42)
        c.showPage()
    c.save()
    wb=Workbook();ws=wb.active;ws.title='Pick list'
    headings=['Picked','Photo','SKU','ASIN','FSN','Product name','Rack ID','Quantity','Orders','Marketplace','Inventory status']
    ws.append(['PICK LIST - '+stamp]);ws.merge_cells('A1:K1');ws.append([f"{pick['orders']} orders | {pick['total_quantity']} units | {pick['excluded_orders']} orders excluded: Needs Check"]);ws.merge_cells('A2:K2');ws.append(headings)
    for row in rows:
        ws.append(['☐','',row['sku'],row['asin'],row['fsn'],row['name'],row['rack_id'],row['quantity'],row['orders'],row['marketplace'],'In inventory' if row['inventory_id'] else 'Not in inventory'])
        n=ws.max_row;ws.row_dimensions[n].height=42
        if row['image'] and (paths.images/row['image']).exists():
            image=ExcelImage(str(paths.images/row['image']));scale=min(48/image.width,48/image.height);image.width*=scale;image.height*=scale;ws.add_image(image,f'B{n}')
        if not row['inventory_id']:
            for col in (3,4,5,6,11):ws.cell(n,col).font=Font(color='B13D32')
    for row in ws:
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
            cell.alignment=Alignment(vertical='top',wrap_text=True)
    for cell in ws[3]:cell.fill=PatternFill('solid',fgColor='163F3B');cell.font=Font(color='FFFFFF',bold=True)
    for index,width in enumerate([8,10,22,18,18,44,12,10,10,22,22],1):ws.column_dimensions[get_column_letter(index)].width=width
    ws.freeze_panes='C4';ws.auto_filter.ref=f'A3:K{max(3,ws.max_row)}';ws.print_title_rows='1:3';ws.page_setup.orientation='portrait';ws.page_setup.paperSize=ws.PAPERSIZE_A4;ws.sheet_properties.pageSetUpPr.fitToPage=True;ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0;ws.oddHeader.right.text='Page &P of &N';wb.save(xlsx)
    return pdf.name,xlsx.name
