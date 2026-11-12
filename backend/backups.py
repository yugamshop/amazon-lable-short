"""Validated inventory backups; restore is transactional and keeps a recovery ZIP."""
import json,sqlite3,tempfile,zipfile
from io import BytesIO
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter,File,UploadFile,HTTPException
from fastapi.responses import FileResponse
from .database import connection,INVENTORY_LOCK
from .inventory import get_product,save_product
from .models import ProductInput
from .media import compress_photo,add_photos

def create_backup(paths):
    folder=paths.data/'backups';folder.mkdir(exist_ok=True)
    filename='inventory-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]+'.zip'
    target=folder/filename
    with INVENTORY_LOCK,connection(paths) as conn,tempfile.TemporaryDirectory() as temp:
        products=[get_product(conn,row['id']) for row in conn.execute('SELECT id FROM products ORDER BY id')]
        database=Path(temp)/'inventory.sqlite3'
        snapshot=sqlite3.connect(database)
        try:conn.backup(snapshot)
        finally:snapshot.close()
        try:
            with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.write(database,'inventory.sqlite3')
                archive.writestr('inventory.json',json.dumps({'format':'labeldesk-inventory','version':1,'products':products},ensure_ascii=False))
                for product in products:
                    for photo in product['photos']:archive.write(paths.images/photo['filename'],'images/'+photo['filename'])
        except Exception:
            target.unlink(missing_ok=True);raise
    return target

def validate_backup(data):
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries=archive.infolist();names=[entry.filename for entry in entries]
            if len(names)!=len(set(names)) or len(names)>40000:raise ValueError('Duplicate or excessive ZIP entries')
            if sum(entry.file_size for entry in entries)>1024*1024*1024:raise ValueError('Backup expands beyond the 1 GB limit')
            if any('..' in Path(name.replace('\\','/')).parts or name.startswith(('/','\\')) or ':' in name for name in names):raise ValueError('Unsafe ZIP paths')
            if archive.getinfo('inventory.json').file_size>40*1024*1024:raise ValueError('Inventory metadata is too large')
            content=json.loads(archive.read('inventory.json'))
            if content.get('format')!='labeldesk-inventory' or content.get('version')!=1:raise ValueError('Not a supported LabelDesk inventory backup')
            products=content['products']
            if not isinstance(products,list) or len(products)>10000:raise ValueError('Backup must contain at most 10,000 products')
            validated=[]
            for product in products:
                model=ProductInput(**product);photos=product.get('photos',[])
                if len(photos)>3:raise ValueError('A product has more than three photos')
                images=[]
                for photo in photos:
                    name=photo['filename']
                    if Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('Invalid image name')
                    entry=archive.getinfo('images/'+name)
                    if entry.file_size>10*1024*1024:raise ValueError('Image exceeds 10 MB')
                    images.append(compress_photo(archive.read(entry)))
                validated.append((model,images,product))
            return validated
    except Exception as exc:
        raise HTTPException(422,'Backup could not be restored: '+str(exc)) from exc

def router(paths):
    routes=APIRouter(prefix='/api/inventory')
    @routes.get('/backup')
    def backup():
        path=create_backup(paths);return FileResponse(path,filename=path.name,media_type='application/zip')
    @routes.get('/backups')
    def backups():
        folder=paths.data/'backups'
        return {'files':[p.name for p in sorted(folder.glob('inventory-*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)] if folder.exists() else []}
    @routes.get('/backups/{name}')
    def download(name:str):
        if Path(name).name!=name or not name.startswith('inventory-') or not name.endswith('.zip'):raise HTTPException(404,'Backup not found')
        path=paths.data/'backups'/name
        if not path.is_file():raise HTTPException(404,'Backup not found')
        return FileResponse(path,filename=path.name,media_type='application/zip')
    @routes.post('/restore')
    def restore(file:UploadFile=File(...)):
        try:data=file.file.read(512*1024*1024+1)
        finally:file.file.close()
        if len(data)>512*1024*1024:raise HTTPException(413,'Backup upload limit is 512 MB')
        records=validate_backup(data);written=[]
        with INVENTORY_LOCK:
            recovery=create_backup(paths)
            try:
                with connection(paths) as conn:
                    conn.execute('DELETE FROM products')
                    for model,images,original in records:
                        product=save_product(conn,model)
                        written.extend(add_photos(conn,paths,product['id'],images))
                        if original.get('created_at'):
                            conn.execute('UPDATE products SET created_at=? WHERE id=?',(str(original['created_at'])[:100],product['id']))
            except Exception as exc:
                for path in written:path.unlink(missing_ok=True)
                if isinstance(exc,HTTPException):raise
                raise HTTPException(422,'Restore failed; previous inventory was retained: '+str(exc)) from exc
        return {'products':len(records),'photos':sum(len(images) for _,images,_ in records),'recovery_backup':recovery.name}
    return routes
