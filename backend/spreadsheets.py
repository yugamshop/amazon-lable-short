"""Inventory spreadsheets, explicit text identifiers, and row-level import errors."""
import csv
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.utils import get_column_letter
from fastapi import HTTPException
from pydantic import ValidationError
from .models import ProductInput
from .inventory import get_product,save_product,FIELDS
from .database import key

HEADERS=['sku','asin','fsn','name','rack_id','stock_quantity','low_stock_threshold','notes','aliases']
SAMPLE=[{'sku':'EXAMPLE-SKU','asin':'EXAMPLE-ASIN','fsn':'','name':'Example product (replace this row)','rack_id':'A-01','stock_quantity':25,'low_stock_threshold':5,'notes':'Replace these examples with your real products.','aliases':['EXAMPLE-ALIAS']}]
HEADER_ALIASES={'product name':'name','product_name':'name','rack':'rack_id','rack id':'rack_id','stock':'stock_quantity','stock quantity':'stock_quantity','quantity':'stock_quantity','sku aliases':'aliases','low stock threshold':'low_stock_threshold'}

def workbook_bytes(records,template=False):
    wb=Workbook();sheet=wb.active;sheet.title='Inventory'
    headers=HEADERS if template else HEADERS+['image_paths']
    sheet.append(headers)
    for record in records:
        row=[ '|'.join(record.get('aliases',[])) if field=='aliases' else '|'.join('images/'+p['filename'] for p in record.get('photos',[])) if field=='image_paths' else record.get(field,'') for field in headers]
        sheet.append(row)
    for row in sheet:
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
            cell.alignment=Alignment(vertical='top',wrap_text=True)
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='143F3C')
    for i,width in enumerate([25,22,22,48,16,19,22,48,40,48][:len(headers)],1):sheet.column_dimensions[get_column_letter(i)].width=width
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    sheet.row_dimensions[1].height=28
    for n in range(2,sheet.max_row+1):sheet.row_dimensions[n].height=42
    sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.page_setup.orientation='landscape';sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    sheet.print_title_rows='1:1'
    output=BytesIO();wb.save(output);return output.getvalue()

def csv_bytes(records,template=False):
    out=StringIO();headers=HEADERS if template else HEADERS+['image_paths']
    writer=csv.DictWriter(out,fieldnames=headers);writer.writeheader()
    for item in records:
        row={k:item.get(k,'') for k in HEADERS};row['aliases']='|'.join(item.get('aliases',[]))
        if not template:row['image_paths']='|'.join('images/'+p['filename'] for p in item.get('photos',[]))
        # Excel CSV readers may execute cells beginning with =,+,-,@. An apostrophe
        # preserves their identifier meaning; our importer removes that exact prefix.
        for k,v in row.items():
            if isinstance(v,str) and v.startswith(('=','+','-','@')):row[k]="'"+v
        writer.writerow(row)
    return out.getvalue().encode('utf-8-sig')

def read_rows(data,filename):
    if len(data)>20*1024*1024:raise HTTPException(413,'Inventory upload must be 20 MB or smaller')
    if filename.lower().endswith('.xlsx'):
        try:
            # Reject zip bombs before openpyxl expands the workbook.
            from zipfile import ZipFile
            with ZipFile(BytesIO(data)) as archive:
                if sum(entry.file_size for entry in archive.infolist())>100*1024*1024:raise ValueError('Workbook is too large when expanded')
            wb=load_workbook(BytesIO(data),read_only=True,data_only=False)
            sheet=wb.active;rows=[]
            for row in sheet.iter_rows(max_row=min(sheet.max_row,10002),max_col=32):
                values=[]
                for cell in row:
                    if cell.data_type=='f':raise ValueError('Formulas are not accepted in import cells. Paste values before importing.')
                    value=cell.value
                    if isinstance(value,(int,float)) and re.fullmatch(r'0{2,}',cell.number_format or ''):value=str(int(value)).zfill(len(cell.number_format))
                    values.append(value)
                rows.append(values)
            wb.close()
        except HTTPException:raise
        except Exception as exc:raise HTTPException(422,str(exc) if isinstance(exc,ValueError) else 'Could not read the Excel file. Use a valid .xlsx workbook.') from exc
    elif filename.lower().endswith('.csv'):
        try:rows=list(csv.reader(StringIO(data.decode('utf-8-sig'))))
        except (UnicodeDecodeError,csv.Error) as exc:raise HTTPException(422,'Could not read CSV. Save it as CSV UTF-8 in Excel.') from exc
    else:raise HTTPException(422,'Use an .xlsx or .csv inventory file')
    if len(rows)>10001:raise HTTPException(422,'Import up to 10,000 products at a time')
    if not rows:raise HTTPException(422,'File is empty')
    headers=[HEADER_ALIASES.get(str(h or '').strip().lower(),str(h or '').strip().lower()) for h in rows[0]]
    used=[h for h in headers if h in HEADERS]
    if len(used)!=len(set(used)):raise HTTPException(422,'The file contains duplicate column headings')
    if not any(h in headers for h in ('sku','asin','fsn')):raise HTTPException(422,'Add a sku, asin or fsn column. Download the sample template for the correct format.')
    return [(n,{h:row[i] if i<len(row) else None for i,h in enumerate(headers) if h in HEADERS}) for n,row in enumerate(rows[1:],2) if any(v not in (None,'') for v in row)]

def integer(value,default):
    if value in (None,''):return default
    try:
        number=Decimal(str(value))
        if not number.is_finite() or number!=number.to_integral_value() or number<0:raise ValueError()
        return int(number)
    except (InvalidOperation,ValueError):raise ValueError('Stock and low-stock threshold must be non-negative whole numbers')

def import_rows(conn,rows,mode):
    result={'created':0,'updated':0,'skipped':0,'errors':[]}
    for number,row in rows:
        conn.execute('SAVEPOINT import_row')
        try:
            clean={k:(str(v).strip() if v is not None else '') for k,v in row.items()}
            for k,v in clean.items():
                if v.startswith(("'=","'+","'-","'@")):clean[k]=v[1:]
            aliases=[v.strip() for v in re.split(r'[|\n]',clean.get('aliases','')) if v.strip()]
            matches=set()
            for code in [clean.get(k,'') for k in ('sku','asin','fsn')]+aliases:
                match=conn.execute('SELECT product_id FROM identifiers WHERE code_key=?',(key(code),)).fetchone() if code else None
                if match:matches.add(match['product_id'])
            if len(matches)>1:raise ValueError('This row matches identifiers belonging to different products. Correct it before importing.')
            product_id=next(iter(matches)) if matches else None
            if product_id and mode=='create':raise ValueError('Product already exists. Choose Update matching products to update it.')
            base=get_product(conn,product_id) if product_id else {}
            values={k:base.get(k,'') for k in FIELDS}
            for k in FIELDS:
                if k in ('stock_quantity','low_stock_threshold'):values[k]=integer(clean.get(k),base.get(k,0 if k=='stock_quantity' else 5))
                elif clean.get(k):values[k]=clean[k]
            # A matched alias should not replace the canonical SKU.
            if product_id and clean.get('sku') and base.get('sku') and key(clean['sku'])!=key(base['sku']):
                aliases.append(clean['sku']);values['sku']=base['sku']
            values['aliases']=base.get('aliases',[])+aliases
            payload=ProductInput(**values)
            save_product(conn,payload,product_id)
            conn.execute('RELEASE import_row')
            result['updated' if product_id else 'created']+=1
        except (HTTPException,ValidationError,ValueError) as exc:
            conn.execute('ROLLBACK TO import_row');conn.execute('RELEASE import_row')
            message=exc.detail if isinstance(exc,HTTPException) else '; '.join(e['msg'] for e in exc.errors()) if isinstance(exc,ValidationError) else str(exc)
            result['errors'].append({'row':number,'message':message});result['skipped']+=1
    return result
