"""
Утилиты для обработки изображений
"""
import io
from PIL import Image


def smart_compress_image(file_bytes: bytes) -> bytes:
    """Сжимает изображение если > 9.5 МБ"""
    LIMIT_BYTES = 9.5 * 1024 * 1024 
    
    if len(file_bytes) <= LIMIT_BYTES:
        return file_bytes 
    
    print(f"⚠️ Файл слишком большой ({len(file_bytes) / 1024 / 1024:.2f} MB). Сжимаю...")
    
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert("RGB")
            
        max_dimension = 2560
        if max(img.size) > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            
        output_io = io.BytesIO()
        img.save(output_io, format='JPEG', quality=85, optimize=True)
        return output_io.getvalue()
    except Exception as e:
        print(f"❌ Ошибка сжатия: {e}")
        return file_bytes


def normalize_image_urls(image_urls) -> list:
    """✅ ЕДИНАЯ функция нормализации URL"""
    if not image_urls:
        return []
    if isinstance(image_urls, str):
        return [image_urls]
    if isinstance(image_urls, list):
        return image_urls
    return []


def create_collage(images: list, max_size=1024) -> Image.Image:
    """
    Создаёт коллаж из 2-4 изображений
    
    2 фото: горизонтально [img1][img2]
    3-4 фото: сетка 2x2
    """
    count = len(images)
    
    if count == 2:
        cols, rows = 2, 1
    elif count <= 4:
        cols, rows = 2, 2
    else:
        raise ValueError("Max 4 images")
    
    cell_w = max_size // cols
    cell_h = max_size // rows
    
    canvas = Image.new('RGB', (max_size, max_size), 'white')
    
    for idx, img in enumerate(images):
        img_resized = img.copy()
        img_resized.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
        
        col = idx % cols
        row = idx // cols
        
        x = col * cell_w + (cell_w - img_resized.width) // 2
        y = row * cell_h + (cell_h - img_resized.height) // 2
        
        canvas.paste(img_resized, (x, y))
    
    return canvas
