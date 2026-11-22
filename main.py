#!/usr/bin/env python3
"""news-image-20261121: draw a one-page news digest for Sat 21 Nov 2026.

Pure Python 3 standard library. Parses real TrueType fonts (DejaVu,
vendored in fonts/ so the grader image needs no system fonts) and
rasterises their glyph outlines itself, draws vector illustrations for four
stories, and writes a deterministic PNG to $MAKEFUN_OUTPUT_DIR/entry.png
(or /output/entry.png). No network, no randomness, no timestamps in output.
"""

import os
import struct
import sys
import zlib

# ---------------------------------------------------------------- fonts
# Fonts are vendored in fonts/ next to this file: the grader image has
# no system fonts, so /usr/share/fonts paths must never be the only copy.
# The vendored file is tried first; system paths stay as local fallbacks.
_HERE = os.path.dirname(os.path.abspath(__file__))


def _font_path(filename, system_path):
    """Prefer the font bundled in this repo; fall back to the system copy."""
    local = os.path.join(_HERE, "fonts", filename)
    if os.path.exists(local):
        return local
    return system_path


FONT_CANDIDATES = {
    "sans": [
        _font_path("DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ],
    "sans_bold": [
        _font_path("DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ],
    "serif_bold": [
        _font_path("DejaVuSerif-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    ],
}


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def _i16(b, o):
    return struct.unpack_from(">h", b, o)[0]


def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


class TrueTypeFont:
    """Minimal TrueType reader: cmap (fmt 4/12), hmtx, glyf (simple+composite)."""

    def __init__(self, path):
        with open(path, "rb") as fh:
            self.data = fh.read()
        d = self.data
        num_tables = _u16(d, 4)
        self.tables = {}
        for i in range(num_tables):
            o = 12 + i * 16
            tag = d[o:o + 4].decode("latin-1")
            self.tables[tag] = (_u32(d, o + 8), _u32(d, o + 12))
        head = self._table("head")
        self.upem = _u16(head, 18)
        self.index_to_loc = _i16(head, 50)
        self.num_glyphs = _u16(self._table("maxp"), 4)
        hhea = self._table("hhea")
        self.num_hmetrics = _u16(hhea, 34)
        self._cmap = self._parse_cmap()
        self._glyph_cache = {}

    def _table(self, tag):
        off, _ln = self.tables[tag]
        return self.data[off:]

    def _parse_cmap(self):
        c = self._table("cmap")
        n = _u16(c, 2)
        best4 = None
        for i in range(n):
            o = 4 + i * 8
            fmt_off = _u32(c, o + 4)
            fmt = _u16(c, fmt_off)
            if fmt == 12:
                return self._cmap12(c[fmt_off:])
            if fmt == 4 and best4 is None:
                best4 = c[fmt_off:]
        return self._cmap4(best4)

    def _cmap4(self, t):
        seg_count = _u16(t, 6) // 2
        end = [_u16(t, 14 + 2 * i) for i in range(seg_count)]
        start = [_u16(t, 16 + 2 * seg_count + 2 * i) for i in range(seg_count)]
        delta = [_i16(t, 16 + 4 * seg_count + 2 * i) for i in range(seg_count)]
        ro_base = 16 + 6 * seg_count
        ro = [_u16(t, ro_base + 2 * i) for i in range(seg_count)]
        glyphs = t[ro_base + 2 * seg_count:]
        mapping = {}
        for s in range(seg_count):
            for code in range(start[s], end[s] + 1):
                if code == 0xFFFF:
                    continue
                if ro[s] == 0:
                    gid = (code + delta[s]) % 65536
                else:
                    idx = ro[s] // 2 + (code - start[s]) + s - seg_count
                    gid = _u16(glyphs, 2 * idx)
                    if gid:
                        gid = (gid + delta[s]) % 65536
                mapping[code] = gid
        return mapping

    def _cmap12(self, t):
        n = _u32(t, 12)
        mapping = {}
        for i in range(n):
            o = 16 + i * 12
            sc, ec, sg = _u32(t, o), _u32(t, o + 4), _u32(t, o + 8)
            for code in range(sc, ec + 1):
                mapping[code] = sg + (code - sc)
        return mapping

    def glyph_index(self, ch):
        return self._cmap.get(ord(ch), 0)

    def advance(self, gid):
        hmtx = self._table("hmtx")
        if gid < self.num_hmetrics:
            return _u16(hmtx, gid * 4)
        return _u16(hmtx, (self.num_hmetrics - 1) * 4)

    def _glyph_bytes(self, gid):
        loca = self._table("loca")
        if self.index_to_loc == 0:
            a, b = _u16(loca, gid * 2) * 2, _u16(loca, gid * 2 + 2) * 2
        else:
            a, b = _u32(loca, gid * 4), _u32(loca, gid * 4 + 4)
        if a == b:
            return None
        return self._table("glyf")[a:b]

    def contours(self, gid):
        """Return list of contours; each is a list of (x, y, on_curve)."""
        if gid in self._glyph_cache:
            return self._glyph_cache[gid]
        raw = self._glyph_bytes(gid)
        if raw is None:
            self._glyph_cache[gid] = []
            return []
        n_cont = _i16(raw, 0)
        if n_cont >= 0:
            out = self._simple_contours(raw, n_cont)
        else:
            out = self._composite_contours(raw)
        self._glyph_cache[gid] = out
        return out

    def _simple_contours(self, raw, n_cont):
        end_pts = [_u16(raw, 10 + 2 * i) for i in range(n_cont)]
        n_pts = end_pts[-1] + 1 if n_pts_ok(end_pts) else 0
        instr_len = _u16(raw, 10 + 2 * n_cont)
        p = 10 + 2 * n_cont + 2 + instr_len
        flags = []
        while len(flags) < n_pts:
            f = raw[p]
            p += 1
            flags.append(f)
            if f & 0x08:
                rep = raw[p]
                p += 1
                flags.extend([f] * rep)
        xs = []
        x = 0
        for f in flags:
            if f & 0x02:
                dx = raw[p]
                p += 1
                x += dx if (f & 0x10) else -dx
            elif not (f & 0x10):
                x += _i16(raw, p)
                p += 2
            xs.append(x)
        ys = []
        y = 0
        for f in flags:
            if f & 0x04:
                dy = raw[p]
                p += 1
                y += dy if (f & 0x20) else -dy
            elif not (f & 0x20):
                y += _i16(raw, p)
                p += 2
            ys.append(y)
        pts = [(xs[i], ys[i], bool(flags[i] & 0x01)) for i in range(n_pts)]
        contours = []
        start = 0
        for e in end_pts:
            contours.append(pts[start:e + 1])
            start = e + 1
        return contours

    def _composite_contours(self, raw):
        p = 10
        out = []
        while True:
            flags = _u16(raw, p)
            comp_gid = _u16(raw, p + 2)
            p += 4
            if flags & 0x0001:  # args are words
                dx, dy = _i16(raw, p), _i16(raw, p + 2)
                p += 4
            else:
                dx = raw[p] if raw[p] < 128 else raw[p] - 256
                dy = raw[p + 1] if raw[p + 1] < 128 else raw[p + 1] - 256
                p += 2
            if flags & 0x0008:  # scale
                s = _i16(raw, p) / 16384.0
                p += 2
                sx = sy = s
                xy = yx = 0.0
            elif flags & 0x0040:  # x/y scale
                sx = _i16(raw, p) / 16384.0
                sy = _i16(raw, p + 2) / 16384.0
                p += 4
                xy = yx = 0.0
            elif flags & 0x0080:  # 2x2
                sx = _i16(raw, p) / 16384.0
                xy = _i16(raw, p + 2) / 16384.0
                yx = _i16(raw, p + 4) / 16384.0
                sy = _i16(raw, p + 6) / 16384.0
                p += 8
            else:
                sx = sy = 1.0
                xy = yx = 0.0
            for contour in self.contours(comp_gid):
                out.append([(x * sx + y * yx + dx, x * xy + y * sy + dy, on)
                            for (x, y, on) in contour])
            if not (flags & 0x0020):  # more components
                break
        return out


def n_pts_ok(end_pts):
    return len(end_pts) > 0


def load_font(kind):
    for path in FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            return TrueTypeFont(path)
    raise FileNotFoundError("no TrueType font found for " + kind)

# ------------------------------------------------------- glyph rasteriser
def flatten_contour(contour):
    """Expand on/off-curve points into a polyline (quadratic flattening)."""
    if not contour:
        return []
    pts = list(contour)
    if not pts[0][2]:
        # contour starts off-curve: implied on-curve midpoint with last point
        last = pts[-1]
        if last[2]:
            start = last
        else:
            start = ((pts[0][0] + last[0]) / 2.0, (pts[0][1] + last[1]) / 2.0, True)
        pts = [start] + pts
    out = [(pts[0][0], pts[0][1])]
    i = 1
    n = len(pts)
    while i < n:
        p = pts[i]
        if p[2]:
            out.append((p[0], p[1]))
            i += 1
        else:
            nxt = pts[i + 1] if i + 1 < n else pts[0]
            if nxt[2]:
                end = (nxt[0], nxt[1])
                i += 2
            else:
                end = ((p[0] + nxt[0]) / 2.0, (p[1] + nxt[1]) / 2.0, True)
                i += 1
            start = out[-1]
            for step in range(1, 9):
                t = step / 8.0
                mt = 1.0 - t
                out.append((mt * mt * start[0] + 2 * mt * t * p[0] + t * t * end[0],
                            mt * mt * start[1] + 2 * mt * t * p[1] + t * t * end[1]))
    return out


class GlyphBitmap:
    __slots__ = ("rows", "w", "h", "left", "top", "advance")

    def __init__(self, rows, w, h, left, top, advance):
        self.rows = rows      # list of bytearray, 1 = ink
        self.w = w
        self.h = h
        self.left = left      # pixel offset of bitmap left vs pen x
        self.top = top        # pixel offset of bitmap top vs baseline
        self.advance = advance


def render_glyph(font, ch, px):
    gid = font.glyph_index(ch)
    contours = font.contours(gid)
    scale = px / font.upem
    adv = font.advance(gid) * scale
    if not contours:
        return GlyphBitmap([], 0, 0, 0, 0, adv)
    polys = [flatten_contour(c) for c in contours]
    polys = [p for p in polys if len(p) >= 2]
    if not polys:
        return GlyphBitmap([], 0, 0, 0, 0, adv)
    xs = [x for p in polys for (x, _y) in p]
    ys = [y for p in polys for (_x, y) in p]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    left = int(xmin * scale) - 1
    top = -int(ymax * scale) - 1          # vs baseline (y grows down)
    w = int(xmax * scale) - left + 2
    h = int(ymax * scale) - int(ymin * scale) + 2
    rows = [bytearray(w) for _ in range(h)]
    edges = []
    for poly in polys:
        for a, b in zip(poly, poly[1:] + poly[:1]):
            x0, y0 = a[0] * scale, -a[1] * scale
            x1, y1 = b[0] * scale, -b[1] * scale
            if y0 != y1:
                edges.append((y0, y1, x0, x1))
    for r in range(h):
        yc = top + r + 0.5
        xs_hit = []
        for (y0, y1, x0, x1) in edges:
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                xs_hit.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
        xs_hit.sort()
        row = rows[r]
        for k in range(0, len(xs_hit) - 1, 2):
            a = int(xs_hit[k] - left)
            b = int(xs_hit[k + 1] - left)
            for xx in range(max(0, a), min(w, b)):
                row[xx] = 1
    return GlyphBitmap(rows, w, h, left, top, adv)


class TextEngine:
    def __init__(self):
        self.fonts = {}
        self.cache = {}

    def font(self, kind):
        if kind not in self.fonts:
            self.fonts[kind] = load_font(kind)
        return self.fonts[kind]

    def glyph(self, kind, ch, px):
        key = (kind, ch, px)
        g = self.cache.get(key)
        if g is None:
            g = render_glyph(self.font(kind), ch, px)
            self.cache[key] = g
        return g

    def width(self, kind, px, text):
        return sum(self.glyph(kind, c, px).advance for c in text)

    def draw(self, canvas, kind, px, x, baseline_y, text, color):
        pen = x
        for ch in text:
            g = self.glyph(kind, ch, px)
            if g.rows:
                canvas.blit(g.rows, int(round(pen)) + g.left, baseline_y + g.top, color)
            pen += g.advance
        return pen


def wrap_text(engine, kind, px, text, max_w):
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if engine.width(kind, px, trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- canvas
class Canvas:
    def __init__(self, w, h, bg):
        self.w, self.h = w, h
        self.pix = bytearray(bg * (w * h))

    def _idx(self, x, y):
        return (y * self.w + x) * 3

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = self._idx(x, y)
            self.pix[i:i + 3] = color

    def blit(self, rows, x0, y0, color):
        for r, row in enumerate(rows):
            y = y0 + r
            if y < 0 or y >= self.h:
                continue
            for x, v in enumerate(row):
                if v:
                    self.set(x0 + x, y, color)

    def rect(self, x, y, w, h, color):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        for yy in range(y0, y1):
            i = self._idx(x0, yy)
            for xx in range(x0, x1):
                self.pix[i:i + 3] = color
                i += 3

    def hline(self, x, y, w, color, thick=1):
        self.rect(x, y - thick // 2, w, thick, color)

    def vline(self, x, y, h, color, thick=1):
        self.rect(x - thick // 2, y, thick, h, color)

    def line(self, x0, y0, x1, y1, color, thick=1):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        steps = max(dx, dy, 1)
        for s in range(steps + 1):
            t = s / steps
            self.rect(round(x0 + (x1 - x0) * t) - thick // 2,
                      round(y0 + (y1 - y0) * t) - thick // 2,
                      thick, thick, color)

    def circle(self, cx, cy, r, color):
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r:
                    self.set(xx, yy, color)

    def ring(self, cx, cy, r, color, thick=3):
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                d = (xx - cx) ** 2 + (yy - cy) ** 2
                if (r - thick) ** 2 <= d <= r * r:
                    self.set(xx, yy, color)

    def poly(self, points, color):
        if len(points) < 3:
            return
        ys = [p[1] for p in points]
        for yy in range(max(0, min(ys)), min(self.h, max(ys) + 1)):
            yc = yy + 0.5
            hits = []
            for a, b in zip(points, points[1:] + points[:1]):
                if (a[1] <= yc < b[1]) or (b[1] <= yc < a[1]):
                    hits.append(a[0] + (yc - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
            hits.sort()
            for k in range(0, len(hits) - 1, 2):
                for xx in range(max(0, int(hits[k])), min(self.w, int(hits[k + 1]))):
                    self.set(xx, yy, color)

    def arc(self, cx, cy, r, a0, a1, color, thick=4):
        import math
        steps = max(8, int(r * abs(a1 - a0) / 6))
        for s in range(steps + 1):
            a = a0 + (a1 - a0) * s / steps
            self.circle(int(cx + r * math.cos(a)), int(cy + r * math.sin(a)),
                        thick // 2, color)


# ------------------------------------------------------------------- PNG
def write_png(canvas, path):
    raw = bytearray()
    stride = canvas.w * 3
    for y in range(canvas.h):
        raw.append(0)
        raw += canvas.pix[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", canvas.w, canvas.h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", comp)
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)

# ------------------------------------------------------------------ layout
W, H = 1024, 1024
NAVY = bytes((16, 32, 66))
NAVY2 = bytes((24, 48, 92))
GOLD = bytes((216, 164, 65))
WHITE = bytes((255, 255, 255))
INK = bytes((24, 24, 28))
GREY = bytes((92, 96, 104))
LIGHT = bytes((238, 240, 243))
BORDER = bytes((198, 202, 210))
RED = bytes((164, 30, 34))
TEAL = bytes((14, 107, 122))
MAROON = bytes((109, 31, 44))
SLATE = bytes((51, 58, 74))
SKY = bytes((214, 231, 240))
SAND = bytes((247, 240, 224))
PALE_RED = bytes((250, 232, 230))
PALE_TEAL = bytes((224, 240, 242))
PALE_GOLD = bytes((250, 243, 226))

PANELS = [
    dict(kicker="EUROPE  |  OVERNIGHT, FRI 20 NOV", color=RED, bg=PALE_RED,
         art="missiles",
         headline="MISSILES LAND IN POLAND",
         body=("Two Russian missiles and a suspected drone came down in "
               "south-east Poland during a huge barrage on Ukraine: 400+ "
               "drones, 110 missiles; at least 8 killed in Ukraine. Warsaw "
               "calls it an \u201cintentional provocation\u201d; NATO calls it "
               "a \u201cgrave violation\u201d of Polish airspace.")),
    dict(kicker="INDIA  |  LANDFALL, FRI 20 NOV", color=TEAL, bg=PALE_TEAL,
         art="cyclone",
         headline="CYCLONE AKVAN HITS ODISHA",
         body=("Severe Cyclone Akvan came ashore near Gopalpur, winds "
               "gusting to 90 km/h. About 370,000 people were evacuated to "
               "shelters; no major damage or loss of life reported so far.")),
    dict(kicker="THE HAGUE  |  RULING DELIVERED 20 NOV", color=MAROON, bg=SAND,
         art="court",
         headline="WORLD COURT: MYANMAR GUILTY OF GENOCIDE",
         body=("The International Court of Justice ruled 9\u20136 that "
               "Myanmar\u2019s forces committed state-sponsored genocide "
               "against the Rohingya. The landmark verdict is welcomed by "
               "the Rohingya.")),
    dict(kicker="MARKETS  |  FRIDAY 20 NOV", color=SLATE, bg=PALE_GOLD,
         art="stocks",
         headline="STOCKS SLIDE INTO CORRECTION",
         body=("US stocks fell about 3.7% on Friday as an AI and chip "
               "sell-off pushed the S&P 500 and the Dow into correction "
               "territory, closing a bruising week for markets.")),
]

ALSO = [
    ("SUMMIT", "Trump, Xi and Putin leave their first trilateral summit in "
               "Shenzhen with few concrete results."),
    ("WASHINGTON", "US Senate rejects the Iran war-powers resolution 51\u201348; "
                   "Trump says an Iran decision may wait until 2027."),
    ("CLIMATE", "COP31 talks in Turkey enter their crucial final stage: "
                "fossil fuels and climate finance."),
    ("BRUSSELS", "UK and EU sign deals on food trade, carbon markets and "
                 "youth mobility at a summit."),
]


def draw_missiles(cv, x, y, w, h):
    cv.rect(x, y, w, h, bytes((18, 24, 48)))
    for cx, cy, r in [(x + 90, y + 78, 3), (x + 210, y + 40, 2), (x + 330, y + 60, 3),
                      (x + 420, y + 30, 2), (x + 150, y + 100, 2)]:
        cv.circle(cx, cy, r, WHITE)
    cv.rect(x, y + h - 26, w, 26, bytes((52, 84, 48)))
    # two missile streaks with fireballs and ground bursts
    for (sx, sy, ex, ey) in [(x + 60, y + 12, x + 190, y + h - 30),
                             (x + 300, y + 8, x + 380, y + h - 34)]:
        cv.line(sx, sy, ex, ey, GOLD, 4)
        cv.line(sx + 6, sy - 4, ex + 6, ey - 4, bytes((255, 220, 130)), 2)
        cv.circle(ex, ey, 7, bytes((255, 120, 40)))
        for k in range(8):
            import math
            a = k * math.pi / 4
            cv.line(ex, ey, ex + int(26 * math.cos(a)), ey + int(18 * math.sin(a)),
                    bytes((255, 90, 30)), 3)
    cv.circle(x + 430, y + 55, 16, bytes((255, 90, 30)))
    cv.circle(x + 430, y + 55, 8, bytes((255, 200, 90)))


def draw_cyclone(cv, x, y, w, h):
    import math
    cv.rect(x, y, w, h, bytes((16, 74, 96)))
    cv.rect(x, y + h - 30, w, 30, bytes((196, 164, 110)))
    cx, cy = x + w // 2 - 30, y + h // 2 - 6
    for r in range(14, 62, 9):
        cv.arc(cx, cy, r, 0.4 + r * 0.05, 0.4 + r * 0.05 + 4.6, WHITE, 5)
    cv.circle(cx, cy, 10, bytes((255, 220, 130)))


def draw_court(cv, x, y, w, h):
    cv.rect(x, y, w, h, bytes((96, 42, 44)))
    cv.rect(x, y + h - 22, w, 22, bytes((216, 164, 65)))
    # three columns and a pediment
    cv.poly([(x + 60, y + 22), (x + w - 60, y + 22), (x + w - 100, y + 52),
             (x + 100, y + 52)], GOLD)
    for cx in (x + 120, x + w // 2, x + w - 120):
        cv.rect(cx - 14, y + 56, 28, h - 84, bytes((232, 222, 200)))
        cv.rect(cx - 20, y + 52, 40, 8, bytes((232, 222, 200)))
        cv.rect(cx - 20, y + h - 34, 40, 8, bytes((232, 222, 200)))


def draw_stocks(cv, x, y, w, h):
    cv.rect(x, y, w, h, bytes((34, 40, 54)))
    for gy in range(y + 18, y + h - 10, 22):
        cv.hline(x + 10, gy, w - 20, bytes((70, 78, 96)), 1)
    pts = [(x + 20, y + 22), (x + 110, y + 34), (x + 180, y + 30),
           (x + 250, y + 58), (x + 320, y + 52), (x + 390, y + 92),
           (x + 460, y + h - 22)]
    for a, b in zip(pts, pts[1:]):
        cv.line(a[0], a[1], b[0], b[1], bytes((255, 80, 70)), 5)
    for (px, py) in pts:
        cv.circle(px, py, 5, WHITE)
    ex, ey = pts[-1]
    cv.poly([(ex - 26, ey - 26), (ex + 4, ey - 26), (ex - 11, ey + 2)], bytes((255, 80, 70)))
    cv.circle(x + 60, y + 26, 12, bytes((255, 80, 70)))
    cv.line(x + 60, y + 18, x + 60, y + 34, WHITE, 3)
    cv.line(x + 52, y + 26, x + 68, y + 26, WHITE, 3)


ART = {"missiles": draw_missiles, "cyclone": draw_cyclone,
       "court": draw_court, "stocks": draw_stocks}


def draw_page():
    eng = TextEngine()
    cv = Canvas(W, H, WHITE)
    # ---- masthead
    cv.rect(0, 0, W, 128, NAVY)
    cv.rect(0, 128, W, 4, GOLD)
    title = "THE WORLD IN ONE DAY"
    tw = eng.width("serif_bold", 58, title)
    eng.draw(cv, "serif_bold", 58, (W - tw) // 2, 66, title, WHITE)
    date = "Saturday 21 November 2026"
    dw = eng.width("sans_bold", 30, date)
    eng.draw(cv, "sans_bold", 30, (W - dw) // 2, 112, date, GOLD)
    tag = "The day's most consequential stories \u2014 in one page"
    tgw = eng.width("sans", 19, tag)
    eng.draw(cv, "sans", 19, (W - tgw) // 2, 152, tag, GREY)

    # ---- four panels, 2 x 2
    px0, py0 = 24, 164
    pw, ph, gap = 480, 324, 16
    for i, p in enumerate(PANELS):
        x = px0 + (i % 2) * (pw + gap)
        y = py0 + (i // 2) * (ph + gap)
        cv.rect(x, y, pw, ph, WHITE)
        cv.rect(x, y, pw, 2, p["color"])
        cv.rect(x, y + ph - 2, pw, 2, p["color"])
        cv.rect(x, y, 2, ph, p["color"])
        cv.rect(x + pw - 2, y, 2, ph, p["color"])
        # kicker band
        cv.rect(x + 2, y + 2, pw - 4, 32, p["color"])
        eng.draw(cv, "sans_bold", 16, x + 14, y + 26, p["kicker"], WHITE)
        # illustration
        ART[p["art"]](cv, x + 2, y + 34, pw - 4, 118)
        # headline (up to 2 lines)
        hy = y + 178
        head_lines = wrap_text(eng, "sans_bold", 27, p["headline"], pw - 28)[:2]
        for line in head_lines:
            eng.draw(cv, "sans_bold", 27, x + 14, hy, line, INK)
            hy += 32
        # body: 5 lines max under a 1-line headline, 4 under a 2-line one
        by = hy + 6
        max_body = 5 if len(head_lines) == 1 else 4
        for line in wrap_text(eng, "sans", 18, p["body"], pw - 28)[:max_body]:
            eng.draw(cv, "sans", 18, x + 14, by, line, INK)
            by += 23

    # ---- also today strip
    sy = py0 + 2 * ph + gap + 8
    cv.rect(24, sy, W - 48, H - sy - 20, LIGHT)
    cv.rect(24, sy, W - 48, 2, NAVY)
    eng.draw(cv, "sans_bold", 20, 40, sy + 24, "ALSO TODAY", NAVY)
    cv.hline(40, sy + 34, W - 104, BORDER, 1)
    col_w = (W - 48 - 32) // 2
    for i, (label, text) in enumerate(ALSO):
        cx = 40 + (i % 2) * col_w
        cy = sy + 50 + (i // 2) * 62
        eng.draw(cv, "sans_bold", 16, cx, cy, label + "  \u2014  ", RED)
        lw = eng.width("sans_bold", 16, label + "  \u2014  ")
        # first line shares the row with the label; later lines use full width
        first_w, rest_w = col_w - 16 - lw, col_w - 16
        lines, cur, limit = [], "", first_w
        for w in text.split(" "):
            trial = (cur + " " + w).strip()
            if eng.width("sans", 16, trial) <= limit or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur, limit = w, rest_w
        if cur:
            lines.append(cur)
        for j, line in enumerate(lines):
            if j == 0:
                eng.draw(cv, "sans", 16, cx + lw, cy, line, INK)
            else:
                eng.draw(cv, "sans", 16, cx, cy + j * 20, line, INK)
    return cv


def main():
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(out_dir, exist_ok=True)   # never remove the directory itself
    cv = draw_page()
    path = os.path.join(out_dir, "entry.png")
    write_png(cv, path)
    print("wrote", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
