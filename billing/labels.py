"""Barcode images for price labels."""

import io
import re

import barcode
from barcode.writer import SVGWriter
from markupsafe import Markup

from .services import ean13_check


MM = 3.7795  # SVG user units per millimetre


def barcode_svg(code, bar_height=7):
    """An inline, scalable SVG barcode: EAN-13 for valid 13-digit codes, Code 128 for anything else.
    bar_height is in mm; the image shrinks to fit narrow labels but never grows past its real size."""
    code = (code or "").strip()
    if len(code) == 13 and code.isdigit() and ean13_check(code[:12]) == code[12]:
        symbol, options = barcode.get("ean13", code[:12], writer=SVGWriter()), {"module_width": 0.25}
    else:
        symbol, options = barcode.get("code128", code, writer=SVGWriter()), {"module_width": 0.2}
    buf = io.BytesIO()
    symbol.write(buf, options={**options, "module_height": bar_height, "font_size": 5, "text_distance": 2.5,
                               "quiet_zone": 1.5, "background": "white"})
    svg = buf.getvalue().decode()
    svg = svg[svg.index("<svg"):]
    size = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', svg)
    width, height = float(size.group(1)), float(size.group(2))
    svg = svg.replace(size.group(0), f'viewBox="0 0 {width * MM:.2f} {height * MM:.2f}" '
                                     f'style="width:100%;max-width:{width}mm;height:auto"', 1)
    return Markup(svg)
