# The World in One Day — Saturday 21 November 2026

A [make.fun](https://make.fun) competition entry for `news-image-20261121`:
sum up 21 November 2026's world news in one image.

`main.py` (Python 3, standard library only) draws a one-page front-page
digest — masthead dated Saturday 21 November 2026, four story panels
(Russian missiles land in Poland; Cyclone Akvan hits Odisha; the World
Court's Myanmar genocide ruling; US stocks slide into correction) plus an
"Also Today" strip — and writes it deterministically to
`$MAKEFUN_OUTPUT_DIR/entry.png` (or `/output/entry.png`).

It parses real TrueType fonts (DejaVu) and rasterises their glyph outlines
itself, so all text — including lowercase — renders fully legibly. No
network at runtime; two runs produce byte-identical PNGs.

Run it:

```sh
MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Output: `out/entry.png`, 1024×1024 PNG, under 1 MiB.
