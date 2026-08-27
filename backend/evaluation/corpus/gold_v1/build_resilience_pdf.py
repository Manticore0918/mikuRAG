from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

OUTPUT = Path(__file__).parent / "documents" / "resilience-handbook.pdf"
PAGES = (
    (
        "Scope and ownership",
        "The resilience handbook covers the tier-one authentication and checkout services. "
        "The Reliability lead owns recovery coordination, while the Commerce lead validates "
        "checkout integrity before traffic returns.",
    ),
    (
        "Recovery objectives",
        "Tier-one services have a recovery time objective of forty-five minutes and a "
        "recovery point objective of ten minutes. Both objectives are measured from the "
        "declared incident start time.",
    ),
    (
        "Failover sequence",
        "The incident commander must freeze writes, promote the verified replica, and then "
        "rotate service endpoints. Two approvers - Reliability and the affected product lead "
        "- must confirm the promotion before traffic is restored.",
    ),
    (
        "Exercise and evidence",
        "A recovery exercise runs every quarter. Evidence must be archived within thirty "
        "days of the exercise, and corrective actions must be assigned within ten business "
        "days.",
    ),
)


def build() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(OUTPUT), pagesize=A4, invariant=1)
    width, height = A4
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "CorpusTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=HexColor("#16324F"),
        spaceAfter=10,
    )
    body = ParagraphStyle(
        "CorpusBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=12,
        leading=18,
        textColor=HexColor("#243447"),
    )
    for page_number, (heading, text) in enumerate(PAGES, start=1):
        canvas.setFillColor(HexColor("#E8F1F8"))
        canvas.rect(0, height - 24 * mm, width, 24 * mm, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#16324F"))
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(20 * mm, height - 15 * mm, "SYNTHETIC RESILIENCE HANDBOOK")
        story_title = Paragraph(heading, title)
        story_title.wrapOn(canvas, width - 40 * mm, 40 * mm)
        story_title.drawOn(canvas, 20 * mm, height - 55 * mm)
        paragraph = Paragraph(text, body)
        paragraph.wrapOn(canvas, width - 40 * mm, 80 * mm)
        paragraph.drawOn(canvas, 20 * mm, height - 92 * mm)
        canvas.setStrokeColor(HexColor("#8AA6BF"))
        canvas.line(20 * mm, 20 * mm, width - 20 * mm, 20 * mm)
        canvas.setFillColor(HexColor("#4A6073"))
        canvas.setFont("Helvetica", 9)
        canvas.drawString(20 * mm, 13 * mm, "CC0 synthetic evaluation fixture")
        canvas.drawRightString(width - 20 * mm, 13 * mm, f"Page {page_number} of 4")
        canvas.showPage()
    canvas.save()


if __name__ == "__main__":
    build()
