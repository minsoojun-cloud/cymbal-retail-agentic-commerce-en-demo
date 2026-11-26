"""Generate the fictional Cymbal sample documents used by "Extract from Documents".

    python scripts/make_samples.py [--font /path/to/Regular.ttf --bold-font /path/to/Bold.ttf]

Outputs (samples/docs/):
  spec_cymbal_frypan_20cm.pdf      Supplier spec sheet (frying pan 20 cm, JAN 4954950777214)
  price_list_2026winter_nabe.xlsx  Winter Hot Pot Fair price list (4 existing JANs + 1 new item)
  price_list_2026winter_nabe.csv   Same price list as CSV (UTF-8)
  label_cymbal_carbonara.png       Food label photo (frozen carbonara, nutrition facts + allergen box)
All values are fictional demo data. Prices are JPY, tax included.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples" / "docs"

PRICE_HEADER = ["JAN", "Product name", "Net contents", "Origin", "Storage", "In season", "Recommended hot pot",
                "Price (JPY, tax incl.)"]
PRICE_ROWS = [
    ["4913667472959", "Ibaraki Napa Cabbage, 1/2 cut", "1/2 cut", "Ibaraki, Japan", "Chilled", "Nov-Feb",
     "Yosenabe, kimchi hot pot, mizutaki", 198],
    ["4958688424846", "Miyagi Shucked Oysters, Raw-Eating Grade, 150g", "150 g", "Miyagi, Japan",
     "Chilled (10°C or below)", "Nov-Feb", "Dote-nabe, oyster hot pot", 798],
    ["4922835139112", "Hokkaido Boiled Horsehair Crab, 2 crabs (about 800g)", "approx. 800 g", "Hokkaido, Japan",
     "Frozen (-18°C or below)", "Dec-Mar", "Crab hot pot", 6980],
    ["4978886449342", "Domestic Bunashimeji Mushrooms, 3 packs (about 300g)", "3 packs (approx. 300 g)", "Japan",
     "Chilled", "Sep-Dec", "Yosenabe, sukiyaki", 258],
    ["4990000000017", "Cymbal Hot Pot Broth, Soy Sauce Yosenabe 750g (serves 3-4)", "750 g", "Made in Japan",
     "Ambient", "", "Yosenabe", 298],
]


def find_font(bold: bool = False) -> str:
    w = "Bold" if bold else "Regular"
    pats = [f"/usr/share/fonts/**/Roboto-{w}.ttf", f"/usr/share/fonts/**/NotoSans-{w}.ttf",
            f"/usr/share/fonts/**/LiberationSans-{w}.ttf", f"~/.local/share/fonts/**/*Sans-{w}.ttf",
            "/usr/share/fonts/**/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
            "/System/Library/Fonts/Supplemental/Arial%s.ttf" % (" Bold" if bold else "")]
    for p in pats:
        hits = sorted(glob.glob(os.path.expanduser(p), recursive=True))
        if hits:
            return hits[0]
    raise SystemExit("No Latin TrueType font found; pass --font / --bold-font")


def make_pdf(path: Path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    F, FB = "Helvetica", "Helvetica-Bold"
    h1 = ParagraphStyle("h1", fontName=FB, fontSize=18, leading=24, textColor=colors.HexColor("#1a73e8"))
    body = ParagraphStyle("b", fontName=F, fontSize=10, leading=15)
    small = ParagraphStyle("s", fontName=F, fontSize=8, leading=12, textColor=colors.HexColor("#5f6368"))
    cell = ParagraphStyle("c", fontName=F, fontSize=9.5, leading=13)
    cellb = ParagraphStyle("cb", fontName=FB, fontSize=9.5, leading=13)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            title="Product Specification - Cymbal Basic Non-Stick Frying Pan 20 cm",
                            author="Cymbal Retail (fictional demo)")
    rows = [["Item", "Specification"],
            ["Product name", "Cymbal Basic Non-Stick Frying Pan 20 cm"],
            ["JAN code", "4954950777214"],
            ["Item number", "CB-FP20"],
            ["Dimensions", "Diameter 20 cm x depth 5.0 cm (overall length 37.5 cm)"],
            ["Weight", "Approx. 480 g"],
            ["Material", "Body: aluminum alloy / Interior: fluororesin (PTFE) non-stick coating / "
                         "Exterior: baked enamel / Handle: phenolic resin"],
            ["Heat sources", "Induction compatible (100 V / 200 V), gas compatible"],
            ["Dishwasher", "Not dishwasher safe (hand wash only)"],
            ["Microwave / oven", "Not suitable"],
            ["Country of origin", "Made in China"],
            ["Distributor", "Cymbal Retail Co., Ltd. (fictional)"]]
    rows = [[Paragraph(a, cellb if i == 0 else cell), Paragraph(b, cellb if i == 0 else cell)]
            for i, (a, b) in enumerate(rows)]
    t = Table(rows, colWidths=[42 * mm, 132 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f0fe")),
                           ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f8f9fa")),
                           ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dadce0")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story = [Paragraph("Product Specification Sheet", h1),
             Paragraph("Cymbal Basic Non-Stick Frying Pan 20 cm", body), Spacer(1, 6 * mm),
             Paragraph("A 20 cm pan that is just right for everyday fried eggs and small stir-fries. The fluororesin "
                       "non-stick coating resists sticking, so you can cook with less oil.", body),
             Spacer(1, 5 * mm), t, Spacer(1, 6 * mm),
             Paragraph("Precautions: Heating the pan empty or using it on high heat will degrade the non-stick coating. "
                       "When using on induction, use a cooktop that supports a pan base diameter of 12 cm or more.", body),
             Spacer(1, 10 * mm), Paragraph("This document contains fictional data for demo purposes (Cymbal Retail demo).",
                                           small)]
    doc.build(story)


def make_xlsx(path: Path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Winter Hot Pot Fair"
    ws.append(["2026 Winter Hot Pot Fair - Price List (Dec 1 - Jan 15)"])
    ws.append([])
    ws.append(PRICE_HEADER)
    for r in PRICE_ROWS:
        ws.append(r)
    ws["A1"].font = Font(bold=True, size=14, color="1A73E8")
    for c in ws[3]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E8F0FE")
        c.alignment = Alignment(horizontal="center", wrap_text=True)
    for row in ws.iter_rows(min_row=4, max_row=3 + len(PRICE_ROWS), min_col=8, max_col=8):
        for c in row:
            c.number_format = '"¥"#,##0'
    for col, w in zip("ABCDEFGH", (16, 56, 22, 16, 22, 11, 32, 14)):
        ws.column_dimensions[col].width = w
    ws.append([])
    ws.append(["Fictional demo data. New JAN 4990000000017 is not in the catalog yet (example of a new product)."])
    wb.save(path)


def make_csv(path: Path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(PRICE_HEADER)
        w.writerows(PRICE_ROWS)


def make_label(path: Path, font: str, bold: str):
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1100, 1360
    im = Image.new("RGB", (W, H), (250, 248, 242))
    d = ImageDraw.Draw(im)
    f = lambda s, b=False: ImageFont.truetype(bold if b else font, s)  # noqa: E731
    ink, grey = (32, 33, 36), (95, 99, 104)
    # package-ish header band
    d.rectangle([0, 0, W, 200], fill=(26, 115, 232))
    d.text((50, 36), "Cymbal Basic", font=f(36, True), fill="white")
    d.text((50, 92), "Frozen Carbonara 260 g", font=f(54, True), fill="white")
    d.text((W - 330, 150), "JAN 4911478160614", font=f(24), fill=(232, 240, 254))
    # product information table
    x0, x1 = 50, W - 50
    rows = [("Name", "Frozen pasta (spaghetti)"),
            ("Ingredients", "Noodles (durum wheat semolina, salt), sauce (cream, bacon,\negg yolk, cheese, "
                            "vegetable oil, salt, black pepper) /\nthickener (modified starch), seasoning (amino "
                            "acids),\nemulsifier (contains wheat, egg, milk, pork)"),
            ("Net weight", "260 g"),
            ("Best before", "See bottom right of package (12 months from manufacture)"),
            ("Storage", "Keep frozen at -18°C or below"),
            ("Preparation", "Microwave 600 W approx. 5 min 30 s / 500 W approx. 6 min 30 s"),
            ("Manufacturer", "Cymbal Retail Co., Ltd. (fictional), Minato-ku, Tokyo")]
    y = 240
    for k, v in rows:
        lines = v.split("\n")
        h = 18 + 34 * len(lines)
        d.rectangle([x0, y, x1, y + h], outline=(60, 64, 67), width=2)
        d.line([x0 + 220, y, x0 + 220, y + h], fill=(60, 64, 67), width=2)
        d.text((x0 + 16, y + 12), k, font=f(25, True), fill=ink)
        for i, ln in enumerate(lines):
            d.text((x0 + 236, y + 12 + i * 34), ln, font=f(23), fill=ink)
        y += h
    # nutrition facts
    y += 30
    nut = [("Energy", "538 kcal"), ("Protein", "19.8 g"), ("Fat", "24.6 g"), ("Carbohydrate", "59.2 g"),
           ("Salt equivalent", "3.1 g")]
    nh = 70 + 38 * len(nut)
    d.rectangle([x0, y, x0 + 560, y + nh], outline=ink, width=3)
    d.text((x0 + 16, y + 10), "Nutrition Facts (per pack, 260 g)", font=f(26, True), fill=ink)
    d.line([x0, y + 54, x0 + 560, y + 54], fill=ink, width=3)
    for i, (k, v) in enumerate(nut):
        yy = y + 64 + i * 38
        d.text((x0 + 16, yy), k, font=f(24), fill=ink)
        d.text((x0 + 400, yy), v, font=f(24, True), fill=ink)
    y += nh + 40
    # allergen box
    d.rectangle([x0, y, x1, y + 200], outline=(217, 48, 37), width=5)
    d.rectangle([x0, y, x1, y + 58], fill=(217, 48, 37))
    d.text((x0 + 20, y + 10), "ALLERGEN INFORMATION", font=f(32, True), fill="white")
    d.text((x0 + 24, y + 80), "Allergens: wheat, egg, milk, pork", font=f(40, True), fill=ink)
    d.text((x0 + 24, y + 140), "Produced in a facility that also processes shrimp and crab.", font=f(25), fill=grey)
    d.text((x0, H - 60), "Fictional label for demo purposes (Cymbal Retail demo)", font=f(22), fill=(128, 134, 139))
    im = im.rotate(1.2, expand=True, fillcolor=(235, 235, 235))   # slightly tilted like a photo
    im.save(path, "PNG", optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font")
    ap.add_argument("--bold-font")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    font = a.font or find_font(False)
    bold = a.bold_font or (find_font(True) if not a.font else a.font)
    make_pdf(OUT / "spec_cymbal_frypan_20cm.pdf")
    make_xlsx(OUT / "price_list_2026winter_nabe.xlsx")
    make_csv(OUT / "price_list_2026winter_nabe.csv")
    make_label(OUT / "label_cymbal_carbonara.png", font, bold)
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main()
