"""Offline label extraction, conservative page grouping, and exports."""
from __future__ import annotations
import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from pypdf import PdfReader, PdfWriter

@dataclass
class Item:
    sku: str = ''
    asin: str = ''
    name: str = ''
    quantity: int = 0
    fsn: str = ''

@dataclass
class Shipment:
    source: str
    pages: list[int]
    marketplace: str = 'Unknown'
    order: str = ''
    awb: str = ''
    courier: str = ''
    items: list[Item] = field(default_factory=list)
    text: str = ''
    warnings: list[str] = field(default_factory=list)
    approved: bool = False

    def issues(self):
        problems = [] if self.approved else list(self.warnings)
        if self.marketplace == 'Unknown': problems.append('Marketplace missing')
        if not (self.order or self.awb): problems.append('Order ID / tracking missing')
        if not self.items: problems.append('Product missing')
        for item in self.items:
            if not (item.sku or item.asin or item.fsn or item.name): problems.append('Product identifier missing')
            if item.quantity <= 0: problems.append('Quantity missing or invalid')
        return list(dict.fromkeys(problems))

def value(text, keys):
    # Require a field label and separator, to avoid treating addresses as products.
    pattern = r'(?im)^\s*(?:' + keys + r')\s*(?:[:#=]|\s-\s)\s*([^\n\r]+)'
    match = re.search(pattern, text)
    if not match: return ''
    return re.split(r'\s{2,}(?=(?:SKU|ASIN|Qty|Quantity|Product|Order|AWB|Courier)\b)',match.group(1),flags=re.I)[0].strip()

def table_items(text):
    """Read explicit column headers in layout-preserving text, never infer prices as qty."""
    lines=text.splitlines()
    aliases={'sku':'sku','seller sku':'sku','sku id':'sku','asin':'asin','product name':'name','description':'name','product':'name','qty':'quantity','quantity':'quantity'}
    for n,line in enumerate(lines):
        headers=list(re.finditer(r'\S.*?(?=\s{2,}|$)',line))
        columns=[(m.start(),aliases.get(m.group().strip().lower().rstrip('.'))) for m in headers]
        fields={f for _,f in columns if f}
        if 'quantity' not in fields or not fields.intersection({'sku','asin'}): continue
        result=[]
        for row in lines[n+1:]:
            if not row.strip():
                if result: break
                continue
            values={}
            for j,(start,key) in enumerate(columns):
                if key:values[key]=row[start:columns[j+1][0] if j+1<len(columns) else len(row)].strip()
            q=values.get('quantity','')
            if not re.fullmatch(r'\d{1,6}',q) or not (values.get('sku') or values.get('asin')): break
            result.append(Item(values.get('sku',''),values.get('asin',''),values.get('name',''),int(q)))
        if result:return result
    return []

def amazon_invoice_items(text):
    """Amazon India invoice: description | ASIN ( seller SKU ), HSN, price qty net."""
    if not re.search(r'Tax\s+Invoice\s*/\s*Bill\s+of\s+Supply\s*/\s*Cash\s+Memo',text,re.I) or 'amazon' not in text.lower():
        return []
    # Plain extraction keeps each product description together ahead of its numeric row.
    body = re.split(r'(?m)^\s*TOTAL\s*:', text, maxsplit=1)[0]
    matches = list(re.finditer(
        r'(?m)^\s*(\d+)\s+([^\n].*?)\|\s*([A-Z0-9]{10})\s*\(\s*(.*?)\s*\)\s*'
        r'(?:HSN\s*:\s*\d+\s*)?(?:₹|Rs\.?|INR)\s*[\d,]+\.\d{2}\s+'
        r'(?:-?\s*(?:₹|Rs\.?|INR)\s*-?[\d,]+\.\d{2}\s+)?(\d+)\s+(?:₹|Rs\.?|INR)\s*[\d,]+\.\d{2}',
        body, re.S))
    if not matches or [int(m[1]) for m in matches] != list(range(1,len(matches)+1)):
        return []
    # Refuse partial extraction if another ASIN/SKU block was left unparsed.
    if len(re.findall(r'\|\s*[A-Z0-9]{10}\s*\(',body)) != len(matches):return []
    return [Item(' '.join(m[4].split()),m[3],' '.join(m[2].split()),int(m[5])) for m in matches]

def amazon_label_items(text):
    """Read the trailing SKU [units] footer on Amazon shipping-only labels."""
    if not re.search(r'amazon', text, re.I) or not re.search(r'\bAWB\s*[:#]?\s*[A-Z0-9-]{6,}',text,re.I):
        return []
    if re.search(r'Tax\s+Invoice\s*/\s*Bill',text,re.I):
        return []
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    while lines and re.fullmatch(r'ATSPL|Sold on:\s*www\.amazon\.in',lines[-1],re.I):
        lines.pop()
    result=[]
    while lines:
        matches=list(re.finditer(r'([^\[\]]+?)\s*\[(\d{1,6})\]',lines[-1]))
        if not matches or lines[-1][matches[-1].end():].strip():break
        result.extend(Item(sku=m[1].strip(),quantity=int(m[2])) for m in reversed(matches))
        lines.pop()
    return list(reversed(result))

def parse_page(text, source, page):
    s = Shipment(str(source), [page], text=text)
    low = text.lower()
    if re.search(r'amazon\s*flex|smart\s*hub', low): s.marketplace = 'Amazon Flex'
    elif 'flipkart' in low: s.marketplace = 'Flipkart'
    elif 'amazon' in low: s.marketplace = 'Amazon'
    s.order = value(text, r'(?:marketplace\s+)?order\s*(?:id|no\.?|number|#)')
    if s.order: s.order = s.order.split()[0]
    if not s.order:
        m = re.search(r'\b(?:\d{3}-\d{7}-\d{7}|OD\d{12,25})\b', text, re.I)
        if m: s.order = m.group()
    s.awb = value(text, r'(?:awb|tracking|waybill)(?:\s*(?:id|no\.?|number|#))?').split(' ')[0]
    if not s.awb:
        awb=re.search(r'\bAWB\s*[:#]?\s*([A-Z0-9-]{6,})',text,re.I)
        if awb:s.awb=awb[1]
    s.courier = value(text, r'courier(?:\s*name)?|carrier|logistics\s*partner|shipped\s*via')
    if not s.courier:
        for courier in ['Ekart', 'Delhivery', 'Blue Dart', 'Ecom Express', 'Xpressbees', 'DTDC', 'Amazon Shipping']:
            if courier.lower() in low: s.courier = courier; break
    # Repeated labeled product blocks support multiple products on one label.
    anchors = list(re.finditer(r'(?im)^\s*(?:seller\s+sku|sku|sku\s*id)\s*[:#=]\s*', text))
    blocks = [text[m.start():anchors[i+1].start() if i+1 < len(anchors) else len(text)] for i,m in enumerate(anchors)] if anchors else [text]
    for block in blocks:
        sku = value(block, r'seller\s+sku|sku(?:\s*id)?')
        asin = value(block, 'asin')
        if not asin:
            m = re.search(r'\bB0[A-Z0-9]{8}\b', block)
            if m: asin = m.group()
        name = value(block, r'product(?:\s*name|\s*description)?|item\s*name|description|title')
        fsn = value(block, r'fsn(?:\s*id)?')
        q = value(block, r'qty\.?|quantity|units')
        quantity = int(q) if re.fullmatch(r'\d{1,6}', q) else 0
        if sku or asin or name or fsn: s.items.append(Item(sku, asin, name, quantity, fsn))
    tabular=table_items(text)
    if tabular:
        s.items=tabular
        s.warnings.append('Table-extracted products: verify column alignment and all rows')
    if not text.strip(): s.warnings.append('No text layer (scan/image); enter details manually')
    if len(anchors) > 1: s.warnings.append('Multiple product blocks: verify quantities')
    footer_items=amazon_label_items(text)
    if footer_items:
        s.items=footer_items
    return s

def load_files(paths, progress=None, ocr_notice=None):
    shipments, failures = [], []
    for n, path in enumerate(paths):
        if progress: progress(f'Reading {Path(path).name} ({n+1}/{len(paths)})')
        try:
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('Password protected PDF; save an unlocked copy first')
            local = []
            for i, page in enumerate(reader.pages):
                try: text = page.extract_text() or ''
                except Exception: text = ''
                ocr_warning=''
                if not re.sub(r'(?im)^\s*Page\s+\d+(?:\s+out of\s+\d+)?\s*$','',text).strip():
                    try:
                        from .ocr import read_page
                        text,confident=read_page(path,i)
                        ocr_warning='' if confident else 'OCR used on this page; verify extracted values'
                        if ocr_notice:ocr_notice(str(path),i,True)
                    except Exception as exc:
                        ocr_warning='OCR unavailable or failed: '+str(exc)
                        if ocr_notice:ocr_notice(str(path),i,False)
                s = parse_page(text, path, i)
                if progress:progress(f'Reading {Path(path).name}: page {i+1} of {len(reader.pages)}')
                try:
                    plain=text
                    amazon_items=amazon_invoice_items(plain)
                    if amazon_items:
                        text=plain; s.marketplace='Amazon'
                        s.items=amazon_items; s.warnings=[]; s.text=plain
                        order=re.search(r'Order Number\s*:\s*(\d{3}-\d{7}-\d{7})',plain)
                        if order:s.order=order[1]
                    elif not s.items or any(item.quantity<=0 for item in s.items):
                        tabular=table_items(page.extract_text(extraction_mode='layout') or '')
                        if tabular:
                            s.items=tabular
                            s.warnings.append('Table-extracted products: verify column alignment and all rows')
                except Exception:pass
                if ocr_warning:s.warnings.append(ocr_warning)
                distinct_orders=set(re.findall(r'(?im)^\s*Order\s*(?:ID|Number|No\.?)\s*[:#]\s*((?:\d{3}-\d{7}-\d{7}|OD\d{12,25}))',text))
                if len(distinct_orders)>1:s.warnings.append('Several orders on one page: kept intact; individual label splitting needs review')
                is_invoice = bool(re.search(r'\b(?:tax\s+invoice|invoice\s*(?:number|no|#|:))', text, re.I))
                # Link only an unambiguous order match in this source file. This also
                # handles invoices appearing before their label after a second pass.
                s._packing=bool(re.search(r'packing\s+slip',text,re.I))
                s._invoice = is_invoice and not s.awb and not s._packing
                local.append(s)
            shipments.extend(group_pages(local))
        except Exception as exc:
            failures.append((str(path), str(exc)))
    # Never silently discard duplicate shipments; flag both copies for review.
    seen = defaultdict(list)
    for s in shipments:
        identity = s.awb or s.order
        if identity: seen[(s.marketplace, identity)].append(s)
    for group in seen.values():
        if len(group)>1:
            for s in group: s.warnings.append('Repeated order/tracking: possible duplicate or split shipment')
    return shipments, failures

def group_pages(local):
    """Group locally, so one unusual invoice cannot invalidate the whole file."""
    used=set();groups=[]
    invoice_only=bool(local) and all(s._invoice and re.search(r'Tax\s+Invoice\s*/\s*Bill\s+of\s+Supply',s.text,re.I) for s in local)
    for i,invoice in enumerate(local):
        if i in used or not invoice._invoice:continue
        pagination=re.search(r'Page\s+(\d+)\s+of\s+(\d+)',invoice.text,re.I)
        number,total=map(int,pagination.groups()) if pagination else (1,1)
        if number!=1:continue
        attached=[i];warnings=list(invoice.warnings)
        for offset in range(1,total):
            j=i+offset
            if j>=len(local):warnings.append('Invoice continuation page missing');break
            follow=local[j]
            expected=re.search(r'Page\s+(\d+)\s+of\s+(\d+)',follow.text,re.I)
            if (not follow._invoice or not expected or tuple(map(int,expected.groups()))!=(offset+1,total)
                    or (follow.order and invoice.order and follow.order!=invoice.order)):
                warnings.append('Invoice continuation page missing or order mismatch');break
            attached.append(j)
        invmatch=re.search(r'Invoice\s*(?:Number|No\.?)\s*[:#]\s*([A-Z0-9-]+)',invoice.text,re.I)
        invoice_id=invmatch[1] if invmatch else ''
        def matches(s):
            return bool((invoice.order and s.order==invoice.order) or (invoice_id and re.search(re.escape(invoice_id)+r'(?!\d)',s.text)))
        candidates=[j for j,s in enumerate(local) if j not in used and not s._invoice and not getattr(s,'_packing',False) and matches(s)]
        label_index=candidates[0] if len(candidates)==1 else None
        # An Amazon invoice directly following an image-only label is a local pair.
        # The next invoice's own printed pagination determines continuation pages.
        if label_index is None and not candidates and i>0 and i-1 not in used:
            prior=local[i-1]
            if (invoice.marketplace=='Amazon' and invoice.order and not prior._invoice
                    and not prior.text.strip()):label_index=i-1
        if label_index is not None:
            label=local[label_index]
            slips=[j for j,s in enumerate(local) if j not in used and getattr(s,'_packing',False) and matches(s)]
            combined=Shipment(label.source,label.pages+[p for j in attached+slips for p in local[j].pages],
                invoice.marketplace,invoice.order,label.awb,label.courier,[],
                label.text+'\n--- Paired invoice ---\n'+'\n'.join(local[j].text for j in attached),warnings)
            combined._invoice=False
            for j in attached:combined.items.extend(local[j].items)
            if not combined.items:combined.items=label.items
            if any(not amazon_invoice_items(local[j].text) and re.search(r'\|\s*[A-Z0-9]{10}\s*\(',local[j].text) for j in attached):
                combined.warnings.append('Some invoice product rows could not be read')
            used.add(label_index);used.update(slips)
        else:
            combined=invoice
            combined.pages=[p for j in attached for p in local[j].pages]
            combined.items=[item for j in attached for item in local[j].items]
            combined.text='\n'.join(local[j].text for j in attached)
            combined.warnings=warnings
            if not invoice_only:combined.warnings.append('Invoice attachment uncertain; attach to its label manually')
        used.update(attached);groups.append(combined)
    for i,s in enumerate(local):
        if i not in used:
            if s._invoice:s.warnings.append('Invoice continuation could not be attached')
            groups.append(s)
    return sorted(groups,key=lambda s:min(s.pages))

def identity(item):
    return ('sku', item.sku.casefold()) if item.sku else ('asin', item.asin.casefold()) if item.asin else ('fsn',item.fsn.casefold()) if item.fsn else ('name', item.name.casefold())

def sorted_shipments(shipments, mode='SKU', separate=True):
    totals = defaultdict(int)
    appearance = {}
    def product(s):
        if not s.items: return ''
        item = s.items[0]
        if mode == 'ASIN': return (item.asin or item.sku or item.name).casefold()
        if mode == 'Product name': return (item.name or item.sku or item.asin).casefold()
        return str(identity(item))
    for s in shipments:
        appearance.setdefault((s.marketplace if separate else '', product(s)),len(appearance))
        if not s.issues(): totals[(s.marketplace if separate else '', product(s))] += sum(i.quantity for i in s.items)
    def key(s):
        if s.issues(): return (1, '', 0, '', '', '')
        market = s.marketplace if separate else ''
        group = product(s)
        rank = -totals[(market,group)] if mode == 'Quantity (highest group first)' else 0
        if mode == 'Product groups (original order)':
            return (0,market,appearance[(market,group)],'','','')
        courier = s.courier.casefold() if mode == 'Courier' else ''
        return (0, market, rank, courier, group, s.order)
    return sorted(shipments, key=key)

def pick_rows(shipments, separate=True):
    groups = {}
    for s in shipments:
        if s.issues(): continue
        for i in s.items:
            k = (s.marketplace if separate else '', identity(i))
            g = groups.setdefault(k, dict(sku=set(),asin=set(),name=set(),quantity=0,orders=set(),market=set()))
            for field in ('sku','asin','name'):
                if getattr(i,field): g[field].add(getattr(i,field))
            g['quantity'] += i.quantity
            g['orders'].add((s.marketplace, s.order or s.awb or (s.source,tuple(s.pages))))
            g['market'].add(s.marketplace)
    return [['☐', ', '.join(sorted(g['sku'])), ', '.join(sorted(g['asin'])), '; '.join(sorted(g['name'])), g['quantity'], len(g['orders']), ', '.join(sorted(g['market']))] for g in groups.values()]
