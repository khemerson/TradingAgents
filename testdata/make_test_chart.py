"""Genere un graphique en chandeliers synthetique, en PNG pur (aucune dependance).

Sert d'image de test a l'analyste vision : trend haussier puis correction, avec
une resistance horizontale marquee, pour que le rapport ait de la matiere.
"""

import struct
import zlib

W, H = 900, 500
BG = (18, 20, 28)
GRID = (44, 48, 60)
UP = (38, 190, 120)
DOWN = (230, 76, 92)
AXIS = (150, 155, 170)
LINE = (240, 190, 70)

px = [[BG for _ in range(W)] for _ in range(H)]


def rect(x0, y0, x1, y1, color):
    x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
    y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
    for y in range(y0, y1 + 1):
        row = px[y]
        for x in range(x0, x1 + 1):
            row[x] = color


# grille horizontale
for i in range(1, 8):
    y = int(H * i / 8)
    rect(60, y, W - 20, y, GRID)
# axes
rect(60, 20, 60, H - 40, AXIS)
rect(60, H - 40, W - 20, H - 40, AXIS)

# serie synthetique : montee, plateau sous resistance, cassure baissiere
closes = []
price = 100.0
moves = ([1.8] * 14) + ([0.3, -0.4] * 8) + ([-2.1] * 12) + ([0.6] * 6)
for m in moves:
    price += m
    closes.append(price)

lo, hi = min(closes) - 4, max(closes) + 4


def ytop(p):
    return int((H - 45) - (p - lo) / (hi - lo) * (H - 75))


n = len(closes)
step = (W - 100) // n
prev = closes[0] - 1.5
for i, c in enumerate(closes):
    o = prev
    h = max(o, c) + 1.4
    lw = min(o, c) - 1.4
    x = 70 + i * step
    color = UP if c >= o else DOWN
    rect(x + step // 2 - 1, ytop(h), x + step // 2, ytop(lw), color)  # meche
    rect(x + 1, ytop(max(o, c)), x + step - 3, ytop(min(o, c)) + 1, color)  # corps
    prev = c

# resistance horizontale au sommet
res = max(closes) - 1.0
yr = ytop(res)
for x in range(70, W - 20, 12):
    rect(x, yr, x + 6, yr, LINE)

raw = b"".join(
    b"\x00" + b"".join(struct.pack("BBB", *px[y][x]) for x in range(W)) for y in range(H)
)


def chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(raw, 9))
    + chunk(b"IEND", b"")
)

import sys

out = sys.argv[1] if len(sys.argv) > 1 else "test_chart.png"
with open(out, "wb") as f:
    f.write(png)
print(f"  image ecrite : {out} ({len(png)} octets, {W}x{H})")
