"""Background PDF batches. Each upload is read anew; original pages remain intact."""
import csv
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from io import StringIO
from pydantic import BaseModel,Field
from fastapi import APIRouter,File,UploadFile,HTTPException
from fastapi.responses import FileResponse,Response
from pypdf import PdfReader,PdfWriter
from .label_parser import load_files,Shipment,Item,sorted_shipments,identity,amazon_invoice_items
from .database import connection,key

class PreviewItem(BaseModel):
    sku:str=Field(default='',max_length=200)
    asin:str=Field(default='',max_length=100)
    fsn:str=Field(default='',max_length=100)
    name:str=Field(default='',max_length=600)
    quantity:int=Field(default=0,ge=0,le=1000000)
class PreviewOrder(BaseModel):
    index:int=Field(ge=0)
    marketplace:str=Field(max_length=40)
    order:str=Field(default='',max_length=200)
    awb:str=Field(default='',max_length=200)
    courier:str=Field(default='',max_length=200)
    approved:bool=False
    items:list[PreviewItem]=Field(max_length=1000)
class Generate(BaseModel):
    rows:list[PreviewOrder]
    mode:str='Product groups (original order)'
    separate:bool=False
    marks:bool=False

def router(paths):
    routes=APIRouter(prefix='/api/batches')
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='labeldesk')
    lock=threading.RLock();jobs={}
    folder=paths.data/'batches';folder.mkdir(parents=True,exist_ok=True)
    def job(id):
        with lock:
            if id in jobs:return jobs[id]
            manifest=folder/id/'batch.json' if len(id)==32 and all(c in '0123456789abcdef' for c in id) else None
            if manifest and manifest.exists():
                jobs[id]=json.loads(manifest.read_text(encoding='utf-8'))
                for row in jobs[id]['rows']:
                    row['source']=str(folder/id/Path(row['source']).name)
                    if any(item.get('quantity',0)<=0 for item in row.get('items',[])):
                        corrected=amazon_invoice_items(row.get('text',''))
                        if corrected:
                            row['items']=[asdict(item) for item in corrected]
                            row['warnings']=[w for w in row.get('warnings',[]) if w!='Some invoice product rows could not be read']
                            row['issues']=[w for w in row.get('issues',[]) if w not in ('Some invoice product rows could not be read','Quantity missing or invalid','Product missing','Product identifier missing')]
                            for stale in ('output','pick','pick_pdf','pick_excel','bundle','print_groups'):jobs[id].pop(stale,None)
                    import re
                    ids=set(re.findall(r'(?im)^\s*Order\s*(?:ID|Number|No\.?)\s*[:#]\s*((?:\d{3}-\d{7}-\d{7}|OD\d{12,25}))',row.get('text','')))
                    if len(ids)<=1:
                        for field in ('warnings','issues'):row[field]=[w for w in row.get(field,[]) if not w.startswith('Several orders on one page')]
                if jobs[id]['status'] in ('processing','generating'):jobs[id].update(status='error',message='Processing was interrupted. Upload these PDFs again.')
                return jobs[id]
        raise HTTPException(404,'Batch not found. Upload your PDFs again.')
    def persist(record):
        target=folder/record['id']/'batch.json';temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8');temp.replace(target)
    def process(record,files):
        try:
            def progress(message):
                with lock:record['message']=message;record['progress']=min(90,record['progress']+max(1,80//len(files)))
            notices=[]
            shipments,failures=load_files(files,progress,lambda path,page,used:notices.append({'file':Path(path).name,'page':page+1,'used':used}))
            rows=[]
            with connection(paths) as conn:
                for index,s in enumerate(shipments):
                    row=asdict(s);row['index']=index;row['filename']=Path(s.source).name.split('_',1)[-1];row['page_numbers']=[p+1 for p in s.pages];row['issues']=s.issues();row['rack_id']=''
                    for item in s.items:
                        for code in (item.sku,item.asin,item.fsn):
                            match=conn.execute('SELECT p.rack_id FROM products p JOIN identifiers i ON i.product_id=p.id WHERE i.code_key=?',(key(code),)).fetchone() if code else None
                            if match:row['rack_id']=match['rack_id'];break
                        if row['rack_id']:break
                    row['text']=s.text[:60000];rows.append(row)
            with lock:
                record.update(status='ready',progress=100,message='Preview ready',rows=rows,failures=[{'file':Path(p).name.split('_',1)[-1],'error':e} for p,e in failures],ocr=notices)
                persist(record)
        except Exception as exc:
            with lock:record.update(status='error',message=str(exc));persist(record)
    @routes.post('',status_code=202)
    def upload(files:list[UploadFile]=File(...)):
        if not 1<=len(files)<=100:raise HTTPException(422,'Upload 1 to 100 PDF files per batch')
        id=uuid4().hex;directory=folder/id;directory.mkdir();saved=[];total=0
        try:
            for index,file in enumerate(files):
                import re
                filename=re.sub(r'[<>:"|?*]','_',Path((file.filename or '').replace('\\','/')).name)
                if not filename.lower().endswith('.pdf'):raise HTTPException(422,'Choose PDF files only')
                target=directory/f'{index:03d}_{filename}';size=0
                with target.open('wb') as out:
                    while chunk:=file.file.read(1024*1024):
                        size+=len(chunk);total+=len(chunk)
                        if size>200*1024*1024 or total>1024*1024*1024:raise HTTPException(413,'Limit: 200 MB per PDF and 1 GB per batch')
                        out.write(chunk)
                saved.append(target)
            record={'id':id,'status':'processing','progress':5,'message':'Reading uploaded PDFs','rows':[],'failures':[],'ocr':[],'output':None}
            with lock:jobs[id]=record;persist(record)
            pool.submit(process,record,saved)
            return {'id':id}
        except Exception:
            import shutil
            shutil.rmtree(directory)
            raise
        finally:
            for file in files:file.file.close()
    @routes.get('/{id}')
    def status(id:str):
        with lock:
            record=job(id)
            return {k:v for k,v in record.items() if k!='rows'} | {'rows':[{k:v for k,v in row.items() if k not in ('source','text')} for row in record['rows']]}
    @routes.get('/{id}/pages/{index}')
    def preview(id:str,index:int):
        import fitz
        record=job(id)
        if not 0<=index<len(record['rows']):raise HTTPException(404,'Order not found')
        row=record['rows'][index]
        with fitz.open(row['source']) as doc:
            page=doc[row['pages'][0]];pix=page.get_pixmap(matrix=fitz.Matrix(1.2,1.2),alpha=False)
            return Response(pix.tobytes('png'),media_type='image/png')
    @routes.get('/{id}/text/{index}')
    def text(id:str,index:int):
        record=job(id)
        if not 0<=index<len(record['rows']):raise HTTPException(404,'Order not found')
        return {'text':record['rows'][index]['text']}
    def generate(record,payload):
        try:
            shipments=[]
            for edited in payload.rows:
                original=record['rows'][edited.index]
                shipments.append(Shipment(source=original['source'],pages=original['pages'],marketplace=edited.marketplace,order=edited.order.strip(),awb=edited.awb.strip(),courier=edited.courier.strip(),items=[Item(**item.model_dump()) for item in edited.items],warnings=original['warnings'],approved=edited.approved))
                original.update(edited.model_dump());original['issues']=shipments[-1].issues()
            if not any(not shipment.issues() for shipment in shipments):
                raise ValueError('No readable orders to sort. Check OCR availability or correct and review the flagged rows. No sorted PDF was created.')
            import fitz,re
            ignored=[]
            for shipment in shipments:
                with fitz.open(shipment.source) as document:
                    keep=[]
                    for page_index in shipment.pages:
                        text=document[page_index].get_text()
                        payment=bool(re.search(r'Please make payment to|bank transfer using NEFT',text,re.I))
                        items=bool(re.search(r'\b(?:Qty|Quantity|Description|HSN)\b',text,re.I))
                        if payment and not items:ignored.append({'file':Path(shipment.source).name,'page':page_index+1})
                        else:keep.append(page_index)
                    shipment.pages=keep
            shipments=[s for s in shipments if s.pages]
            ordered=sorted_shipments(shipments,payload.mode,payload.separate)
            if payload.mode=='Rack ID':
                with connection(paths) as conn:
                    def rack(s):
                        for item in s.items:
                            for code in (item.sku,item.asin,item.fsn):
                                row=conn.execute('SELECT p.rack_id FROM products p JOIN identifiers i ON i.product_id=p.id WHERE i.code_key=?',(key(code),)).fetchone() if code else None
                                if row:return row['rack_id'].casefold()
                        return '\uffff'
                    ordered=sorted(ordered,key=lambda s:(bool(s.issues()),s.marketplace if payload.separate else '',rack(s)))
            stamp=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+record['id'][:6]
            output=paths.output/f'sorted-labels-{stamp}.pdf';report=paths.output/f'needs-check-{stamp}.csv'
            paths.output.mkdir(parents=True,exist_ok=True);writer=PdfWriter();readers={};out=StringIO();csvout=csv.writer(out);csvout.writerow(['File','Original pages','Marketplace','Order ID','Issues'])
            print_groups=[]
            for i,s in enumerate(ordered):
                item=s.items[0] if s.items else Item()
                group_key=str((s.marketplace if payload.separate else '',identity(item),bool(s.issues())))
                first_page=len(writer.pages)+1
                if s.source not in readers:readers[s.source]=PdfReader(s.source)
                reader=readers[s.source]
                if reader.is_encrypted:reader.decrypt('')
                for page in s.pages:writer.add_page(reader.pages[page])
                if not print_groups or print_groups[-1]['key']!=group_key:
                    print_groups.append({'key':group_key,'sku':item.sku,'asin':item.asin,'name':item.name,'orders':0,'quantity':0,'first_page':first_page,'last_page':len(writer.pages),'needs_check':bool(s.issues())})
                group=print_groups[-1];group['orders']+=1;group['quantity']+=sum(item.quantity for item in s.items);group['last_page']=len(writer.pages)
                if s.issues():csvout.writerow([Path(s.source).name.split('_',1)[-1],','.join(str(p+1) for p in s.pages),s.marketplace,s.order,'; '.join(s.issues())])
                with lock:record['progress']=int((i+1)/max(1,len(ordered))*95)
            writer.write(output);writer.close()
            from .picking import build_pick_list,export_pick_list
            from .marking import apply_marks
            marking_notes,mark_count=apply_marks(output,ordered,quantities=payload.marks)
            for note in marking_notes:csvout.writerow(['Output PDF',','.join(map(str,note['output_pages'])),'',note['order'],'; '.join(note['notes'])])
            for failure in record['failures']:csvout.writerow([failure['file'],'','','','File not included: '+failure['error']])
            for removed in ignored:csvout.writerow([removed['file'],removed['page'],'','','Ignored: payment-information-only invoice addendum'])
            report.write_text(out.getvalue(),encoding='utf-8-sig')
            pick=build_pick_list(ordered,paths,payload.separate)
            pick_pdf,pick_excel=export_pick_list(pick,paths,stamp)
            from zipfile import ZipFile,ZIP_DEFLATED
            bundle=paths.output/f'LabelDesk-results-{stamp}.zip'
            with ZipFile(bundle,'w',ZIP_DEFLATED) as archive:
                for path in (output,report,paths.output/pick_pdf,paths.output/pick_excel):archive.write(path,path.name)
            with lock:
                record.update(status='ready',progress=100,message='Sorted labels and pick list ready',output=output.name,report=report.name,output_pages=sum(len(s.pages) for s in ordered),needs_check=sum(bool(s.issues()) for s in ordered),pick=pick,pick_pdf=pick_pdf,pick_excel=pick_excel,bundle=bundle.name,marking_notes=marking_notes,mark_count=mark_count,sort_mode=payload.mode,print_groups=print_groups,ignored_pages=ignored);persist(record)
        except Exception as exc:
            with lock:record.update(status='ready',message='Could not generate: '+str(exc),generation_error=str(exc));persist(record)
    @routes.post('/{id}/generate',status_code=202)
    def start_generate(id:str,payload:Generate):
        record=job(id)
        if record['status']!='ready':raise HTTPException(409,'Wait for processing to finish')
        if not record['rows']:raise HTTPException(422,'No PDF pages could be read')
        if payload.mode not in ('Product groups (original order)','SKU','ASIN','Product name','Quantity (highest group first)','Courier','Rack ID'):raise HTTPException(422,'Invalid sort option')
        if sorted(r.index for r in payload.rows)!=list(range(len(record['rows']))):raise HTTPException(422,'Every order must appear exactly once; refresh the preview')
        with lock:record.update(status='generating',progress=0,message='Generating sorted PDF',output=None,generation_error=None,separate=payload.separate)
        pool.submit(generate,record,payload);return {'id':id}
    @routes.get('/{id}/download')
    def download(id:str,report:bool=False,kind:str='output'):
        if kind not in ('output','pick_pdf','pick_excel','bundle'):raise HTTPException(422,'Unknown download type')
        record=job(id);filename=record.get('report' if report else kind)
        if not filename or not (paths.output/filename).is_file():raise HTTPException(404,'Generate the sorted PDF first')
        return FileResponse(paths.output/filename,filename=filename)
    @routes.post('/{id}/refresh-pick')
    def refresh_pick(id:str):
        record=job(id)
        if record['status']!='ready' or not record.get('output'):raise HTTPException(409,'Generate your labels and pick list first')
        from .picking import build_pick_list,export_pick_list
        from zipfile import ZipFile,ZIP_DEFLATED
        shipments=[Shipment(source=r['source'],pages=r['pages'],marketplace=r['marketplace'],order=r['order'],awb=r['awb'],courier=r['courier'],items=[Item(**i) for i in r['items']],warnings=r['warnings'],approved=r['approved']) for r in record['rows']]
        pick=build_pick_list(shipments,paths,record.get('separate',False))
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+id[:6]
        pdf,excel=export_pick_list(pick,paths,stamp)
        bundle=paths.output/f'LabelDesk-results-{stamp}.zip'
        with ZipFile(bundle,'w',ZIP_DEFLATED) as archive:
            for name in (record['output'],record['report'],pdf,excel):archive.write(paths.output/name,name)
        with lock:record.update(pick=pick,pick_pdf=pdf,pick_excel=excel,bundle=bundle.name);persist(record)
        return {'updated':True}
    return routes
