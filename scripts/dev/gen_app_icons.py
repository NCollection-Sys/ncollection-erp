#!/usr/bin/env python3
"""Generate the NCollection Home-application icons from ONE definition.

Why a generator and not hand-made files: every NCollection application tile has to look like the same family (the
existing Reports and Barcode icons: 128x128, rounded square in the brand blue, a white glyph). Drawing each icon
separately is how a family drifts. The glyphs below are the only place an icon's shape is decided, and re-running this
script reproduces every file byte-for-byte.

Odoo's own mechanism does the loading: a root menu declares `web_icon="<module>,static/description/<file>.png"` and the
Home launcher reads the stored `web_icon_data` (ir.ui.menu.nc_tenant_apps). Nothing is loaded at runtime from anywhere
else, and there is no icon font or third-party package.

Needs Pillow (the odoo image has it):
    docker compose exec -T odoo python3 - < scripts/dev/gen_app_icons.py
or, with Pillow on the host:  python scripts/dev/gen_app_icons.py custom_addons
"""
import math
import os
import sys

from PIL import Image, ImageDraw

SIZE = 128
SCALE = 4                                   # draw 4x, downsample: smooth edges without a vector dependency
BLUE = (27, 94, 160, 255)                   # the brand blue of the existing Reports / Barcode icons
WHITE = (255, 255, 255, 255)
RADIUS = 26


def _canvas():
    big = SIZE * SCALE
    image = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=RADIUS * SCALE, fill=BLUE)
    return image, draw


def _s(value):
    return value * SCALE


def summary(draw):
    """Summary: a 2x2 grid of tiles, the executive overview."""
    for col in range(2):
        for row in range(2):
            x, y = 30 + col * 36, 30 + row * 36
            draw.rounded_rectangle((_s(x), _s(y), _s(x + 32), _s(y + 32)), radius=_s(6), fill=WHITE)


def alerts(draw):
    """Anomaly Alerts: a bell."""
    draw.pieslice((_s(34), _s(24), _s(94), _s(84)), 180, 360, fill=WHITE)        # the dome
    draw.polygon([(_s(34), _s(54)), (_s(94), _s(54)), (_s(104), _s(88)), (_s(24), _s(88))], fill=WHITE)
    draw.rounded_rectangle((_s(22), _s(86), _s(106), _s(94)), radius=_s(4), fill=WHITE)
    draw.ellipse((_s(54), _s(94), _s(74), _s(112)), fill=WHITE)                   # the clapper
    draw.rounded_rectangle((_s(60), _s(16), _s(68), _s(28)), radius=_s(3), fill=WHITE)


def workspace(draw):
    """Workspace Settings: a gear."""
    cx = cy = 64
    for step in range(8):
        angle = step * 45
        corners = []
        for dx, dy in ((-7, -40), (7, -40), (7, -22), (-7, -22)):
            rad = math.radians(angle)
            corners.append((_s(cx + dx * math.cos(rad) - dy * math.sin(rad)),
                            _s(cy + dx * math.sin(rad) + dy * math.cos(rad))))
        draw.polygon(corners, fill=WHITE)
    draw.ellipse((_s(cx - 32), _s(cy - 32), _s(cx + 32), _s(cy + 32)), fill=WHITE)
    draw.ellipse((_s(cx - 13), _s(cy - 13), _s(cx + 13), _s(cy + 13)), fill=BLUE)


def departments(draw):
    """Department Dashboards: a pie chart with a separated slice."""
    draw.pieslice((_s(22), _s(26), _s(100), _s(104)), 0, 270, fill=WHITE)
    draw.pieslice((_s(60), _s(14), _s(110), _s(64)), 270, 360, fill=WHITE)


def reseller(draw):
    """Reseller: a parent node linked to two partner nodes."""
    line = _s(7)
    for end in ((34, 96), (94, 96)):
        draw.line((_s(64), _s(40), _s(end[0]), _s(end[1])), fill=WHITE, width=line)
    for cx, cy, r in ((64, 38, 17), (34, 96, 14), (94, 96, 14)):
        draw.ellipse((_s(cx - r), _s(cy - r), _s(cx + r), _s(cy + r)), fill=WHITE)


def platform(draw):
    """NCollection SaaS: three stacked layers, the platform every workspace sits on."""
    for top in (30, 52, 74):
        draw.rounded_rectangle((_s(22), _s(top), _s(106), _s(top + 22)), radius=_s(7), fill=WHITE)
    for top in (41, 63):
        draw.rectangle((_s(30), _s(top - 2), _s(98), _s(top + 2)), fill=BLUE)


def branding(draw):
    """Branding: a palette with colour wells."""
    draw.ellipse((_s(18), _s(24), _s(110), _s(104)), fill=WHITE)
    draw.ellipse((_s(78), _s(70), _s(104), _s(96)), fill=BLUE)                    # the thumb hole
    for cx, cy in ((42, 48), (66, 40), (88, 52), (36, 74)):
        draw.ellipse((_s(cx - 8), _s(cy - 8), _s(cx + 8), _s(cy + 8)), fill=BLUE)


def cheque(draw):
    """Cheque print: a cheque (payee line, amount box, signature line) with a pen across the corner."""
    draw.rounded_rectangle((_s(16), _s(34), _s(112), _s(94)), radius=_s(7), fill=WHITE)
    draw.rectangle((_s(26), _s(48), _s(74), _s(54)), fill=BLUE)                    # payee line
    draw.rectangle((_s(26), _s(64), _s(58), _s(69)), fill=BLUE)                    # amount in words
    draw.rounded_rectangle((_s(80), _s(60), _s(102), _s(72)), radius=_s(3), fill=BLUE)   # amount box
    draw.rectangle((_s(26), _s(80), _s(62), _s(83)), fill=BLUE)                    # signature line


# module -> {file name: glyph}. A file name other than icon.png is for a module that owns several Home tiles.
ICONS = {
    'ncollection_core': {'icon_summary.png': summary, 'icon_alerts.png': alerts, 'icon_workspace.png': workspace},
    'ncollection_account_dashboard': {'icon.png': departments},
    'ncollection_reseller': {'icon.png': reseller},
    'ncollection_subscription': {'icon.png': platform},
    'ncollection_branding': {'icon.png': branding},
    'ncollection_cheque_print': {'icon.png': cheque},
}


def main(root):
    for module, files in ICONS.items():
        target = os.path.join(root, module, 'static', 'description')
        os.makedirs(target, exist_ok=True)
        for name, glyph in files.items():
            image, draw = _canvas()
            glyph(draw)
            image = image.resize((SIZE, SIZE), Image.LANCZOS)
            image.save(os.path.join(target, name), optimize=False)
            print('wrote', os.path.join(module, 'static', 'description', name), image.size)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '/mnt/extra-addons')
