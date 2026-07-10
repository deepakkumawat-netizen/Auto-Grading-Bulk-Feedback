"""Per-student feedback PDF generation using reportlab."""
from __future__ import annotations

import io
import os
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# Setup Font Family supporting Devanagari/Hindi and Latin characters
_FONT_NAME = "Helvetica"
_FONT_BOLD_NAME = "Helvetica-Bold"

def _setup_fonts():
    global _FONT_NAME, _FONT_BOLD_NAME
    # Standard paths to search for Nirmala UI (Windows standard Indic font)
    paths_to_try = [
        ("C:\\Windows\\Fonts\\Nirmala.ttf", "C:\\Windows\\Fonts\\NirmalaB.ttf"),
        ("C:\\winnt\\fonts\\Nirmala.ttf", "C:\\winnt\\fonts\\NirmalaB.ttf"),
    ]
    # Check standard Linux font paths just in case
    linux_paths = [
        ("/usr/share/fonts/truetype/nirmala/Nirmala.ttf", "/usr/share/fonts/truetype/nirmala/NirmalaB.ttf"),
        ("/usr/share/fonts/Nirmala.ttf", "/usr/share/fonts/NirmalaB.ttf"),
    ]
    paths_to_try.extend(linux_paths)
    
    for norm_p, bold_p in paths_to_try:
        if os.path.exists(norm_p) and os.path.exists(bold_p):
            try:
                pdfmetrics.registerFont(TTFont("Nirmala", norm_p))
                pdfmetrics.registerFont(TTFont("Nirmala-Bold", bold_p))
                registerFontFamily("Nirmala", normal="Nirmala", bold="Nirmala-Bold")
                _FONT_NAME = "Nirmala"
                _FONT_BOLD_NAME = "Nirmala-Bold"
                return
            except Exception as e:
                print(f"[PDF] Failed to register Nirmala font family from {norm_p}: {e}")

_setup_fonts()

_styles = getSampleStyleSheet()

# Premium & Compact Typography using dynamic font family
_H1 = ParagraphStyle(
    "h1", parent=_styles["Heading1"],
    fontName=_FONT_BOLD_NAME,
    fontSize=14, leading=18, textColor=colors.HexColor("#1e293b"),
    spaceBefore=0, spaceAfter=2
)
_H2 = ParagraphStyle(
    "h2", parent=_styles["Heading2"],
    fontName=_FONT_BOLD_NAME,
    fontSize=10, leading=13, textColor=colors.HexColor("#0f172a"),
    spaceBefore=5, spaceAfter=3
)
_BODY = ParagraphStyle(
    "body", parent=_styles["BodyText"],
    fontName=_FONT_NAME,
    fontSize=8.5, leading=11, textColor=colors.HexColor("#334155")
)
_MUTED = ParagraphStyle(
    "muted", parent=_BODY,
    fontName=_FONT_NAME,
    fontSize=7.5, leading=10, textColor=colors.HexColor("#64748b")
)
_Q_STYLE = ParagraphStyle(
    "q_style", parent=_BODY,
    fontName=_FONT_NAME,
    fontSize=8.5, leading=11, textColor=colors.HexColor("#1e293b")
)

def build_feedback_pdf(result: dict[str, Any], meta: dict[str, Any]) -> bytes:
    """Render one student's grading result as a styled, compact A4 PDF with full Unicode/Indic support."""
    buf = io.BytesIO()
    # 10mm margins expand printable width to 190mm and printable height to 277mm
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=10 * mm, bottomMargin=10 * mm,
    )
    story: list = []

    student = result.get("student_name") or meta.get("file") or "Student"
    grade   = meta.get("grade", "")
    subject = meta.get("subject", "")
    chapter = meta.get("chapter", "")

    # Clean Header Title
    story.append(Paragraph(f"Auto-Grading Feedback Report", _H1))
    story.append(Spacer(1, 2))
    
    def _fmt(val: Any) -> str:
        if val is None or val == "-":
            return "-"
        try:
            f_val = float(val)
            return str(int(f_val)) if f_val.is_integer() else str(f_val)
        except Exception:
            return str(val)

    marks    = result.get("marks_awarded", 0)
    total    = result.get("marks_total", 0)
    pct      = result.get("percentage", 0)

    # Metadata Grid Table (Width: 95mm + 95mm = 190mm)
    meta_data = [
        [
            Paragraph(f"<b>Student Name:</b> {student}", _BODY),
            Paragraph(f"<b>Subject:</b> {subject} (Grade {grade})", _BODY)
        ],
        [
            Paragraph(f"<b>Topic / Chapter:</b> {chapter or 'N/A'}", _BODY),
            Paragraph(f"<b>Overall Score:</b> <font color='#1e293b'><b>{_fmt(marks)}/{_fmt(total)} ({pct}%)</b></font>", _BODY)
        ]
    ]
    meta_table = Table(meta_data, colWidths=[95*mm, 95*mm])
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#f1f5f9")),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 5))

    # Question-Wise Marks Breakdown (Width: 25mm + 20mm + 145mm = 190mm)
    per_q = result.get("per_question") or []
    if per_q:
        story.append(Paragraph("Question-Wise Marks Breakdown", _H2))
        data = [["Question", "Marks", "Feedback"]]
        for q in per_q:
            fb = str(q.get("feedback", ""))
            if len(fb) > 800:
                fb = fb[:797] + "..."
            data.append([
                Paragraph(str(q.get("q", "")), _Q_STYLE),
                f"{_fmt(q.get('marks_awarded'))}/{_fmt(q.get('marks_total'))}",
                Paragraph(fb, _BODY),
            ])
        
        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), _FONT_BOLD_NAME),
            ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
            ("ALIGN",      (1, 0), (1, -1), "CENTER"),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BOX",        (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("INNERGRID",  (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ]
        
        for idx, q in enumerate(per_q):
            row_idx = idx + 1
            try:
                awarded = float(q.get("marks_awarded", 0))
                total_q = float(q.get("marks_total", 0))
                if total_q > 0:
                    if awarded >= total_q:
                        t_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#f0fdf4")))
                        t_style.append(("TEXTCOLOR", (1, row_idx), (1, row_idx), colors.HexColor("#166534")))
                    elif awarded == 0:
                        t_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#fef2f2")))
                        t_style.append(("TEXTCOLOR", (1, row_idx), (1, row_idx), colors.HexColor("#991b1b")))
                    else:
                        t_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#fff7ed")))
                        t_style.append(("TEXTCOLOR", (1, row_idx), (1, row_idx), colors.HexColor("#9a3412")))
            except Exception:
                bg = colors.white if row_idx % 2 == 1 else colors.HexColor("#f8fafc")
                t_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))

        table = Table(data, colWidths=[25*mm, 20*mm, 145*mm], repeatRows=1)
        table.setStyle(TableStyle(t_style))
        story.append(table)
        story.append(Spacer(1, 4))

    # Sequential Key Strengths & Areas to Improve (prevents LayoutError by allowing page split)
    strengths = result.get("strengths") or []
    if strengths:
        story.append(Paragraph("✓ Key Strengths", _H2))
        for s in strengths:
            story.append(Paragraph(f"• {s}", _BODY))
        story.append(Spacer(1, 3))

    mistakes = result.get("mistakes") or []
    if mistakes:
        story.append(Paragraph("✗ Areas to Improve", _H2))
        for m in mistakes:
            m_type = m.get("type", "")
            desc = m.get("description", "")
            if m_type:
                story.append(Paragraph(f"• <b>{m_type}</b>: {desc}", _BODY))
            else:
                story.append(Paragraph(f"• {desc}", _BODY))
        story.append(Spacer(1, 3))

    suggestion = result.get("suggestion")
    if suggestion:
        story.append(Paragraph("💡 Personalized Suggestion & Next Steps", _H2))
        story.append(Paragraph(str(suggestion), _BODY))
        story.append(Spacer(1, 4))

    verifier = result.get("verifier")
    if verifier:
        story.append(Paragraph("🔍 AI Verifier Audit", _H2))
        agree = verifier.get("agrees")
        conf  = verifier.get("confidence", "")
        comment = verifier.get("comment", "")
        status_text = "Reviewer agrees with the score" if agree else "Reviewer suggests adjustments"
        story.append(Paragraph(
            f"<b>Status:</b> {status_text} (confidence: {conf}%).<br/>"
            f"<b>Details:</b> {comment}", _BODY))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<i>Generated by Auto-Grading & Bulk Feedback System. Review and verify scores before sharing.</i>",
        _MUTED))

    doc.build(story)
    return buf.getvalue()
