"""Lead export (simplified workflow): EVERY lead matching the Leads page's current search / filters / sort - not one page of
it - as an Excel workbook or a printable PDF, in Arabic (RTL) or English (LTR).

  * WHO / WHICH. sales.leads.export (Sales Manager; Super Admin) and the caller's own lead scope - the same predicate and
    the same filters as GET /leads (repositories/leads._conditions), so an export can never hold a lead the caller could not
    list. At most EXPORT_MAX_ROWS rows: past that the request answers 413 and asks for narrower filters.
  * WHAT. An explicit column list of what the Leads page already shows a manager. Never the normalized duplicate-detection
    columns, never notes / descriptions (free text), never anything about accounts beyond a person's display name.
  * EXCEL. One row per lead, one typed column per field (real Excel dates in the application timezone, a numeric value
    column), header row frozen with an autofilter, no merged cells - ready for a pivot table. A second sheet records what was
    exported (when, by whom, the filters, the row count). Every text cell is written as TEXT: a business name such as
    "=HYPERLINK(...)" stays a string, never a formula (spreadsheet formula injection).
  * PDF. A4 landscape, a repeated header row, page numbers, the filters on the first page. Arabic is shaped
    (arabic_reshaper) and put in visual order (python-bidi) line by line AFTER wrapping, so a wrapped Arabic cell still reads
    top-to-bottom; in Arabic the column order and the alignment are mirrored. The font is IBM Plex Sans Arabic (OFL, bundled
    in sales/assets/fonts - it carries the Latin glyphs too), embedded as a subset.
  * AUDITED. Every export writes a `leads_exported` staff audit event (format, language, row count, filters).
"""
import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from sales.constants import EXPORT_MAX_ROWS
from sales.permissions import PLATFORM_ROLE_STAFF
from sales.repositories import campaigns as campaigns_repo
from sales.repositories import leads as leads_repo
from sales.repositories.leads import LeadFilters, LeadRow
from sales.services import audit as audit_service
from sales.services.access import StaffContext
from sales.services.audit import AuditContext
from sales.services.common import field_error
from sales.services.lead_access import lead_visibility
from sales.timezone import APP_TZ, APP_TZ_NAME

logger = logging.getLogger("sales.export")

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_REGULAR = "JazPlexArabic"
FONT_BOLD = "JazPlexArabic-Bold"

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}

# ---- labels (the same wording as the Sales UI: frontend/src/utils/salesLeadsTranslations.js) ------------------------
_STAGES = {
    "ar": {
        "new": "جديد", "assigned": "تم التعيين", "contacted": "تم التواصل", "interested": "مهتم",
        "demo_scheduled": "عرض تجريبي مجدول", "demo_completed": "تم العرض التجريبي", "trial": "فترة تجريبية",
        "negotiation": "تفاوض", "won": "تم الفوز", "lost": "خسارة",
    },
    "en": {
        "new": "New", "assigned": "Assigned", "contacted": "Contacted", "interested": "Interested",
        "demo_scheduled": "Demo scheduled", "demo_completed": "Demo completed", "trial": "Trial",
        "negotiation": "Negotiation", "won": "Won", "lost": "Lost",
    },
}
_PRIORITIES = {"ar": {"low": "منخفضة", "medium": "متوسطة", "high": "عالية"}, "en": {"low": "Low", "medium": "Medium", "high": "High"}}
_LOST_REASONS = {
    "ar": {
        "too_expensive": "السعر مرتفع", "not_interested": "غير مهتم", "already_using_another_system": "يستخدم نظاماً آخر",
        "no_response": "لا يوجد رد", "wrong_number": "رقم خاطئ", "business_closed": "النشاط مغلق", "not_suitable": "غير مناسب",
        "delayed_decision": "تأجيل القرار", "competitor": "اختار منافساً", "other": "سبب آخر",
    },
    "en": {
        "too_expensive": "Too expensive", "not_interested": "Not interested",
        "already_using_another_system": "Already using another system", "no_response": "No response",
        "wrong_number": "Wrong number", "business_closed": "Business closed", "not_suitable": "Not suitable",
        "delayed_decision": "Delayed decision", "competitor": "Chose a competitor", "other": "Other",
    },
}
_TEXT = {
    "ar": {
        "title": "العملاء المحتملون", "sheet": "العملاء المحتملون", "info_sheet": "معلومات التصدير",
        "exported_at": "وقت التصدير", "exported_by": "بواسطة", "rows": "عدد العملاء المحتملين", "timezone": "المنطقة الزمنية",
        "filters": "عوامل التصفية", "no_filters": "بدون تصفية - جميع العملاء المحتملين النشطين", "page": "صفحة",
        "yes": "نعم", "no": "لا", "unassigned": "غير معيّن", "none": "-", "all": "الكل",
        "f_q": "بحث", "f_stages": "المرحلة", "f_assigned": "مُعيّن إلى", "f_source": "المصدر", "f_campaign": "الحملة",
        "f_priority": "الأولوية", "f_created_from": "من تاريخ", "f_created_to": "إلى تاريخ", "f_archived": "المؤرشفة",
        "archived_only": "المؤرشفة فقط", "archived_all": "النشطة والمؤرشفة", "f_sort": "الترتيب",
        "me": "أنا", "asc": "تصاعدي", "desc": "تنازلي",
    },
    "en": {
        "title": "Leads", "sheet": "Leads", "info_sheet": "Export info",
        "exported_at": "Exported at", "exported_by": "Exported by", "rows": "Leads", "timezone": "Time zone",
        "filters": "Filters", "no_filters": "No filters - every active lead", "page": "Page",
        "yes": "Yes", "no": "No", "unassigned": "Unassigned", "none": "-", "all": "All",
        "f_q": "Search", "f_stages": "Stage", "f_assigned": "Assigned to", "f_source": "Source", "f_campaign": "Campaign",
        "f_priority": "Priority", "f_created_from": "Created from", "f_created_to": "Created to", "f_archived": "Archived",
        "archived_only": "Archived only", "archived_all": "Active and archived", "f_sort": "Sort",
        "me": "Me", "asc": "ascending", "desc": "descending",
    },
}
_SORTS = {
    "ar": {"created_at": "تاريخ الإنشاء", "updated_at": "آخر تحديث", "business_name": "اسم النشاط", "priority": "الأولوية",
           "estimated_value": "القيمة المتوقعة", "pipeline_stage": "المرحلة"},
    "en": {"created_at": "Created date", "updated_at": "Updated date", "business_name": "Business name", "priority": "Priority",
           "estimated_value": "Estimated value", "pipeline_stage": "Stage"},
}


# ---- the columns -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Column:
    key: str
    en: str
    ar: str
    kind: str = "text"            # text | datetime | number
    xlsx_width: float = 18
    pdf_weight: float = 0         # 0 = not in the PDF (the PDF is a printable subset; Excel has every column)


COLUMNS: Tuple[Column, ...] = (
    Column("id", "Lead ID", "معرّف العميل المحتمل", xlsx_width=38, pdf_weight=0.75),
    Column("business_name", "Company", "الشركة", xlsx_width=30, pdf_weight=1.6),
    Column("business_type", "Business type", "نوع النشاط", xlsx_width=18),
    Column("contact_name", "Contact", "جهة الاتصال", xlsx_width=22, pdf_weight=1.2),
    Column("contact_position", "Contact position", "المسمى الوظيفي", xlsx_width=18),
    Column("phone", "Phone", "الهاتف", xlsx_width=18, pdf_weight=1.05),
    Column("whatsapp", "WhatsApp", "واتساب", xlsx_width=18),
    Column("email", "Email", "البريد الإلكتروني", xlsx_width=28),
    Column("city", "City", "المدينة", xlsx_width=16, pdf_weight=0.8),
    Column("country", "Country", "الدولة", xlsx_width=14),
    Column("source", "Source", "المصدر", xlsx_width=16, pdf_weight=0.85),
    Column("campaign", "Campaign", "الحملة", xlsx_width=22, pdf_weight=1.0),
    Column("assigned_to", "Assigned employee", "الموظف المسؤول", xlsx_width=22, pdf_weight=1.1),
    Column("stage", "Stage / Status", "المرحلة / الحالة", xlsx_width=18, pdf_weight=0.95),
    Column("lost_reason", "Lost reason", "سبب الخسارة", xlsx_width=22),
    Column("priority", "Priority", "الأولوية", xlsx_width=11),
    Column("estimated_value", "Estimated value", "القيمة المتوقعة", kind="number", xlsx_width=15),
    Column("created_by", "Created by", "أنشأه", xlsx_width=20),
    Column("created_at", "Created date", "تاريخ الإنشاء", kind="datetime", xlsx_width=18, pdf_weight=0.8),
    Column("updated_at", "Updated date", "آخر تحديث", kind="datetime", xlsx_width=18, pdf_weight=0.8),
    Column("archived", "Archived", "مؤرشف", xlsx_width=10),
)
PDF_COLUMNS: Tuple[Column, ...] = tuple(c for c in COLUMNS if c.pdf_weight)


def _local(value: Optional[datetime]) -> Optional[datetime]:
    """A stored UTC timestamp as the application's wall-clock time (naive: Excel has no time zones)."""
    return value.astimezone(APP_TZ).replace(tzinfo=None) if value is not None else None


def lead_record(row: LeadRow, lang: str, source_names: Dict[str, str]) -> Dict[str, object]:
    """One lead as {column key: value} - the explicit column list, localized."""
    lead, t = row.lead, _TEXT[lang]
    return {
        "id": str(lead.id),
        "business_name": lead.business_name,
        "business_type": lead.business_type,
        "contact_name": lead.contact_name,
        "contact_position": lead.contact_position,
        "phone": lead.phone,
        "whatsapp": lead.whatsapp,
        "email": lead.email,
        "city": lead.city,
        "country": lead.country,
        "source": source_names.get(lead.source, lead.source),
        "campaign": row.campaign_name,
        "assigned_to": row.assignee_name if lead.assigned_to else t["unassigned"],
        "stage": _STAGES[lang].get(lead.pipeline_stage, lead.pipeline_stage),
        "lost_reason": _LOST_REASONS[lang].get(lead.lost_reason) if lead.lost_reason else None,
        "priority": _PRIORITIES[lang].get(lead.priority, lead.priority),
        "estimated_value": float(lead.estimated_value) if lead.estimated_value is not None else None,
        "created_by": row.creator_name,
        "created_at": _local(lead.created_at),
        "updated_at": _local(lead.updated_at),
        "archived": t["yes"] if lead.archived_at is not None else t["no"],
    }


# ---- Excel ---------------------------------------------------------------------------------------------------------
def to_xlsx(records: Sequence[Dict[str, object]], lang: str, info: Sequence[Tuple[str, str]]) -> bytes:
    import xlsxwriter

    t = _TEXT[lang]
    out = io.BytesIO()
    # Nothing a person typed is ever interpreted: no formulas, no URLs, no numbers out of strings.
    workbook = xlsxwriter.Workbook(out, {
        "in_memory": True, "strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False,
    })
    workbook.set_properties({"title": t["title"], "author": "JAZ Sales", "comments": f"{len(records)} {t['rows']}"})
    header = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#0033A0", "border": 1, "valign": "vcenter"})
    text = workbook.add_format({"valign": "top"})
    when = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm", "valign": "top"})
    money = workbook.add_format({"num_format": "#,##0.00", "valign": "top"})
    label = workbook.add_format({"bold": True})

    sheet = workbook.add_worksheet(t["sheet"][:31])
    if lang == "ar":
        sheet.right_to_left()
    for c, column in enumerate(COLUMNS):
        sheet.write_string(0, c, column.ar if lang == "ar" else column.en, header)
        sheet.set_column(c, c, column.xlsx_width)
    for r, record in enumerate(records, start=1):
        for c, column in enumerate(COLUMNS):
            value = record.get(column.key)
            if value is None or value == "":
                sheet.write_blank(r, c, None, text)
            elif column.kind == "datetime":
                sheet.write_datetime(r, c, value, when)
            elif column.kind == "number":
                sheet.write_number(r, c, value, money)
            else:
                sheet.write_string(r, c, str(value), text)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, max(len(records), 1), len(COLUMNS) - 1)
    sheet.set_row(0, 22)

    about = workbook.add_worksheet(t["info_sheet"][:31])
    if lang == "ar":
        about.right_to_left()
    about.set_column(0, 0, 24)
    about.set_column(1, 1, 60)
    for r, (key, value) in enumerate(info):
        about.write_string(r, 0, key, label)
        about.write_string(r, 1, value)
    workbook.close()
    return out.getvalue()


# ---- PDF -----------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _register_fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(FONT_DIR / "IBMPlexSansArabic-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / "IBMPlexSansArabic-Bold.ttf")))


def _has_rtl(text: str) -> bool:
    return any("֐" <= ch <= "ࣿ" or "יִ" <= ch <= "ﻼ" for ch in text)


def visual(text: str) -> str:
    """One LINE of logical-order text as it must be drawn: Arabic letters joined (reshaped) and the line reordered for display
    (bidi). Text without right-to-left characters is returned untouched."""
    if not text or not _has_rtl(text):
        return text
    import arabic_reshaper
    from bidi import get_display

    return get_display(arabic_reshaper.reshape(text))


def wrap(text: str, width: float, measure: Callable[[str], float], max_lines: int = 4) -> List[str]:
    """Break LOGICAL-order text into lines no wider than `width` (measured in their drawn form), then convert each line to
    visual order. Wrapping first is what keeps a multi-line Arabic cell in reading order. Words longer than a line (an email
    address, an id) are cut by characters; past `max_lines` the last line ends with an ellipsis."""
    if not text:
        return [""]
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if measure(visual(candidate)) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        while measure(visual(word)) > width and len(word) > 1:      # a single word wider than the column
            cut = len(word)
            while cut > 1 and measure(visual(word[:cut])) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…" if len(lines[-1]) > 1 else "…"
    return [visual(line) for line in lines]


def _pdf_value(column: Column, value) -> str:
    if value is None or value == "":
        return "-"
    if column.kind == "datetime":
        return value.strftime("%Y-%m-%d")
    if column.key == "id":
        return str(value)[:8]            # the printable reference; the Excel export carries the full id
    return str(value)


def to_pdf(records: Sequence[Dict[str, object]], lang: str, info: Sequence[Tuple[str, str]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    _register_fonts()
    t = _TEXT[lang]
    rtl = lang == "ar"
    font_size, header_size, padding = 7, 7.5, 3
    page_w, page_h = landscape(A4)
    margin = 12 * mm
    usable = page_w - 2 * margin

    columns = list(reversed(PDF_COLUMNS)) if rtl else list(PDF_COLUMNS)
    total_weight = sum(c.pdf_weight for c in columns)
    widths = [usable * c.pdf_weight / total_weight for c in columns]
    align = "RIGHT" if rtl else "LEFT"

    def cell(text: str, width: float, *, bold: bool = False, size: float = font_size) -> str:
        font = FONT_BOLD if bold else FONT_REGULAR
        return "\n".join(wrap(text, width - 2 * padding - 1, lambda s: stringWidth(s, font, size)))

    data = [[cell(c.ar if rtl else c.en, w, bold=True, size=header_size) for c, w in zip(columns, widths)]]
    for record in records:
        data.append([cell(_pdf_value(c, record.get(c.key)), w) for c, w in zip(columns, widths)])

    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), FONT_BOLD, header_size),
        ("FONT", (0, 1), (-1, -1), FONT_REGULAR, font_size),
        ("LEADING", (0, 0), (-1, -1), font_size + 2.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0033A0")),
        ("ALIGN", (0, 0), (-1, -1), align),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
        ("LEFTPADDING", (0, 0), (-1, -1), padding),
        ("RIGHTPADDING", (0, 0), (-1, -1), padding),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for r in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F3F4F6")))
    table.setStyle(TableStyle(style))

    def info_line(key: str, value: str) -> str:
        if not rtl:
            return cell(f"{key}: {value}", usable, size=8)
        # In Arabic the label and the value are laid out as two separate runs (the value to the label's left): shaped as one
        # line, bidi would re-order a date or a number value around the label ("2026-09-25" drawn as "25-09-2026").
        measure = lambda s: stringWidth(s, FONT_REGULAR, 8)
        label = visual(f"{key}:")
        lines = wrap(value, usable - measure(label) - 8, measure)
        return "\n".join([f"{lines[0]} {label}"] + lines[1:])

    # the heading block: title + what was exported (right-aligned in Arabic)
    heading_rows = [[cell(t["title"], usable, bold=True, size=14)]]
    for key, value in info:
        heading_rows.append([info_line(key, value)])
    heading = Table(heading_rows, colWidths=[usable])
    heading.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), FONT_BOLD, 14),
        ("LEADING", (0, 0), (-1, 0), 18),
        ("FONT", (0, 1), (-1, -1), FONT_REGULAR, 8),
        ("LEADING", (0, 1), (-1, -1), 10.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#4B5563")),
        ("ALIGN", (0, 0), (-1, -1), align),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT_REGULAR, 7)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        label = visual(f"{t['page']} {doc.page}")
        if rtl:
            canvas.drawRightString(page_w - margin, 7 * mm, label)
            canvas.drawString(margin, 7 * mm, "JAZ Sales")
        else:
            canvas.drawString(margin, 7 * mm, label)
            canvas.drawRightString(page_w - margin, 7 * mm, "JAZ Sales")
        canvas.restoreState()

    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=(page_w, page_h), leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=14 * mm,
        title=t["title"], author="JAZ Sales", subject=f"{len(records)} {t['rows']}", creator="JAZ Sales",
    )
    doc.build([heading, Spacer(1, 4 * mm), table], onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


# ---- the export ----------------------------------------------------------------------------------------------------
async def _filter_summary(db: AsyncSession, ctx: StaffContext, filters: LeadFilters, sort: str, descending: bool,
                          lang: str, source_names: Dict[str, str]) -> List[Tuple[str, str]]:
    """The filters in words (both files say what they contain), and the same facts in a plain dict for the audit event."""
    t = _TEXT[lang]
    parts: List[Tuple[str, str]] = []
    if filters.q:
        parts.append((t["f_q"], filters.q))
    if filters.stages:
        parts.append((t["f_stages"], "، ".join(_STAGES[lang].get(s, s) for s in filters.stages) if lang == "ar"
                      else ", ".join(_STAGES[lang].get(s, s) for s in filters.stages)))
    if filters.unassigned:
        parts.append((t["f_assigned"], t["unassigned"]))
    elif filters.assigned_to is not None:
        # a name only for an internal staff account (the people leads are assigned to) - never any other platform user's
        person = await db.get(User, filters.assigned_to)
        staff = person is not None and person.role == PLATFORM_ROLE_STAFF
        parts.append((t["f_assigned"], person.name if staff else str(filters.assigned_to)))
    if filters.source:
        parts.append((t["f_source"], source_names.get(filters.source, filters.source)))
    if filters.campaign_id is not None:
        campaign = await campaigns_repo.get(db, filters.campaign_id)
        parts.append((t["f_campaign"], campaign.name if campaign is not None else str(filters.campaign_id)))
    if filters.priority:
        parts.append((t["f_priority"], _PRIORITIES[lang].get(filters.priority, filters.priority)))
    if filters.created_from:
        parts.append((t["f_created_from"], filters.created_from.isoformat()))
    if filters.created_to:
        parts.append((t["f_created_to"], filters.created_to.isoformat()))
    if filters.archived in ("only", "all"):
        parts.append((t["f_archived"], t["archived_only"] if filters.archived == "only" else t["archived_all"]))
    parts.append((t["f_sort"], f"{_SORTS[lang].get(sort, sort)} ({t['desc'] if descending else t['asc']})"))
    return parts


def _audit_filters(filters: LeadFilters, sort: str, descending: bool) -> dict:
    return {
        key: value for key, value in {
            "q": filters.q, "stages": list(filters.stages) if filters.stages else None,
            "assigned_to": str(filters.assigned_to) if filters.assigned_to else None, "unassigned": filters.unassigned or None,
            "source": filters.source, "campaign_id": str(filters.campaign_id) if filters.campaign_id else None,
            "priority": filters.priority, "created_from": filters.created_from.isoformat() if filters.created_from else None,
            "created_to": filters.created_to.isoformat() if filters.created_to else None,
            "archived": filters.archived, "sort": sort, "order": "desc" if descending else "asc",
        }.items() if value is not None
    }


async def export_leads(
    db: AsyncSession, ctx: StaffContext, filters: LeadFilters, *, sort: str, descending: bool, fmt: str, lang: str,
    audit: AuditContext,
) -> Tuple[bytes, str, int]:
    """(file bytes, file name, row count)."""
    rows = await leads_repo.list_all_rows(
        db, visibility=lead_visibility(ctx), filters=filters, sort=sort, descending=descending, max_rows=EXPORT_MAX_ROWS,
    )
    if len(rows) > EXPORT_MAX_ROWS:
        raise field_error(
            "filters", f"More than {EXPORT_MAX_ROWS} leads match. Narrow the filters and export again.", 413,
            code="export_too_large", max_rows=EXPORT_MAX_ROWS,
        )
    sources = await leads_repo.list_sources(db, active_only=False)
    source_names = {s.key: (s.name_ar if lang == "ar" else s.name_en) for s in sources}
    records = [lead_record(row, lang, source_names) for row in rows]

    t = _TEXT[lang]
    now_utc = datetime.now(timezone.utc)
    filter_parts = await _filter_summary(db, ctx, filters, sort, descending, lang, source_names)
    info: List[Tuple[str, str]] = [
        (t["exported_at"], now_utc.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M")),
        (t["exported_by"], ctx.user.get("name") or ""),
        (t["rows"], str(len(records))),
        (t["timezone"], f"{APP_TZ_NAME} (UTC+3)"),
    ]
    info += filter_parts if len(filter_parts) > 1 else [(t["filters"], t["no_filters"])] + filter_parts

    # Building the file is CPU work (a 10,000-row PDF takes ~12 s): off the event loop, so other requests keep being served.
    content = await asyncio.to_thread(to_xlsx if fmt == "xlsx" else to_pdf, records, lang, info)
    await audit_service.record(
        db, ctx, audit,
        action=audit_service.ACTION_LEADS_EXPORTED, target_type=audit_service.TARGET_SALES_LEADS,
        metadata={"format": fmt, "language": lang, "rows": len(records), "filters": _audit_filters(filters, sort, descending)},
    )
    logger.info("leads_exported actor=%s format=%s lang=%s rows=%d", ctx.user_id, fmt, lang, len(records))
    filename = f"jaz-leads-{now_utc.astimezone(APP_TZ).strftime('%Y%m%d-%H%M')}.{fmt}"
    return content, filename, len(records)
