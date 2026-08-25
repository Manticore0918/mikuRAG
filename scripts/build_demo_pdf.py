from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "backend" / "app" / "demo_data" / "v1" / "operations-handbook.pdf"
PAGE_WIDTH, PAGE_HEIGHT = A4
BLUE = HexColor("#2B5C8A")
INK = HexColor("#17202A")
MUTED = HexColor("#5D6D7E")


def _header(canvas: Canvas, title: str, page: int) -> float:
    canvas.setFillColor(BLUE)
    canvas.rect(0, PAGE_HEIGHT - 86, PAGE_WIDTH, 86, fill=1, stroke=0)
    canvas.setFillColorRGB(1, 1, 1)
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(54, PAGE_HEIGHT - 52, title)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(PAGE_WIDTH - 54, 32, f"Baseline demo v1 | page {page}")
    return PAGE_HEIGHT - 126


def _section(canvas: Canvas, y: float, heading: str, lines: list[str]) -> float:
    canvas.setFillColor(BLUE)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(54, y, heading)
    y -= 24
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica", 10.5)
    for line in lines:
        canvas.drawString(66, y, line)
        y -= 17
    return y - 18


def build() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(
        str(OUTPUT),
        pagesize=A4,
        invariant=1,
        pageCompression=1,
    )
    canvas.setTitle("Miku City Operations Handbook")
    canvas.setAuthor("mikuRAG baseline demo")

    y = _header(canvas, "Miku City Operations Handbook", 1)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(54, y, "A compact, redistributable corpus for the mikuRAG baseline demo.")
    y -= 38
    y = _section(
        canvas,
        y,
        "Incident escalation",
        [
            "The incident escalation code is MIKU-4271.",
            "Page the on-call engineer within ten minutes of a severity-one alert.",
            "The incident commander owns the timeline and final resolution note.",
        ],
    )
    _section(
        canvas,
        y,
        "Change control",
        [
            "Production changes require a recorded health-check result.",
            "A failed health check stops the rollout and keeps the previous version active.",
            "Emergency changes still require an incident record and named approver.",
        ],
    )
    canvas.showPage()

    y = _header(canvas, "Privacy and evidence handling", 2)
    y = _section(
        canvas,
        y,
        "Telemetry boundary",
        [
            "Telemetry must never contain Document text, query text, or evidence text.",
            "Credentials and personal data are also prohibited from logs, metrics, and traces.",
            "Use opaque identifiers, counts, durations, and safe error categories instead.",
        ],
    )
    _section(
        canvas,
        y,
        "Citation review",
        [
            "A reviewer must be able to open the cited source and inspect its locator.",
            "An unsupported answer must return an explicit inability to answer reliably.",
            "Authorization filters are applied before ranking candidate evidence.",
        ],
    )
    canvas.save()


if __name__ == "__main__":
    build()
