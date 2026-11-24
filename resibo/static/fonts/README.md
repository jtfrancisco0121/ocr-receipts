# Fonts

Self-hosted because the CSP blocks third-party font hosts. Both families are licensed under the
SIL Open Font License 1.1 (see `OFL.txt`). Latin subset (U+0020-007E, U+00A0-00FF, general
punctuation, arrow, minus sign and the peso sign U+20B1) made offline with fontTools
`pyftsubset` (woff2). fontTools is not a project dependency.

Source: github.com/google/fonts, `ofl/archivo` (Archivo 2.001, variable `wght` 100-900 and `wdth` 62-125)
and `ofl/courierprime` (Courier Prime 3.018). Checked on 2026-11-24.

Result of the fontTools cmap check for the peso sign (`U+20B1`), one line per shipped file:

- Archivo-Latin.woff2 (73636 bytes) U+20B1: yes
- CourierPrime-Regular.woff2 (17128 bytes) U+20B1: no
- CourierPrime-Bold.woff2 (17692 bytes) U+20B1: no

Courier Prime has no peso glyph, so `theme.css` declares a "Resibo Peso" face from
`Archivo-Latin.woff2` with `unicode-range: U+20B1` and lists it first in `--font-mono`. The peso
sign in amounts and in server-written flag messages is drawn from that file.
