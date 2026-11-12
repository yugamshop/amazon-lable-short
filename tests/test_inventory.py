from io import BytesIO
import pytest
from PIL import Image
from openpyxl import Workbook,load_workbook
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import Paths

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Paths(tmp_path)),base_url='http://localhost') as c:yield c

def product(client,**changes):
    payload={'sku':'65cm Octa Grid','asin':'B0FWRWVL3V','fsn':'FSN-001','name':'Octa grid','rack_id':'A-01','stock_quantity':12,'aliases':['YSG11']};payload.update(changes)
    response=client.post('/api/products',json=payload)
    assert response.status_code==201,response.text
    return response.json()

def test_identifiers_conflict_and_edit_rollback(client):
    p=product(client)
    for code in ['65cm octa grid','b0fwrwvl3v','FSN-001','ysg11']:assert client.get('/api/lookup',params={'code':code}).json()['id']==p['id']
    assert client.post('/api/products',json={'sku':'YSG11','name':'Other'}).status_code==409
    other=product(client,sku='ABC',asin='',fsn='',aliases=[])
    assert client.put(f"/api/products/{other['id']}",json={'sku':'B0FWRWVL3V','name':'Bad'}).status_code==409
    assert client.get('/api/lookup?code=ABC').json()['name']=='Octa grid'
    assert client.post('/api/products',json={'sku':'X','name':'X','stock_quantity':-1}).status_code==422

def image_bytes(size=(2000,1500)):
    out=BytesIO();Image.new('RGB',size,'orange').save(out,'PNG');return out.getvalue()

def test_photos_atomic_limit_main_and_cleanup(client):
    p=product(client);url=f"/api/products/{p['id']}/photos"
    assert client.post(url,files=[('files',('good.png',image_bytes(),'image/png')),('files',('bad.png',b'bad','image/png'))]).status_code==422
    assert client.get(f"/api/products/{p['id']}").json()['photos']==[]
    response=client.post(url,files=[('files',(f'{i}.png',image_bytes(),'image/png')) for i in range(3)])
    assert response.status_code==200,response.text
    photos=response.json()['photos'];assert len(photos)==3 and photos[0]['width']==1200
    assert client.post(url,files={'files':('four.png',image_bytes(),'image/png')}).status_code==422
    assert client.post(f"/api/photos/{photos[2]['id']}/main").json()['photos'][0]['id']==photos[2]['id']
    assert client.get(photos[0]['url']).headers['content-type']=='image/jpeg'
    assert client.delete(f"/api/products/{p['id']}").status_code==200
    assert client.get(photos[0]['url']).status_code==404

def test_import_template_export_and_partial_errors(client):
    template=client.get('/api/inventory/template');assert template.status_code==200
    result=client.post('/api/inventory/import',files={'file':('sample.xlsx',template.content)}).json()
    assert result['created']==1,result
    csv=b'sku,name,stock_quantity,aliases\n001,Zero code,4,ALIAS\nBAD,Bad,-2,\n'
    result=client.post('/api/inventory/import',files={'file':('inventory.csv',csv)}).json()
    assert result['created']==1 and result['skipped']==1 and result['errors'][0]['row']==3
    csv=b'sku,stock_quantity\nALIAS,10\n'
    result=client.post('/api/inventory/import?mode=update',files={'file':('update.csv',csv)}).json()
    assert result['updated']==1,result
    p=client.get('/api/lookup?code=001').json();assert p['sku']=='001' and p['stock_quantity']==10 and p['name']=='Zero code'
    exported=load_workbook(BytesIO(client.get('/api/inventory/export').content));assert exported.active.max_row==3
    wb=Workbook();wb.active.append(['sku','name']);wb.active.append([7,'Padded']);wb.active['A2'].number_format='00000';out=BytesIO();wb.save(out)
    assert client.post('/api/inventory/import',files={'file':('padded.xlsx',out.getvalue())}).json()['created']==1
    assert client.get('/api/lookup?code=00007').status_code==200
    wb.active['A2']='=1+1';out=BytesIO();wb.save(out)
    assert client.post('/api/inventory/import',files={'file':('formula.xlsx',out.getvalue())}).status_code==422

def test_search_filters_and_local_requests(client):
    product(client,sku='SKU_%',stock_quantity=2)
    product(client,sku='OTHER',asin='',fsn='',aliases=[],name='Other',rack_id='B-02')
    assert client.get('/api/products?search=%25').json()['total']==1
    assert client.get('/api/products?low_stock=true').json()['total']==1
    result=client.get('/api/products?page_size=1&page=2').json();assert len(result['items'])==1 and result['total']==2
    assert client.get('/api/products?rack=B-02').json()['total']==1
    assert client.post('/api/products',headers={'Origin':'https://other.example'},json={'sku':'X','name':'X'}).status_code==403
    assert client.get('/').status_code==200
