from io import BytesIO
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException

MAX_FILE_BYTES=10*1024*1024
Image.MAX_IMAGE_PIXELS=25_000_000

def compress_photo(data):
    if len(data)>MAX_FILE_BYTES:raise HTTPException(413,'Each photo must be 10 MB or smaller')
    try:
        with Image.open(BytesIO(data)) as original:
            if original.width*original.height>25_000_000:raise ValueError('Image exceeds 25 megapixels')
            image=ImageOps.exif_transpose(original).convert('RGBA')
            image.thumbnail((1200,1200),Image.Resampling.LANCZOS)
            background=Image.new('RGB',image.size,'white');background.paste(image,mask=image.getchannel('A'))
            result=BytesIO();background.save(result,'JPEG',quality=85,optimize=True)
            return {'filename':uuid4().hex+'.jpg','data':result.getvalue(),'width':background.width,'height':background.height}
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError) as exc:
        raise HTTPException(422,'Photo could not be read. Use a PNG, JPEG or WebP image (up to 25 megapixels).') from exc

def add_photos(conn,paths,product_id,images):
    existing=conn.execute('SELECT count(*),coalesce(max(position),-1) FROM photos WHERE product_id=?',(product_id,)).fetchone()
    if existing[0]+len(images)>3:raise HTTPException(422,'A product can have at most 3 photos. Remove one before adding more.')
    written=[]
    try:
        for n,image in enumerate(images):
            path=paths.images/image['filename'];path.write_bytes(image['data']);written.append(path)
            conn.execute('INSERT INTO photos (product_id,filename,position,width,height,bytes) VALUES (?,?,?,?,?,?)',(product_id,image['filename'],existing[1]+n+1,image['width'],image['height'],len(image['data'])))
        return written
    except Exception:
        for path in written:path.unlink(missing_ok=True)
        raise
