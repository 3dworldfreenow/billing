"""Product photos: checked, shrunk and saved as small WebP files under instance/uploads/products."""

import os
import secrets

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 800  # pixels; plenty for a phone screen, small to download


class ImageError(ValueError):
    pass


def folder():
    path = os.path.join(current_app.instance_path, "uploads", "products")
    os.makedirs(path, exist_ok=True)
    return path


def save_product_image(upload, product):
    """Replace the product's photo with the uploaded one. The caller commits."""
    try:
        with Image.open(upload.stream) as img:
            img = ImageOps.exif_transpose(img)  # phone photos: honour the rotation
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info else "RGB")
            name = f"{product.id}-{secrets.token_hex(4)}.webp"
            img.save(os.path.join(folder(), name), "WEBP", quality=82, method=4)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("That file isn't a picture we can read. Use a JPG, PNG or WebP photo.")
    remove_product_image(product)
    product.image = name


def remove_product_image(product):
    if product.image:
        try:
            os.remove(os.path.join(folder(), product.image))
        except FileNotFoundError:
            pass
        product.image = None
