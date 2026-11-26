#!/usr/bin/env python3
"""
VisionMate AI - HRR-style pseudoisochromatic plate generator
============================================================
Generates 24 original, copyright-free "HRR-inspired" plates (dot mosaics that hide
a circle / cross / triangle) plus an answer key (plates.json).

NOT the real Hardy-Rand-Rittler plates. Screening only, not a medical diagnosis.

Method
  * Figure and ground dot colours are chosen in CIELAB so that, after the published
    Machado et al. (2009) colour-vision-deficiency simulation, they become (almost)
    the same colour for the deficiency being tested ("confusion pair"), while they stay
    clearly different for normal vision.
  * Every dot gets a random brightness (scaling in linear RGB, which keeps its
    chromaticity), so the symbol cannot be found from brightness alone.
  * Each chosen pair is verified numerically and the numbers are saved in plates.json.

Usage:  python3 generate_hrr_style_plates.py [output_dir]
Needs:  numpy, Pillow
"""
import json, os, sys
import numpy as np
from PIL import Image, ImageDraw

OUT = sys.argv[1] if len(sys.argv) > 1 else "hrr_style_plates"
SIZE = 800            # final plate size in px
SS = 2                # supersampling for smooth dots
SEED = 2026

# ------------------------------------------------------------------ colour maths
M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                      [0.2126729, 0.7151522, 0.0721750],
                      [0.0193339, 0.1191920, 0.9503041]])
M_XYZ2RGB = np.linalg.inv(M_RGB2XYZ)
WP = np.array([0.95047, 1.0, 1.08883])

# Machado, Oliveira & Fernandes (2009), severity 1.0, linear RGB
MACHADO = {
    "protan": np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]]),
    "deutan": np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]]),
    "tritan": np.array([[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]]),
}


def lab2lin(L, a, b):
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    f = lambda t: np.where(t ** 3 > 0.008856, t ** 3, (t - 16 / 116) / 7.787)
    xyz = np.stack([f(fx) * WP[0], f(fy) * WP[1], f(fz) * WP[2]], -1)
    return xyz @ M_XYZ2RGB.T


def lin2lab(rgb):
    xyz = rgb @ M_RGB2XYZ.T / WP
    f = lambda t: np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)
    fx, fy, fz = f(xyz[..., 0]), f(xyz[..., 1]), f(xyz[..., 2])
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], -1)


def sim(rgb, kind, severity=1.0):
    m = (1 - severity) * np.eye(3) + severity * MACHADO[kind]
    return rgb @ m.T


def lin2srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def srgb2lin(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def ab_dist(lin1, lin2, kind=None, severity=1.0):
    """Colour difference in the a*b* plane (lightness ignored) - what matters because
    brightness is randomised on every dot. kind=None -> normal vision."""
    if kind:
        lin1, lin2 = sim(lin1, kind, severity), sim(lin2, kind, severity)
    l1 = lin2lab(np.clip(lin1, 1e-4, 1)); l2 = lin2lab(np.clip(lin2, 1e-4, 1))
    return np.hypot(*(l1[..., 1:] - l2[..., 1:]).T)


# ------------------------------------------------------------------ plate plan
# 20 plates.  'confuse' = deficiencies that should NOT see the symbol.
# 'visible'  = deficiencies that should still see it (used to tell types apart).
# 'dn'       = target a*b* difference for normal vision (smaller = harder).
PLAN = (
    [dict(group="demonstration", confuse=[], visible=["protan", "deutan", "tritan"], dn=None, n=1)] * 2 +
    [dict(group="red_green_screening", confuse=["protan", "deutan"], visible=["tritan"], dn=d, n=n)
     for d, n in ((32, 1), (34, 1), (33, 2), (35, 2), (33, 1), (34, 1))] +
    [dict(group="tritan_screening", confuse=["tritan"], visible=["protan", "deutan"], dn=d, n=n)
     for d, n in ((30, 1), (32, 2), (31, 1), (33, 1))] +
    [dict(group="protan_type", confuse=["protan"], visible=["deutan", "tritan"], dn=d, n=n)
     for d, n in ((45, 1), (50, 1), (48, 2), (52, 1))] +
    [dict(group="deutan_type", confuse=["deutan"], visible=["protan", "tritan"], dn=d, n=n)
     for d, n in ((45, 1), (50, 1), (48, 2), (52, 1))] +
    [dict(group="severity_ladder", confuse=["protan", "deutan"], visible=[], dn=d, n=n)
     for d, n in ((32, 1), (26, 1), (21, 1), (16, 1))]
)
assert len(PLAN) == 24
SYMBOLS = ["circle", "cross", "triangle"]


def find_pair(rng, spec, tries=6):
    """Random search for a (figure, ground) colour pair that fits the plate spec."""
    for _ in range(tries):
        N = 300000
        L = 58.0
        f = np.stack([np.full(N, L), rng.uniform(-48, 48, N), rng.uniform(-48, 48, N)], -1)
        g = np.stack([np.full(N, L), rng.uniform(-48, 48, N), rng.uniform(-48, 48, N)], -1)
        fl, gl = lab2lin(*f.T), lab2lin(*g.T)
        ok = (fl.max(1) < 0.85) & (gl.max(1) < 0.85) & (fl.min(1) > 0.02) & (gl.min(1) > 0.02)
        dn = ab_dist(fl, gl)
        for k in spec["confuse"]:
            ok &= ab_dist(fl, gl, k) < 3.5
        for k in spec["visible"]:
            ok &= ab_dist(fl, gl, k) > 14
        lo, hi = spec["dn"] - 2.5, spec["dn"] + 2.5
        ok &= (dn >= lo) & (dn <= hi)
        idx = np.flatnonzero(ok)
        if len(idx):
            i = rng.choice(idx)
            return f[i], g[i]
    raise RuntimeError("no colour pair found for %s" % spec)


def demo_pair(rng):
    """Demonstration plates: visible to everybody (strong hue AND lightness contrast)."""
    for _ in range(100000):
        f = np.array([rng.uniform(30, 40), rng.uniform(-30, 30), rng.uniform(-50, -10)])  # dark blue-ish
        g = np.array([rng.uniform(72, 82), rng.uniform(10, 40), rng.uniform(30, 60)])     # light orange-ish
        fl, gl = lab2lin(*f), lab2lin(*g)
        if max(fl.max(), gl.max()) > 0.85 or min(fl.min(), gl.min()) < 0.02:
            continue
        ok = True
        for k in MACHADO:
            lf, lg = lin2lab(np.clip(sim(fl, k), 1e-4, 1)), lin2lab(np.clip(sim(gl, k), 1e-4, 1))
            ok &= np.linalg.norm(lf - lg) > 30
        if ok:
            return f, g
    raise RuntimeError("no demo pair")


def jitter_colours(lab, rng, n=3, radius=3.0):
    out = []
    for _ in range(n):
        d = rng.normal(size=2); d = d / np.linalg.norm(d) * rng.uniform(0, radius)
        out.append(lab2lin(lab[0], lab[1] + d[0], lab[2] + d[1]))
    return np.array(out)


# ------------------------------------------------------------------ geometry
def symbol_mask(kind, cx, cy, scale):
    S = SIZE * SS
    im = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(im)
    u = SS * scale
    t = 44 * SS                      # stroke thickness (fixed so small symbols stay readable)
    cx, cy = cx * SS, cy * SS
    if kind == "circle":
        r = 105 * u
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=255, width=int(t))
    elif kind == "cross":
        a = 120 * u
        d.rectangle([cx - a, cy - t / 2, cx + a, cy + t / 2], fill=255)
        d.rectangle([cx - t / 2, cy - a, cx + t / 2, cy + a], fill=255)
    else:  # triangle
        r = 135 * u
        pts = [(cx, cy - r), (cx + r * 0.95, cy + r * 0.62), (cx - r * 0.95, cy + r * 0.62)]
        d.line(pts + [pts[0], pts[1]], fill=255, width=int(t), joint="curve")
    return np.array(im.resize((SIZE, SIZE), Image.BILINEAR)) > 127


def pack_dots(rng, R=382, rmax=12.5, rmin=4.2, gap=1.6):
    """Random sequential packing of non-overlapping circles inside a disc."""
    cell = 2 * rmax + gap
    grid = {}
    dots = []
    c = SIZE / 2
    for r in np.arange(rmax, rmin - 0.01, -0.35):
        fails = 0
        while fails < 700:
            ang, rad = rng.uniform(0, 2 * np.pi), (R - r) * np.sqrt(rng.uniform())
            x, y = c + rad * np.cos(ang), c + rad * np.sin(ang)
            gx, gy = int(x // cell), int(y // cell)
            clash = False
            for ix in (gx - 1, gx, gx + 1):
                for iy in (gy - 1, gy, gy + 1):
                    for (px, py, pr) in grid.get((ix, iy), ()):
                        if (px - x) ** 2 + (py - y) ** 2 < (pr + r + gap) ** 2:
                            clash = True; break
                    if clash: break
                if clash: break
            if clash:
                fails += 1
            else:
                grid.setdefault((gx, gy), []).append((x, y, r))
                dots.append((x, y, r))
    return dots


def render(dots, mask, f_cols, g_cols, rng, bright):
    big = SIZE * SS
    im = Image.new("RGB", (big, big), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for (x, y, r) in dots:
        inside = mask[min(SIZE - 1, int(y)), min(SIZE - 1, int(x))]
        base = f_cols[rng.integers(len(f_cols))] if inside else g_cols[rng.integers(len(g_cols))]
        s = rng.uniform(*bright)
        rgb = (lin2srgb(base * s) * 255).round().astype(int)
        d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=tuple(rgb))
    return im.resize((SIZE, SIZE), Image.LANCZOS)


# ------------------------------------------------------------------ main
def main():
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(SEED)
    meta = []
    last_sym = None
    for pid, spec in enumerate(PLAN, 1):
        prng = np.random.default_rng(SEED + pid)
        demo = spec["group"] == "demonstration"
        f_lab, g_lab = demo_pair(prng) if demo else find_pair(prng, spec)
        if prng.random() < 0.5:                     # randomly swap which is figure / ground
            pass
        f_cols, g_cols = jitter_colours(f_lab, prng), jitter_colours(g_lab, prng)
        # symbols
        n = spec["n"]
        syms = []
        while len(syms) < n:
            s = SYMBOLS[prng.integers(3)]
            if s not in syms and s != last_sym:
                syms.append(s)
        last_sym = syms[-1]
        mask = np.zeros((SIZE, SIZE), bool)
        if n == 1:
            mask |= symbol_mask(syms[0], 400, 400, 1.25)
        else:
            mask |= symbol_mask(syms[0], 228, 400, 0.9)
            mask |= symbol_mask(syms[1], 572, 400, 0.9)
        dots = pack_dots(prng)
        bright = (0.92, 1.08) if demo else (0.62, 1.15)
        img = render(dots, mask, f_cols, g_cols, prng, bright)
        fn = "plate_%02d.png" % pid
        img.save(os.path.join(OUT, fn))
        # verification numbers (mean figure vs mean ground colour)
        fm, gm = f_cols.mean(0), g_cols.mean(0)
        ver = {"normal_vision_ab_diff": round(float(ab_dist(fm, gm)), 1)}
        for k in MACHADO:
            ver["%s_sim_ab_diff" % k] = round(float(ab_dist(fm, gm, k)), 1)
        for k in ("protan", "deutan"):
            ver["%s_sim_severity50_ab_diff" % k] = round(float(ab_dist(fm, gm, k, 0.5)), 1)
        meta.append(dict(plate=pid, file=fn, group=spec["group"], answer=syms, n_dots=len(dots), n_symbols=n,
                         seen_by_normal=True,
                         hidden_for=spec["confuse"], visible_for=spec["visible"] if not demo else ["all"],
                         verification=ver))
        print(fn, spec["group"], syms, ver)
    key = dict(
        title="VisionMate AI HRR-style plate set (original, not the real HRR plates)",
        disclaimer="Indicative screening only - not a medical diagnosis.",
        answer_options=SYMBOLS + ["none"],
        scoring_hint={
            "normal": "all plates answered correctly",
            "red_green": "errors on red_green_screening plates -> red-green deficiency suspected",
            "protan_vs_deutan": "more errors on protan_type plates -> protan; more on deutan_type -> deutan",
            "tritan": "errors on tritan_screening plates -> tritan suspected",
            "severity": "severity_ladder plates get harder; count how many are answered correctly (4 = mild/none ... 0 = strong)",
        },
        plates=meta)
    json.dump(key, open(os.path.join(OUT, "plates.json"), "w"), indent=2)

    # contact sheet
    th = 240
    sheet = Image.new("RGB", (6 * th, 4 * th), "white")
    for i, m in enumerate(meta):
        im = Image.open(os.path.join(OUT, m["file"])).resize((th, th), Image.LANCZOS)
        sheet.paste(im, ((i % 6) * th, (i // 6) * th))
    sheet.save(os.path.join(OUT, "contact_sheet.png"))


if __name__ == "__main__":
    main()
