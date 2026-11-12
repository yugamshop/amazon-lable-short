from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import sqlite3
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import default_paths
from .database import initialize, connection, key
from .models import ProductInput
from .inventory import save_product, get_product, search_products
from .media import compress_photo, add_photos, MAX_FILE_BYTES
from .spreadsheets import read_rows, import_rows, workbook_bytes, csv_bytes, SAMPLE

def create_app(paths=None):
    paths=paths or default_paths()
    @asynccontextmanager
    async def lifespan(app):
        initialize(paths)
        yield
    app=FastAPI(title='LabelDesk Local',lifespan=lifespan)
    @app.middleware('http')
    async def fresh_interface(request,call_next):
        response=await call_next(request)
        if request.url.path in ('/','/backup') or request.url.path.startswith('/static/'):
            response.headers['Cache-Control']='no-store'
        return response
    from .labels import router
    app.include_router(router(paths))
    from .backups import router as backup_router
    app.include_router(backup_router(paths))
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]'])
    @app.middleware('http')
    async def local_only(request,call_next):
        from urllib.parse import urlsplit
        origin=request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin:
            parsed=urlsplit(origin)
            if parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.netloc!=request.headers.get('host'):
                return Response('Local requests only',status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response
    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409,content={'detail':'An identifier already belongs to another product. Refresh and check the identifiers.'})
    @app.get('/api/health')
    def health():return {'status':'ok','stage':2,'local':True}
    @app.get('/api/products')
    def products(search:str='',rack:str='',low_stock:bool=False,page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=100)):
        with connection(paths) as conn:return search_products(conn,search,rack,low_stock,page,page_size)
    @app.get('/api/lookup')
    def lookup(code:str):
        with connection(paths) as conn:
            row=conn.execute('SELECT product_id FROM identifiers WHERE code_key=?',(key(code),)).fetchone()
            if not row:raise HTTPException(404,'Product not in inventory')
            return get_product(conn,row['product_id'])
    @app.get('/api/products/{product_id}')
    def product(product_id:int):
        with connection(paths) as conn:return get_product(conn,product_id)
    @app.post('/api/products',status_code=201)
    def create(payload:ProductInput):
        with connection(paths) as conn:return save_product(conn,payload)
    @app.put('/api/products/{product_id}')
    def update(product_id:int,payload:ProductInput):
        with connection(paths) as conn:return save_product(conn,payload,product_id)
    @app.delete('/api/products/{product_id}')
    def delete(product_id:int):
        with connection(paths) as conn:
            record=get_product(conn,product_id)
            conn.execute('DELETE FROM products WHERE id=?',(product_id,))
        for photo in record['photos']:(paths.images/photo['filename']).unlink(missing_ok=True)
        return {'deleted':True}
    @app.post('/api/products/{product_id}/photos')
    async def photos(product_id:int,files:list[UploadFile]=File(...)):
        if not 1<=len(files)<=3:raise HTTPException(422,'Choose 1 to 3 photos')
        compressed=[]
        for file in files:
            try:compressed.append(compress_photo(await file.read(MAX_FILE_BYTES+1)))
            finally:await file.close()
        written=[]
        try:
            with connection(paths) as conn:
                get_product(conn,product_id)
                written=add_photos(conn,paths,product_id,compressed)
                result=get_product(conn,product_id)
        except Exception:
            for path in written:path.unlink(missing_ok=True)
            raise
        return result
    @app.get('/api/photos/{photo_id}')
    def photo(photo_id:int):
        with connection(paths) as conn:row=conn.execute('SELECT filename FROM photos WHERE id=?',(photo_id,)).fetchone()
        if not row or not (paths.images/row['filename']).exists():raise HTTPException(404,'Photo not found')
        return FileResponse(paths.images/row['filename'],media_type='image/jpeg')
    @app.delete('/api/photos/{photo_id}')
    def remove_photo(photo_id:int):
        with connection(paths) as conn:
            row=conn.execute('SELECT * FROM photos WHERE id=?',(photo_id,)).fetchone()
            if not row:raise HTTPException(404,'Photo not found')
            conn.execute('DELETE FROM photos WHERE id=?',(photo_id,))
        (paths.images/row['filename']).unlink(missing_ok=True)
        return {'deleted':True}
    @app.post('/api/photos/{photo_id}/main')
    def main_photo(photo_id:int):
        with connection(paths) as conn:
            row=conn.execute('SELECT * FROM photos WHERE id=?',(photo_id,)).fetchone()
            if not row:raise HTTPException(404,'Photo not found')
            ordered=[photo_id]+[r['id'] for r in conn.execute('SELECT id FROM photos WHERE product_id=? AND id<>? ORDER BY position,id',(row['product_id'],photo_id))]
            for position,id_ in enumerate(ordered):conn.execute('UPDATE photos SET position=? WHERE id=?',(position,id_))
            return get_product(conn,row['product_id'])
    @app.get('/api/inventory/template')
    def template(format:Literal['xlsx','csv']='xlsx'):return download(SAMPLE,format,'inventory-template',True)
    @app.get('/api/inventory/export')
    def export(format:Literal['xlsx','csv']='xlsx'):
        with connection(paths) as conn:records=[get_product(conn,r['id']) for r in conn.execute('SELECT id FROM products ORDER BY name COLLATE NOCASE')]
        return download(records,format,'inventory',False)
    def download(records,format,name,template):
        content=workbook_bytes(records,template) if format=='xlsx' else csv_bytes(records,template)
        media='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if format=='xlsx' else 'text/csv; charset=utf-8'
        return Response(content,media_type=media,headers={'Content-Disposition':f'attachment; filename="{name}.{format}"'})
    @app.post('/api/inventory/import')
    def bulk_import(file:UploadFile=File(...),mode:Literal['create','update']='create'):
        try:data=file.file.read(20*1024*1024+1)
        finally:file.file.close()
        rows=read_rows(data,file.filename or '')
        with connection(paths) as conn:return import_rows(conn,rows,mode)
    @app.get('/backup')
    def backup_page():return FileResponse(paths.static/'backup-page.html',headers={'Cache-Control':'no-store'})
    @app.get('/')
    def index():return FileResponse(paths.static/'index.html',headers={'Cache-Control':'no-store'})
    app.mount('/static',StaticFiles(directory=paths.static),name='static')
    return app

app=create_app()
