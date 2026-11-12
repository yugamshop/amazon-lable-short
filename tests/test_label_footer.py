from backend.label_parser import parse_page, sorted_shipments, pick_rows

def label(sku, quantity, awb='375104583736'):
    return parse_page(f'Amazon\nShip To:\nAWB {awb}\nBOX 1 of 1\n{sku} [{quantity}]\nATSPL', 'sample.pdf', 0)

def test_footer_quantity_and_pack_size():
    shipment=label('Corner Floor Lamp (Pack-2)',4)
    assert not shipment.issues()
    assert shipment.items[0].sku=='Corner Floor Lamp (Pack-2)'
    assert shipment.items[0].quantity==4

def test_footer_grouping_and_pick_totals():
    shipments=[label('Z',1),label('A',4,'375104583737'),label('Z',2,'375104583738')]
    assert [s.items[0].sku for s in sorted_shipments(shipments)]==['A','Z','Z']
    assert sum(row[4] for row in pick_rows(shipments))==7

def test_missing_quantity_is_not_guessed():
    assert parse_page('Amazon\nAWB 375104583736\nProduct without quantity','sample.pdf',0).issues()
    assert label('SKU',0).issues()

def test_two_footer_products_on_same_line():
    shipment=label('RING LIGHT BAG (2.0) [1] 9ft Bag',2)
    assert [(i.sku,i.quantity) for i in shipment.items]==[('RING LIGHT BAG (2.0)',1),('9ft Bag',2)]

def test_invoice_and_non_amazon_brackets_are_not_label_items():
    assert not parse_page('Amazon Tax Invoice/Bill of Supply\nAWB 375104583736\nSKU [4]','sample.pdf',0).items
    assert not parse_page('Courier\nAWB 375104583736\nAddress [4]','sample.pdf',0).items
