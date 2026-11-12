import fitz
from backend.label_parser import Shipment,Item,sorted_shipments
from backend.picking import build_pick_list,export_pick_list
from backend.marking import mark_order
from backend.config import Paths
from backend.database import initialize,connection
from backend.inventory import save_product
from backend.models import ProductInput

def test_first_product_group_and_inventory_alias_totals(tmp_path):
    paths=Paths(tmp_path,output_directory=tmp_path/'Output');initialize(paths)
    with connection(paths) as conn:save_product(conn,ProductInput(sku='65cm',asin='B0FWRWVL3V',name='Octa Grid',aliases=['GRID-ALIAS'],rack_id='A-01'))
    def order(id,sku,qty=1):return Shipment('input.pdf',[id],marketplace='Amazon',order=str(id),items=[Item(sku=sku,name=sku,quantity=qty)])
    orders=[order(0,'65cm'),order(1,'ABC'),order(2,'65cm')]
    assert [s.order for s in sorted_shipments(orders,'Product groups (original order)',False)]==['0','2','1']
    orders.append(order(3,'GRID-ALIAS',2))
    pick=build_pick_list(orders,paths);row=pick['rows'][0]
    assert row['sku']=='65cm' and row['quantity']==4 and row['orders']==3 and row['rack_id']=='A-01'
    assert pick['total_quantity']==5
    pdf,xlsx=export_pick_list(pick,paths,'test')
    with fitz.open(paths.output/pdf) as doc:assert 'Page 1 of 1' in doc[0].get_text()

def test_blue_marks_qty_only_on_invoice_and_skip_unsafe():
    doc=fitz.open();label=doc.new_page();label.insert_text((30,100),'Amazon Shipping Label AWB 1234567')
    invoice=doc.new_page();invoice.insert_text((30,100),'Tax Invoice/Bill of Supply');invoice.insert_text((220,550),'Authorised Signatory')
    notes,count=mark_order(doc,[0,1],3)
    assert count==2 and not notes
    assert 'QTY:' not in doc[0].get_text() and 'QTY: 3' in doc[1].get_text()
    blue=[d for d in doc[0].get_drawings() if d.get('color') and abs(d['color'][2]-216/255)<.01]
    assert len(blue)==1
    blocked=fitz.open();page=blocked.new_page();page.draw_rect(page.rect,color=(0,0,0),fill=(0,0,0))
    notes,count=mark_order(blocked,[0],2);assert count==0 and notes
