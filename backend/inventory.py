from datetime import datetime, timezone
from fastapi import HTTPException
from .database import key

FIELDS=('sku','asin','fsn','name','rack_id','stock_quantity','low_stock_threshold','notes')

def serialize(conn, row):
    if row is None: raise HTTPException(404, 'Product not found')
    product=dict(row)
    product['aliases']=[r['code'] for r in conn.execute("SELECT code FROM identifiers WHERE product_id=? AND kind='alias' ORDER BY code",(row['id'],))]
    product['photos']=[dict(r) | {'url':f"/api/photos/{r['id']}"} for r in conn.execute('SELECT id,filename,position,width,height,bytes FROM photos WHERE product_id=? ORDER BY position,id',(row['id'],))]
    product['main_image']=product['photos'][0]['url'] if product['photos'] else None
    product['low_stock']=product['stock_quantity']<=product['low_stock_threshold']
    return product

def get_product(conn, product_id):
    return serialize(conn, conn.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone())

def save_product(conn, payload, product_id=None):
    if product_id is not None:get_product(conn,product_id)
    codes={}
    for kind in ('sku','asin','fsn'):
        code=getattr(payload,kind)
        if code:codes.setdefault(key(code),(code,kind))
    for code in payload.aliases:codes.setdefault(key(code),(code,'alias'))
    for code_key,(code,_) in codes.items():
        collision=conn.execute('SELECT product_id FROM identifiers WHERE code_key=?',(code_key,)).fetchone()
        if collision and collision['product_id']!=product_id:
            other=get_product(conn,collision['product_id'])
            raise HTTPException(409,f'Identifier "{code}" already belongs to "{other["name"]}". Edit that product or use a different identifier.')
    now=datetime.now(timezone.utc).isoformat()
    values=[getattr(payload,field) for field in FIELDS]
    if product_id is None:
        product_id=conn.execute(f'INSERT INTO products ({",".join(FIELDS)},created_at,updated_at) VALUES ({",".join("?" for _ in range(len(FIELDS)+2))})',values+[now,now]).lastrowid
    else:
        conn.execute(f'UPDATE products SET {",".join(field+"=?" for field in FIELDS)},updated_at=? WHERE id=?',values+[now,product_id])
        conn.execute('DELETE FROM identifiers WHERE product_id=?',(product_id,))
    for code_key,(code,kind) in codes.items():conn.execute('INSERT INTO identifiers VALUES (?,?,?,?)',(code_key,code,kind,product_id))
    return get_product(conn,product_id)

def search_products(conn, search='', rack='', low_stock=False, page=1, page_size=24):
    where=[]; args=[]
    if search:
        # Escape LIKE metacharacters so searching a literal SKU containing % or _ works.
        text=search.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        where.append("(p.name LIKE ? ESCAPE '\\' OR p.rack_id LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM identifiers i WHERE i.product_id=p.id AND i.code LIKE ? ESCAPE '\\'))")
        args.extend(['%'+text+'%']*3)
    if rack:where.append('p.rack_id=?');args.append(rack)
    if low_stock:where.append('p.stock_quantity<=p.low_stock_threshold')
    condition=' WHERE '+' AND '.join(where) if where else ''
    total=conn.execute('SELECT count(*) FROM products p'+condition,args).fetchone()[0]
    rows=conn.execute('SELECT p.* FROM products p'+condition+' ORDER BY p.name COLLATE NOCASE,p.id LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size]).fetchall()
    summary=dict(conn.execute('SELECT count(*) AS products,coalesce(sum(stock_quantity),0) AS units,coalesce(sum(stock_quantity<=low_stock_threshold),0) AS low_stock,count(DISTINCT NULLIF(rack_id,\'\')) AS racks FROM products').fetchone())
    racks=[r[0] for r in conn.execute("SELECT DISTINCT rack_id FROM products WHERE rack_id<>'' ORDER BY rack_id COLLATE NOCASE")]
    return {'items':[serialize(conn,row) for row in rows],'total':total,'page':page,'page_size':page_size,'summary':summary,'racks':racks}
