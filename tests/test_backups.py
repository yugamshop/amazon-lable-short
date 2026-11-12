from io import BytesIO
import zipfile,json
from PIL import Image
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import Paths

def test_full_backup_restore_and_invalid_restore_preserves_inventory(tmp_path):
    with TestClient(create_app(Paths(tmp_path,output_directory=tmp_path/'Output')),base_url='http://localhost') as client:
        p=client.post('/api/products',json={'sku':'SKU1','asin':'ASIN1','fsn':'FSN1','name':'Tripod','stock_quantity':17,'rack_id':'A-12','aliases':['ALIAS1'],'notes':'Warehouse note'}).json()
        photo=BytesIO();Image.new('RGB',(150,150),'blue').save(photo,'PNG')
        client.post(f"/api/products/{p['id']}/photos",files={'files':('photo.png',photo.getvalue(),'image/png')})
        backup=client.get('/api/inventory/backup');assert backup.status_code==200
        with zipfile.ZipFile(BytesIO(backup.content)) as z:assert 'inventory.sqlite3' in z.namelist() and any(n.startswith('images/') for n in z.namelist())
        client.delete(f"/api/products/{p['id']}")
        response=client.post('/api/inventory/restore',files={'file':('backup.zip',backup.content)})
        assert response.status_code==200,response.text
        assert response.json()['products']==1 and response.json()['photos']==1
        restored=client.get('/api/lookup?code=ALIAS1').json()
        assert restored['stock_quantity']==17 and restored['rack_id']=='A-12' and restored['notes']=='Warehouse note'
        assert client.get(restored['main_image']).status_code==200
        recovery=response.json()['recovery_backup'];assert client.get('/api/inventory/backups/'+recovery).status_code==200
        assert client.post('/api/inventory/restore',files={'file':('bad.zip',b'not a zip')}).status_code==422
        assert client.get('/api/products').json()['total']==1
        bad=BytesIO()
        with zipfile.ZipFile(bad,'w') as z:
            z.writestr('inventory.json',json.dumps({'format':'labeldesk-inventory','version':1,'products':[{'sku':'DUP','name':'A'},{'sku':'DUP','name':'B'}]}))
        assert client.post('/api/inventory/restore',files={'file':('bad.zip',bad.getvalue())}).status_code==409
        assert client.get('/api/lookup?code=ALIAS1').status_code==200
