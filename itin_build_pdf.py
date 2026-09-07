"""
Master itinerary PDF generator.
Single source of truth is data.py (PRETRIP + DAYS) — this script renders it into
master_itinerary.pdf. Run: python3 build_pdf.py
Regenerate whenever data.py changes so the PDF and the live HTML page never drift.
"""
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from data import PRETRIP, DAYS

NAVY = colors.HexColor("#1a3a5c")
GOLD = colors.HexColor("#b8860b")
MAROON = colors.HexColor("#8c2f39")
PINK_RED = colors.HexColor("#c0435a")
GRAY = colors.HexColor("#666666")
LIGHTGRAY = colors.HexColor("#999999")

STATUS_COLORS = {
    "BOOKED": MAROON,
    "BOOKED / PAID": MAROON,
    "PAID": MAROON,
    "TODO": PINK_RED,
    "OPTIONAL": GOLD,
}

styles = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=20, textColor=NAVY, spaceAfter=10, leading=24),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=10, textColor=GRAY, spaceAfter=14),
    "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=12, textColor=GOLD, spaceBefore=6, spaceAfter=8),
    "checkdate": ParagraphStyle("checkdate", fontName="Helvetica-Bold", fontSize=9.5, textColor=MAROON, spaceBefore=8, spaceAfter=1),
    "checkdetail": ParagraphStyle("checkdetail", fontName="Helvetica", fontSize=9, textColor=GRAY, spaceAfter=4, leading=12),
    "daytitle": ParagraphStyle("daytitle", fontName="Helvetica-Bold", fontSize=11, textColor=colors.white, leftIndent=6),
    "itemtitle": ParagraphStyle("itemtitle", fontName="Helvetica-Bold", fontSize=10, textColor=NAVY, spaceBefore=7, spaceAfter=1, leading=13),
    "itemsub": ParagraphStyle("itemsub", fontName="Helvetica", fontSize=8.5, textColor=GRAY, spaceAfter=2, leading=11, leftIndent=10),
}


def status_tag(status):
    if not status or status == "—":
        return ""
    color = STATUS_COLORS.get(status, LIGHTGRAY)
    return f'  <font color="{color.hexval()}">[{status}]</font>'


def build():
    doc = SimpleDocTemplate(
        "master_itinerary.pdf", pagesize=LETTER,
        topMargin=0.55 * inch, bottomMargin=0.55 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )
    story = []

    story.append(Paragraph("Conrad Family Trip — Master Itinerary", styles["title"]))
    story.append(Paragraph("London &amp; Scotland &middot; September 10&ndash;21, 2026", styles["subtitle"]))

    story.append(Paragraph("CHECKLIST", styles["section"]))
    for item in PRETRIP:
        story.append(Paragraph(f'{item["deadline"]} &mdash; <b>{item["action"]}</b>', styles["checkdate"]))
        if item.get("detail"):
            story.append(Paragraph(item["detail"], styles["checkdetail"]))

    story.append(Spacer(1, 6))
    story.append(Paragraph("DAY BY DAY", styles["section"]))

    for day in DAYS:
        bar = Table([[Paragraph(day["date"], styles["daytitle"])]], colWidths=[7.1 * inch])
        bar.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(Spacer(1, 8))
        story.append(bar)

        for it in day["items"]:
            title_line = f'{it["time"]} &mdash; <b>{it["title"]}</b>{status_tag(it.get("status"))}'
            story.append(Paragraph(title_line, styles["itemtitle"]))
            sub_parts = []
            if it.get("address") and it["address"] != "—":
                sub_parts.append(it["address"])
            if it.get("conf"):
                sub_parts.append(f'<i>{it["conf"]}</i>')
            if it.get("detail"):
                sub_parts.append(it["detail"])
            if sub_parts:
                story.append(Paragraph(" &middot; ".join(sub_parts), styles["itemsub"]))

    doc.build(story)
    print("Built master_itinerary.pdf")


if __name__ == "__main__":
    build()
