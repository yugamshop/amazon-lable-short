import time
from io import BytesIO
import fitz
from pypdf import PdfReader
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import Paths

def pdf(sku,order):
    document=fitz.open()
    label=document.new_page();label.insert_text((40,40),f'Amazon\nOrder ID: {order}\nAWB: TRACK123456\nSKU: {sku}\nProduct name: Product {sku}\nQuantity: 2')
    invoice=document.new_page();invoice.insert_text((40,40),f'Amazon Tax Invoice\nOrder ID: {order}\nSKU: {sku}\nProduct name: Product {sku}\nQuantity: 2')
    return document.tobytes()

def wait(client,id):
    for _ in range(200):
        result=client.get('/api/batches/'+id).json()
        if result['status'] in ('ready','error'):return result
        time.sleep(.02)
    raise AssertionError('Background job did not finish')

def test_fresh_upload_edit_sort_and_page_preservation(tmp_path):
    with TestClient(create_app(Paths(tmp_path,output_directory=tmp_path/'Output')),base_url='http://localhost') as client:
        source=pdf('ZZZ','407-1111111-1111111')
        first=client.post('/api/batches',files={'files':('first.pdf',source)}).json()['id']
        a=wait(client,first);assert len(a['rows'])==1 and a['rows'][0]['pages']==[0,1]
        second=client.post('/api/batches',files={'files':('second.pdf',pdf('AAA','407-2222222-2222222'))}).json()['id']
        b=wait(client,second);assert b['rows'][0]['items'][0]['sku']=='AAA'
        assert a['rows'][0]['items'][0]['sku']=='ZZZ' and first!=second
        row=a['rows'][0];row['items'][0]['quantity']=3;row['items'][0]['sku']='CORRECTED';row['approved']=True
        payload={'rows':[row],'mode':'SKU','separate':False}
        assert client.post('/api/batches/'+first+'/generate',json=payload).status_code==202
        result=wait(client,first);assert result['output_pages']==2 and result['needs_check']==0,result
        output=client.get('/api/batches/'+first+'/download');assert len(PdfReader(BytesIO(output.content)).pages)==2
        with fitz.open(stream=source,filetype='pdf') as original,fitz.open(stream=output.content,filetype='pdf') as sorted_pdf:
            for index in range(2):assert original[index].get_pixmap(clip=fitz.Rect(0,0,595,800)).samples==sorted_pdf[index].get_pixmap(clip=fitz.Rect(0,0,595,800)).samples
        assert client.get(f'/api/batches/{first}/pages/0').headers['content-type']=='image/png'
        assert client.post('/api/batches/'+first+'/generate',json={'rows':[]}).status_code==422
        assert client.post('/api/batches',files={'files':('bad.txt',b'bad')}).status_code==422
