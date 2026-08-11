"""
gen_api_doc_v2.py  –  SLM Studio: Full API Documentation (v2)
Generates API_Documentation_v2.pdf with Unicode symbols rendered via SegoeSym TTF.
Run:  python gen_api_doc_v2.py
"""

import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# ─── Register Segoe UI Symbol for Unicode characters not in CP1252 ───────────
_SEGOE_SYM_PATH = r"C:\Windows\Fonts\seguisym.ttf"
if os.path.exists(_SEGOE_SYM_PATH):
    pdfmetrics.registerFont(TTFont("SegoeSym", _SEGOE_SYM_PATH))
    _SEGOE_AVAILABLE = True
else:
    _SEGOE_AVAILABLE = False
    print("WARNING: seguisym.ttf not found – Unicode symbols may render blank")

def U(text: str) -> str:
    """Wrap Unicode symbols that fail in CP1252 with SegoeSym font tags."""
    if not _SEGOE_AVAILABLE:
        return text
    for ch in ["✓", "✗", "⚠", "⚙", "→", "↓", "←", "↑", "•"]:
        text = text.replace(ch, f'<font name="SegoeSym">{ch}</font>')
    return text

# ─── Output path ─────────────────────────────────────────────────────────────
OUTPUT = r"E:\งานลูกค้า\Web Service\TM KPR\Project 2\smart-model-tune-main\API_Documentation_v2.pdf"

# ─── Colour palette ──────────────────────────────────────────────────────────
C_NAVY   = colors.HexColor("#1e3a5f")
C_BLUE   = colors.HexColor("#2563eb")
C_TEAL   = colors.HexColor("#0891b2")
C_GREEN  = colors.HexColor("#16a34a")
C_AMBER  = colors.HexColor("#d97706")
C_RED    = colors.HexColor("#dc2626")
C_GRAY   = colors.HexColor("#6b7280")
C_LGRAY  = colors.HexColor("#f3f4f6")
C_MGRAY  = colors.HexColor("#e5e7eb")
C_WHITE  = colors.white
C_BLACK  = colors.black
C_DKBLUE = colors.HexColor("#1d4ed8")

# ─── Styles ──────────────────────────────────────────────────────────────────
SS = getSampleStyleSheet()

def style(name, **kw):
    kw.setdefault("fontName",  "Helvetica")
    kw.setdefault("fontSize",  10)
    kw.setdefault("textColor", C_BLACK)
    kw.setdefault("leading",   14)
    return ParagraphStyle(name, parent=SS["Normal"], **kw)

S_COVER_TITLE = style("cover_title",  fontName="Helvetica-Bold",  fontSize=28, textColor=C_WHITE,  alignment=TA_CENTER, leading=36)
S_COVER_SUB   = style("cover_sub",   fontName="Helvetica",        fontSize=14, textColor=colors.HexColor("#93c5fd"), alignment=TA_CENTER, leading=20)
S_COVER_VER   = style("cover_ver",   fontName="Helvetica",        fontSize=10, textColor=colors.HexColor("#bfdbfe"), alignment=TA_CENTER)

S_H1   = style("h1",  fontName="Helvetica-Bold",  fontSize=16, textColor=C_NAVY,  spaceBefore=14, spaceAfter=6,  leading=22)
S_H2   = style("h2",  fontName="Helvetica-Bold",  fontSize=13, textColor=C_BLUE,  spaceBefore=10, spaceAfter=4,  leading=18)
S_H3   = style("h3",  fontName="Helvetica-Bold",  fontSize=11, textColor=C_TEAL,  spaceBefore=8,  spaceAfter=3,  leading=16)
S_H4   = style("h4",  fontName="Helvetica-BoldOblique", fontSize=10, textColor=C_GRAY, spaceBefore=6, spaceAfter=2, leading=14)

S_BODY    = style("body",   leading=15, spaceAfter=4)
S_BODY_J  = style("body_j", leading=15, spaceAfter=4, alignment=TA_JUSTIFY)
S_SMALL   = style("small",  fontSize=9,  textColor=C_GRAY,   leading=13)
S_CODE    = style("code",   fontName="Courier",       fontSize=9,  textColor=colors.HexColor("#1e1e2e"), backColor=colors.HexColor("#f8f8f2"), leading=13)
S_CODE_SM = style("code_sm",fontName="Courier",       fontSize=8,  textColor=colors.HexColor("#1e1e2e"), backColor=colors.HexColor("#f8f8f2"), leading=12)
S_NOTE    = style("note",   fontSize=9,  textColor=colors.HexColor("#92400e"), backColor=colors.HexColor("#fffbeb"), leading=13, leftIndent=8, rightIndent=8, borderPadding=4)
S_TIP     = style("tip",    fontSize=9,  textColor=colors.HexColor("#065f46"), backColor=colors.HexColor("#ecfdf5"), leading=13, leftIndent=8, rightIndent=8, borderPadding=4)
S_WARN    = style("warn",   fontSize=9,  textColor=colors.HexColor("#7c2d12"), backColor=colors.HexColor("#fff7ed"), leading=13, leftIndent=8, rightIndent=8, borderPadding=4)
S_BULLET  = style("bullet", leftIndent=16, firstLineIndent=-10, leading=15)
S_CENTER  = style("center", alignment=TA_CENTER)
S_RIGHT   = style("right",  alignment=TA_RIGHT, fontSize=9, textColor=C_GRAY)

# ─── Helper builders ─────────────────────────────────────────────────────────
def H1(txt):        return Paragraph(txt, S_H1)
def H2(txt):        return Paragraph(txt, S_H2)
def H3(txt):        return Paragraph(txt, S_H3)
def H4(txt):        return Paragraph(txt, S_H4)
def P(txt):         return Paragraph(txt, S_BODY)
def PJ(txt):        return Paragraph(txt, S_BODY_J)
def Sm(txt):        return Paragraph(txt, S_SMALL)
def Code(txt):      return Paragraph(txt, S_CODE)
def CodeSm(txt):    return Paragraph(txt, S_CODE_SM)
def Note(txt):      return Paragraph(U(txt), S_NOTE)
def Tip(txt):       return Paragraph(U(txt), S_TIP)
def Warn(txt):      return Paragraph(U(txt), S_WARN)
def Bul(txt):       return Paragraph(U(f"  {txt}"), S_BULLET)
def SP(n=6):        return Spacer(1, n)
def HR():           return HRFlowable(width="100%", thickness=0.5, color=C_MGRAY, spaceAfter=4)

def section_note(txt):
    return [SP(4), Warn(f"⚠  {txt}"), SP(4)]

def engine_note(txt):
    return [SP(4), Note(f"⚙  {txt}"), SP(4)]

def method_badge(method):
    colours = {"GET": C_GREEN, "POST": C_BLUE, "PATCH": C_AMBER, "DELETE": C_RED, "PUT": C_TEAL}
    col = colours.get(method.upper(), C_GRAY)
    return f'<font color="{col.hexval() if hasattr(col,"hexval") else col}"><b>{method.upper()}</b></font>'

def endpoint_block(method, path, summary):
    badge = {"GET": "00AA55", "POST": "2563eb", "PATCH": "d97706",
             "DELETE": "dc2626", "PUT": "0891b2"}.get(method.upper(), "6b7280")
    txt = (f'<font name="Helvetica-Bold" color="#{badge}">[{method.upper()}]</font>  '
           f'<font name="Courier" color="#1e1e2e">{path}</font>  '
           f'<font color="#6b7280">— {summary}</font>')
    return Paragraph(txt, S_BODY)

def param_table(rows, colWidths=None):
    if not rows:
        return []
    header = ["Parameter", "Type", "Required", "Description"]
    data = [header] + rows
    cw = colWidths or [3.5*cm, 2.5*cm, 2*cm, 9*cm]
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_NAVY),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("FONTNAME",    (0,1), (1,-1),  "Courier"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING",(0,0), (-1,-1), 5),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ]))
    return [t, SP(4)]

def response_table(rows, colWidths=None):
    if not rows:
        return []
    header = ["Field", "Type", "Description"]
    data = [header] + rows
    cw = colWidths or [3.5*cm, 2.5*cm, 11*cm]
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_TEAL),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("FONTNAME",    (0,1), (1,-1),  "Courier"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING",(0,0), (-1,-1), 5),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ]))
    return [t, SP(4)]

def schema_table(rows, title=None):
    header = ["Column", "Type", "Constraints / Notes"]
    data = [header] + rows
    cw = [4*cm, 3.5*cm, 9.5*cm]
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_DKBLUE),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("FONTNAME",    (0,1), (1,-1),  "Courier"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING",(0,0), (-1,-1), 5),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ]))
    result = []
    if title:
        result.append(H3(title))
    result += [t, SP(6)]
    return result

# ─── Page template ───────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4

def _header_footer(canvas, doc):
    canvas.saveState()
    # Header bar
    canvas.setFillColor(C_NAVY)
    canvas.rect(0, PAGE_H - 1.2*cm, PAGE_W, 1.2*cm, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(C_WHITE)
    canvas.drawString(1.5*cm, PAGE_H - 0.85*cm, "SLM Studio  |  API Documentation v2")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(PAGE_W - 1.5*cm, PAGE_H - 0.85*cm, "Confidential")
    # Footer
    canvas.setFillColor(C_LGRAY)
    canvas.rect(0, 0, PAGE_W, 1.0*cm, fill=1, stroke=0)
    canvas.setFillColor(C_GRAY)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(1.5*cm, 0.35*cm, "engine.slmstudio.ai/v1  |  api.slmstudio.ai/v1  |  Supabase BaaS")
    canvas.drawRightString(PAGE_W - 1.5*cm, 0.35*cm, f"Page {doc.page}")
    canvas.restoreState()

def build_doc(story):
    doc = SimpleDocTemplate(
        OUTPUT,
        pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.8*cm,  bottomMargin=1.6*cm,
        title="SLM Studio API Documentation v2",
        author="SLM Studio Team",
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)

# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT
# ═══════════════════════════════════════════════════════════════════════════════

def cover_page():
    elems = []
    # Blue gradient header block
    data = [[Paragraph("SLM Studio", S_COVER_TITLE)],
            [Paragraph("API Documentation", S_COVER_TITLE)],
            [SP(8)],
            [Paragraph("Full Reference: Supabase Internal APIs + External Fine-Tuning Engine", S_COVER_SUB)],
            [SP(12)],
            [Paragraph("Version 2.0  |  2025", S_COVER_VER)],
            [Paragraph("engine.slmstudio.ai/v1  &amp;  api.slmstudio.ai/v1", S_COVER_VER)],
            ]
    t = Table(data, colWidths=[PAGE_W - 3.6*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_NAVY),
        ("TOPPADDING",  (0,0), (-1,-1), 6),
        ("BOTTOMPADDING",(0,0),(-1,-1), 6),
        ("LEFTPADDING", (0,0), (-1,-1), 20),
        ("RIGHTPADDING",(0,0), (-1,-1), 20),
        ("ROUNDEDCORNERS", [8]),
    ]))
    elems.append(SP(40))
    elems.append(t)
    elems.append(SP(20))

    # Info table
    info = [
        ["Project",   "Smart Model Tune (SLM Studio)"],
        ["Stack",     "React 18 + TypeScript + Vite + Supabase + shadcn/ui"],
        ["Backend",   "Supabase BaaS (PostgreSQL + Auth + Row-Level Security)"],
        ["Engine",    "engine.slmstudio.ai/v1  (Auto Fine-Tuning Pipeline)"],
        ["Inference", "api.slmstudio.ai/v1  (OpenAI-Compatible Inference)"],
        ["Auth",      "Bearer sk-slm-{prefix}-{random4}  |  Supabase JWT"],
        ["Date",      "May 2025"],
    ]
    it = Table(info, colWidths=[4*cm, 13*cm])
    it.setStyle(TableStyle([
        ("FONTNAME",  (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",  (1,0), (1,-1), "Helvetica"),
        ("FONTSIZE",  (0,0), (-1,-1), 9),
        ("TEXTCOLOR", (0,0), (0,-1), C_NAVY),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_LGRAY, C_WHITE]),
        ("GRID",      (0,0), (-1,-1), 0.3, C_MGRAY),
        ("TOPPADDING",(0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ("LEFTPADDING",(0,0),(-1,-1), 6),
    ]))
    elems.append(it)
    elems.append(PageBreak())
    return elems


def toc_page():
    elems = [H1("Table of Contents"), HR(), SP(6)]
    toc = [
        ("1.", "Overview & Architecture"),
        ("2.", "Authentication"),
        ("3.", "Internal Supabase APIs"),
        ("  3.1", "Projects API"),
        ("  3.2", "Models API"),
        ("  3.3", "Datasets API"),
        ("  3.4", "Deployments API"),
        ("  3.5", "API Keys API"),
        ("  3.6", "Analytics API"),
        ("4.", "External Fine-Tuning Engine APIs  (engine.slmstudio.ai/v1)"),
        ("  4.1", "Dataset Upload & Analysis"),
        ("  4.2", "Training Job Lifecycle"),
        ("  4.3", "Training Progress Stream (SSE)"),
        ("  4.4", "Model Evaluation"),
        ("  4.5", "Hyperparameter Optimisation (HPO)"),
        ("  4.6", "Auto-Tuning Parameters"),
        ("5.", "Inference API  (api.slmstudio.ai/v1)"),
        ("  5.1", "Chat Completions"),
        ("  5.2", "Model Listing"),
        ("6.", "Engine Deployment Management"),
        ("  6.1", "Create Endpoint"),
        ("  6.2", "Scale / Terminate Endpoint"),
        ("  6.3", "Endpoint Health"),
        ("7.", "Account & Billing APIs"),
        ("8.", "Database Schema (Supabase)"),
        ("9.", "UI Component  →  API Mapping"),
        ("10.", "Error Codes Reference"),
        ("11.", "Mock Data Locations & Migration Guide"),
    ]
    for num, title in toc:
        indent = 16 if num.startswith("  ") else 0
        elems.append(Paragraph(f'<font name="Courier">{num.ljust(6)}</font>{title}',
                                style("toc_item", leftIndent=indent, fontSize=10, leading=16)))
    elems.append(PageBreak())
    return elems


def sec_overview():
    elems = [H1("1. Overview & Architecture"), HR()]
    elems.append(PJ(
        "SLM Studio is a full-stack platform for fine-tuning Small Language Models (SLMs). "
        "The UI is built with React 18 + Vite, backed by Supabase for user data, and connected "
        "to an external GPU-powered fine-tuning engine for training, evaluation, and deployment."
    ))
    elems.append(SP(6))

    # Architecture diagram as table
    arch = [
        [Paragraph("<b>Layer</b>", S_BODY), Paragraph("<b>Technology</b>", S_BODY),
         Paragraph("<b>Responsibility</b>", S_BODY)],
        ["Browser / UI",    "React 18 + shadcn/ui",     "User interaction, form management, visualisation"],
        ["Supabase BaaS",   "PostgreSQL + Auth + RLS",   "User accounts, project metadata, API keys, analytics events"],
        ["Fine-Tune Engine","engine.slmstudio.ai/v1",   "GPU training, HPO, SSE progress stream, evaluation"],
        ["Inference API",   "api.slmstudio.ai/v1",      "OpenAI-compatible chat completions for deployed models"],
        ["Deployment Mgr",  "engine.slmstudio.ai/v1",   "Endpoint lifecycle: create, scale, health, terminate"],
    ]
    t = Table(arch, colWidths=[4*cm, 5*cm, 8*cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_NAVY),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
    ]))
    elems += [t, SP(8)]

    elems.append(H2("Training Job State Flow"))
    flow = Table(
        [[Paragraph(U("queued → training → evaluating → completed\n               ↓                       ↓\n           failed                    failed"), S_CODE)]],
        colWidths=[PAGE_W - 3.6*cm]
    )
    flow.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#1e1e2e")),
        ("TOPPADDING",  (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
    ]))
    elems += [flow, SP(6)]
    elems += engine_note(
        "⚙  All training operations are asynchronous. Poll GET /jobs/{id} or subscribe to "
        "GET /jobs/{id}/stream (SSE) to track progress."
    )
    elems.append(PageBreak())
    return elems


def sec_auth():
    elems = [H1("2. Authentication"), HR()]
    elems.append(H2("2.1 Supabase Auth (Internal)"))
    elems.append(P(
        "Supabase manages user registration and login via JWT tokens stored in the browser. "
        "All Supabase table operations use Row-Level Security (RLS) so users can only access their own rows."
    ))
    elems.append(SP(4))
    data = [
        ["Method", "Endpoint", "Description"],
        ["POST", "POST /auth/v1/signup",   "Register new account (email + password)"],
        ["POST", "POST /auth/v1/token",    "Login — returns access_token (JWT)"],
        ["POST", "POST /auth/v1/logout",   "Invalidate current session"],
        ["GET",  "GET  /auth/v1/user",     "Return current user profile"],
    ]
    t = Table(data, colWidths=[2*cm, 7*cm, 8*cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_NAVY),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("FONTNAME",    (0,1), (1,-1),  "Courier"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
    ]))
    elems += [t, SP(8)]

    elems.append(H2("2.2 SLM API Keys (External Engine & Inference)"))
    elems.append(P(
        "External calls to <b>engine.slmstudio.ai/v1</b> and <b>api.slmstudio.ai/v1</b> use "
        "Bearer token authentication with SLM-format API keys."
    ))
    elems += [
        Code("Authorization: Bearer sk-slm-{prefix}-{random4}"),
        SP(4),
    ]
    elems += section_note(
        "⚠  API keys are stored hashed in Supabase. The plain-text key is shown only once at creation. "
        "Key format: sk-slm-{first4chars}-{4-char-random}. Example: sk-slm-mypr-a3f9"
    )
    elems.append(SP(4))
    elems.append(H3("Key Scopes"))
    scopes = [
        ["Scope", "Allowed Operations"],
        ["inference",    "POST /chat/completions, GET /models"],
        ["training",     "POST /datasets, POST /jobs, GET /jobs/*"],
        ["deployment",   "POST /endpoints, PATCH /endpoints/{id}, DELETE /endpoints/{id}"],
        ["read-only",    "GET operations only (no mutations)"],
        ["admin",        "All operations including billing and HPO"],
    ]
    t2 = Table(scopes, colWidths=[4*cm, 13*cm], repeatRows=1)
    t2.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_TEAL),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("FONTNAME",    (0,1), (0,-1),  "Courier"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
    ]))
    elems += [t2, SP(4)]
    elems.append(PageBreak())
    return elems


def sec_internal_projects():
    elems = [H1("3. Internal Supabase APIs"), HR(),
             H2("3.1 Projects API"), SP(4)]
    elems += section_note("⚠  These functions call the Supabase JavaScript client directly. "
                          "No HTTP base URL is needed; authentication is via the active Supabase session.")
    elems.append(H3("listProjects()"))
    elems.append(endpoint_block("GET", "/rest/v1/projects", "Return all projects owned by current user"))
    elems += response_table([
        ["id",          "uuid",     "Primary key"],
        ["name",        "string",   "Project display name"],
        ["description", "string",   "Task prompt / description"],
        ["task_type",   "string",   "classification | generation | summarisation | translation | qa | code"],
        ["base_model",  "string",   "Base model identifier (e.g. mistral-7b)"],
        ["status",      "string",   "draft | training | completed | failed"],
        ["epochs",      "integer",  "Number of training epochs"],
        ["learning_rate","float",   "Learning rate (e.g. 0.0001)"],
        ["dataset_size","integer",  "Estimated dataset row count"],
        ["created_at",  "timestamp","ISO 8601 creation timestamp"],
        ["updated_at",  "timestamp","ISO 8601 last-updated timestamp"],
    ])

    elems.append(H3("createProject(input)"))
    elems.append(endpoint_block("POST", "/rest/v1/projects", "Insert a new project row"))
    elems += param_table([
        ["name",         "string",  "Yes", "Project name (max 120 chars)"],
        ["description",  "string",  "Yes", "Task prompt"],
        ["taskType",     "TaskType","Yes", "One of: classification | generation | summarisation | translation | qa | code"],
        ["baseModel",    "BaseModel","Yes","One of supported base model identifiers"],
        ["epochs",       "integer", "No",  "Default auto-computed by autoTuneParams()"],
        ["learningRate", "float",   "No",  "Default auto-computed"],
        ["datasetSize",  "integer", "No",  "Estimated number of dataset rows"],
    ])

    elems.append(H3("updateProject(id, patch)"))
    elems.append(endpoint_block("PATCH", "/rest/v1/projects?id=eq.{id}", "Partial update of a project"))
    elems += param_table([
        ["id",    "uuid",   "Yes", "Project UUID"],
        ["patch", "object", "Yes", "Partial ProjectRow fields to update"],
    ])

    elems.append(H3("deleteProject(id)"))
    elems.append(endpoint_block("DELETE", "/rest/v1/projects?id=eq.{id}", "Delete a project"))
    elems += param_table([["id", "uuid", "Yes", "Project UUID"]])
    elems.append(PageBreak())
    return elems


def sec_internal_models():
    elems = [H2("3.2 Models API"), SP(4)]
    elems += section_note("⚠  Returns trained_models rows joined with associated project metadata.")
    elems.append(H3("listModels()"))
    elems.append(endpoint_block("GET", "/rest/v1/trained_models", "Return all trained models for current user"))
    elems += response_table([
        ["id",           "uuid",    "Primary key"],
        ["project_id",   "uuid",    "Parent project UUID"],
        ["name",         "string",  "Model display name"],
        ["task_type",    "string",  "Task category"],
        ["base_model",   "string",  "Foundation model used"],
        ["status",       "string",  "training | completed | failed | deployed"],
        ["accuracy",     "float",   "Evaluation accuracy (0-1)"],
        ["f1_score",     "float",   "F1 score (0-1)"],
        ["inference_speed","float", "Tokens per second"],
        ["model_size_mb","integer", "Model file size in MB"],
        ["created_at",   "timestamp","ISO 8601"],
    ])
    elems.append(H3("getModel(id)"))
    elems.append(endpoint_block("GET", "/rest/v1/trained_models?id=eq.{id}", "Return a single model by ID"))
    elems += param_table([["id", "uuid", "Yes", "Model UUID"]])
    elems.append(SP(6))
    return elems


def sec_internal_datasets():
    elems = [H2("3.3 Datasets API"), SP(4)]
    elems += section_note("⚠  Dataset rows are metadata only. Actual file content is stored on the Engine.")
    elems.append(H3("listDatasets()"))
    elems.append(endpoint_block("GET", "/rest/v1/datasets", "Return all datasets for current user"))
    elems += response_table([
        ["id",           "uuid",   "Primary key"],
        ["project_id",   "uuid",   "Associated project (nullable)"],
        ["name",         "string", "Dataset name"],
        ["description",  "string", "Optional description"],
        ["rows",         "integer","Number of data rows"],
        ["columns",      "integer","Number of columns/features"],
        ["file_size",    "string", "Human-readable size (e.g. 2.4 MB)"],
        ["format",       "string", "csv | jsonl | parquet | txt"],
        ["quality_score","float",  "0-100 auto quality score from Engine"],
        ["created_at",   "timestamp","ISO 8601"],
    ])
    elems.append(PageBreak())
    return elems


def sec_internal_deployments():
    elems = [H2("3.4 Deployments API"), SP(4)]
    elems += section_note("⚠  Endpoint status transitions: inactive → active → inactive. "
                          "Hardware allocation is managed by the Engine.")
    elems.append(H3("listEndpoints()"))
    elems.append(endpoint_block("GET", "/rest/v1/deployed_endpoints", "Return all deployed endpoints for current user"))
    elems += response_table([
        ["id",           "uuid",   "Primary key"],
        ["model_id",     "uuid",   "Associated trained_models UUID"],
        ["name",         "string", "Endpoint display name"],
        ["status",       "string", "active | inactive | provisioning | error"],
        ["url",          "string", "HTTPS endpoint URL for inference"],
        ["region",       "string", "Deployment region (e.g. us-east-1)"],
        ["replicas",     "integer","Number of running replicas"],
        ["requests_per_min","integer","Current RPS (from Engine metrics)"],
        ["avg_latency_ms","integer","P50 latency from Engine"],
        ["created_at",   "timestamp","ISO 8601"],
    ])
    elems.append(H3("setEndpointStatus(id, status)"))
    elems.append(endpoint_block("PATCH", "/rest/v1/deployed_endpoints?id=eq.{id}", "Update endpoint status"))
    elems += param_table([
        ["id",     "uuid",   "Yes", "Endpoint UUID"],
        ["status", "string", "Yes", "active | inactive"],
    ])
    elems.append(SP(4))
    return elems


def sec_internal_apikeys():
    elems = [H2("3.5 API Keys API"), SP(4)]
    elems += section_note("⚠  Plain-text key is returned only at creation. Store it immediately; "
                          "it cannot be retrieved after the initial response.")
    elems.append(H3("listApiKeys()"))
    elems.append(endpoint_block("GET", "/rest/v1/api_keys", "Return all API keys for current user (hashed values only)"))
    elems += response_table([
        ["id",         "uuid",      "Primary key"],
        ["name",       "string",    "User-given label"],
        ["key_prefix", "string",    "First 12 chars for display (e.g. sk-slm-mypr-)"],
        ["key_hash",   "string",    "SHA-256 hash of full key"],
        ["scopes",     "string[]",  "Array of granted scopes"],
        ["last_used",  "timestamp", "Last successful API call timestamp (nullable)"],
        ["created_at", "timestamp", "ISO 8601"],
        ["expires_at", "timestamp", "Expiry timestamp (nullable = no expiry)"],
    ])
    elems.append(H3("createApiKey(name)"))
    elems.append(endpoint_block("POST", "/rest/v1/api_keys", "Generate a new API key"))
    elems += param_table([["name", "string", "Yes", "Display label for the key (max 60 chars)"]])
    elems.append(H3("revokeApiKey(id)"))
    elems.append(endpoint_block("DELETE", "/rest/v1/api_keys?id=eq.{id}", "Hard-delete an API key"))
    elems += param_table([["id", "uuid", "Yes", "API key UUID"]])
    elems.append(PageBreak())
    return elems


def sec_internal_analytics():
    elems = [H2("3.6 Analytics API"), SP(4)]
    elems += section_note("⚠  Analytics events are written by the Inference API on each request. "
                          "The UI reads aggregate views only; individual event rows may be large.")
    elems.append(H3("listCallEvents(range)"))
    elems.append(endpoint_block("GET", "/rest/v1/api_call_events", "Return paginated call events within a time range"))
    elems += param_table([
        ["range",      "string", "No", "7d | 30d | 90d  (default: 30d)"],
        ["endpoint_id","uuid",   "No", "Filter by endpoint UUID"],
        ["model_id",   "uuid",   "No", "Filter by model UUID"],
    ])

    elems.append(H3("summarize()  /  bucketByTime()  /  endpointStats()"))
    elems.append(P(
        "Helper functions computed client-side from listCallEvents() data. "
        "bucketByTime() aggregates events by hour/day. "
        "endpointStats() computes p50/p95/p99 latency percentiles and error rates per endpoint."
    ))
    elems += response_table([
        ["total_calls",    "integer", "Total API calls in range"],
        ["total_tokens",   "integer", "Total tokens processed"],
        ["avg_latency_ms", "float",   "Mean latency across all calls"],
        ["p50_latency_ms", "float",   "Median (50th percentile) latency"],
        ["p95_latency_ms", "float",   "95th percentile latency"],
        ["p99_latency_ms", "float",   "99th percentile latency"],
        ["error_rate",     "float",   "Fraction of requests with status >= 400"],
        ["success_rate",   "float",   "Fraction of requests with status 200"],
    ])
    elems.append(PageBreak())
    return elems


def sec_engine_intro():
    elems = [H1("4. External Fine-Tuning Engine APIs"), HR(),
             H2("Base URL:  engine.slmstudio.ai/v1"), SP(4)]
    elems.append(P(
        "The fine-tuning engine is a GPU-backed service that handles all computationally intensive "
        "operations: dataset ingestion, model training, hyperparameter optimisation, evaluation, and "
        "deployment management. All requests require a valid SLM API key with appropriate scope."
    ))
    elems += engine_note(
        "⚙  The engine uses asynchronous job queuing. Long-running operations return a job_id "
        "immediately; results are retrieved via polling or SSE streaming."
    )
    return elems


def sec_engine_datasets():
    elems = [H2("4.1 Dataset Upload & Analysis"), SP(4)]

    elems.append(H3("Upload Dataset"))
    elems.append(endpoint_block("POST", "/datasets", "Upload a new dataset file for analysis"))
    elems += param_table([
        ["file",        "File",   "Yes", "Multipart file upload (CSV, JSONL, Parquet, TXT)"],
        ["project_id",  "string", "Yes", "Supabase project UUID to associate with"],
        ["name",        "string", "Yes", "Human-readable dataset name"],
        ["description", "string", "No",  "Optional description"],
        ["task_type",   "string", "No",  "Hint for quality analysis"],
    ])
    elems += response_table([
        ["dataset_id",    "string",  "Engine-assigned dataset ID (store in Supabase datasets table)"],
        ["rows",          "integer", "Detected row count"],
        ["columns",       "integer", "Detected column count"],
        ["file_size",     "string",  "Human-readable size"],
        ["format",        "string",  "Detected format"],
        ["quality_score", "float",   "0-100 quality score (completeness, balance, etc.)"],
        ["analysis",      "object",  "Detailed analysis: class_distribution, missing_values, token_lengths"],
        ["warnings",      "string[]","Quality warnings (e.g. 'class imbalance detected')"],
    ])

    elems.append(H3("Get Dataset Analysis"))
    elems.append(endpoint_block("GET", "/datasets/{dataset_id}/analysis", "Retrieve full quality analysis report"))
    elems += param_table([
        ["dataset_id", "string", "Yes", "Engine dataset ID"],
        ["include_sample", "boolean", "No", "Include 10-row preview (default false)"],
    ])
    elems.append(PageBreak())
    return elems


def sec_engine_training():
    elems = [H2("4.2 Training Job Lifecycle"), SP(4)]

    elems.append(H3("Create Training Job"))
    elems.append(endpoint_block("POST", "/jobs", "Queue a new fine-tuning job"))
    elems += param_table([
        ["project_id",     "string",  "Yes", "Supabase project UUID"],
        ["dataset_id",     "string",  "Yes", "Engine dataset ID from upload step"],
        ["base_model",     "string",  "Yes", "Foundation model (e.g. mistral-7b, llama-3-8b)"],
        ["task_type",      "string",  "Yes", "classification | generation | summarisation | translation | qa | code"],
        ["epochs",         "integer", "No",  "Override auto-tuned epochs (1-50)"],
        ["learning_rate",  "float",   "No",  "Override auto-tuned LR (1e-6 to 1e-2)"],
        ["batch_size",     "integer", "No",  "Override auto-tuned batch size (4-128)"],
        ["warmup_steps",   "integer", "No",  "LR warmup steps (default: 100)"],
        ["weight_decay",   "float",   "No",  "L2 regularisation (default: 0.01)"],
        ["lora_rank",      "integer", "No",  "LoRA rank for PEFT training (default: 8)"],
        ["use_hpo",        "boolean", "No",  "Run HPO before training (default: false)"],
        ["hpo_trials",     "integer", "No",  "Number of HPO trials if use_hpo=true (default: 10)"],
    ])
    elems += response_table([
        ["job_id",        "string",    "Engine job UUID"],
        ["status",        "string",    "queued"],
        ["estimated_time","integer",   "Estimated training seconds"],
        ["queue_position","integer",   "Position in GPU queue"],
        ["created_at",    "timestamp", "ISO 8601"],
    ])

    elems.append(H3("Get Job Status"))
    elems.append(endpoint_block("GET", "/jobs/{job_id}", "Return current status and metrics for a job"))
    elems += response_table([
        ["job_id",        "string",  "Engine job UUID"],
        ["status",        "string",  "queued | training | evaluating | completed | failed"],
        ["current_epoch", "integer", "Current epoch number"],
        ["total_epochs",  "integer", "Total epochs planned"],
        ["train_loss",    "float",   "Latest training loss"],
        ["eval_loss",     "float",   "Latest evaluation loss"],
        ["accuracy",      "float",   "Latest evaluation accuracy"],
        ["elapsed_seconds","integer","Elapsed training time"],
        ["model_id",      "string",  "Engine model ID (set when completed)"],
        ["error",         "string",  "Error message if status=failed"],
    ])

    elems.append(H3("Cancel Job"))
    elems.append(endpoint_block("DELETE", "/jobs/{job_id}", "Cancel a queued or running job"))
    elems += param_table([["job_id", "string", "Yes", "Engine job UUID"]])
    elems.append(PageBreak())
    return elems


def sec_engine_sse():
    elems = [H2("4.3 Training Progress Stream (SSE)"), SP(4)]
    elems.append(endpoint_block("GET", "/jobs/{job_id}/stream",
                                "Server-Sent Events stream of training progress"))
    elems.append(SP(4))
    elems.append(P("Subscribe with EventSource. Each event has type <b>progress</b> or <b>log</b>:"))
    elems.append(SP(4))
    sse_example = (
        "const es = new EventSource(\n"
        "  `https://engine.slmstudio.ai/v1/jobs/${jobId}/stream`,\n"
        "  { headers: { Authorization: `Bearer ${apiKey}` } }\n"
        ");\n\n"
        "es.addEventListener('progress', (e) => {\n"
        "  const d = JSON.parse(e.data);\n"
        "  // d.epoch, d.loss, d.accuracy, d.elapsed_seconds\n"
        "  updateProgressBar(d.epoch / d.total_epochs);\n"
        "});\n\n"
        "es.addEventListener('log', (e) => {\n"
        "  appendLog(JSON.parse(e.data).message);\n"
        "});\n\n"
        "es.addEventListener('complete', (e) => {\n"
        "  const d = JSON.parse(e.data);\n"
        "  // d.model_id, d.final_accuracy, d.final_loss\n"
        "  es.close();\n"
        "});"
    )
    code_t = Table([[Code(sse_example)]], colWidths=[PAGE_W - 3.6*cm])
    code_t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#1e1e2e")),
        ("TOPPADDING",  (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
        ("RIGHTPADDING",(0,0), (-1,-1), 12),
    ]))
    elems += [code_t, SP(6)]
    elems += section_note("⚠  The current codebase (useTrainingSimulator.ts) uses a fake setTimeout loop. "
                          "Replace with the EventSource pattern above to receive real training progress.")
    elems.append(H3("SSE Event Payload Fields"))
    elems += response_table([
        ["type",           "string",  "progress | log | complete | error"],
        ["epoch",          "integer", "Current epoch (progress events)"],
        ["total_epochs",   "integer", "Total epochs (progress events)"],
        ["loss",           "float",   "Training loss at this step"],
        ["eval_loss",      "float",   "Validation loss (if eval step)"],
        ["accuracy",       "float",   "Validation accuracy (if eval step)"],
        ["elapsed_seconds","integer", "Elapsed seconds since job start"],
        ["message",        "string",  "Human-readable log line (log events)"],
        ["model_id",       "string",  "Final model ID (complete event only)"],
    ])
    elems.append(PageBreak())
    return elems


def sec_engine_eval():
    elems = [H2("4.4 Model Evaluation"), SP(4)]

    elems.append(H3("Trigger Evaluation"))
    elems.append(endpoint_block("POST", "/models/{model_id}/evaluate",
                                "Run full evaluation on a held-out test set"))
    elems += param_table([
        ["model_id",    "string", "Yes", "Engine model ID"],
        ["dataset_id",  "string", "No",  "Evaluation dataset (defaults to 20% split from training set)"],
        ["metrics",     "array",  "No",  "List of metrics: accuracy, f1, bleu, rouge, perplexity"],
    ])
    elems += response_table([
        ["eval_id",       "string", "Evaluation run UUID"],
        ["accuracy",      "float",  "Accuracy score (0-1)"],
        ["f1_score",      "float",  "Macro F1 score"],
        ["precision",     "float",  "Macro precision"],
        ["recall",        "float",  "Macro recall"],
        ["bleu",          "float",  "BLEU score (generation tasks)"],
        ["rouge_l",       "float",  "ROUGE-L score (summarisation tasks)"],
        ["perplexity",    "float",  "Perplexity (language modelling tasks)"],
        ["confusion_matrix","object","Class-level confusion matrix (classification tasks)"],
        ["inference_speed","float", "Tokens per second on eval hardware"],
        ["evaluated_at",  "timestamp","ISO 8601"],
    ])
    elems += section_note("⚠  ProjectDetail.tsx currently computes metrics from a hash of the project ID "
                          "(lines 77-86). Replace with this endpoint to get real evaluation scores.")
    elems.append(PageBreak())
    return elems


def sec_engine_hpo():
    elems = [H2("4.5 Hyperparameter Optimisation (HPO)"), SP(4)]
    elems.append(P(
        "HPO runs multiple short training trials to find the best hyperparameter combination. "
        "Results are stored and can be applied automatically to the full training run."
    ))
    elems.append(H3("Create HPO Job"))
    elems.append(endpoint_block("POST", "/hpo", "Queue a hyperparameter search job"))
    elems += param_table([
        ["dataset_id",  "string",  "Yes", "Engine dataset ID"],
        ["base_model",  "string",  "Yes", "Foundation model"],
        ["task_type",   "string",  "Yes", "Task category"],
        ["n_trials",    "integer", "No",  "Number of search trials (default: 10, max: 50)"],
        ["search_space","object",  "No",  "Override default search space (lr, batch_size, epochs, lora_rank)"],
        ["metric",      "string",  "No",  "Optimisation target: accuracy | f1 | loss (default: accuracy)"],
    ])
    elems += response_table([
        ["hpo_id",          "string", "HPO job UUID"],
        ["status",          "string", "queued | running | completed"],
        ["best_trial",      "object", "Best trial: {lr, batch_size, epochs, accuracy}"],
        ["all_trials",      "array",  "All trial results sorted by metric descending"],
        ["recommended_config","object","Ready-to-use config for POST /jobs"],
    ])

    elems.append(H2("4.6 Auto-Tuning Parameters (Client-side Heuristic)"))
    elems.append(P(
        "The client has a lightweight auto-tuning heuristic in <b>NewProject.tsx</b> "
        "that estimates hyperparameters from dataset size before the engine HPO runs:"
    ))
    heuristic = [
        ["Dataset Size (rows)", "Epochs", "Learning Rate", "Batch Size"],
        ["< 1,000",             "10",     "1e-4",          "8"],
        ["1,000 – 5,000",       "5",      "2e-4",          "16"],
        ["> 5,000",             "3",      "3e-4",          "32"],
    ]
    ht = Table(heuristic, colWidths=[5*cm, 3*cm, 4*cm, 4*cm], repeatRows=1)
    ht.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_TEAL),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
    ]))
    elems += [ht, SP(4)]
    elems += engine_note(
        "⚙  This heuristic uses files.length × 500 as a dataset size proxy. "
        "After engine integration, use the actual row count from POST /datasets response."
    )
    elems.append(PageBreak())
    return elems


def sec_inference():
    elems = [H1("5. Inference API  (api.slmstudio.ai/v1)"), HR(), SP(4)]
    elems.append(P(
        "The Inference API is OpenAI-compatible, allowing any OpenAI SDK to be pointed at "
        "api.slmstudio.ai/v1 with a SLM API key."
    ))

    elems.append(H2("5.1 Chat Completions"))
    elems.append(endpoint_block("POST", "/chat/completions",
                                "Generate a chat response from a deployed SLM model"))
    elems += param_table([
        ["model",       "string",  "Yes", "Deployed model name (from /models list)"],
        ["messages",    "array",   "Yes", "[{role: user|assistant|system, content: string}]"],
        ["temperature", "float",   "No",  "Sampling temperature 0-2 (default: 0.7)"],
        ["max_tokens",  "integer", "No",  "Maximum tokens to generate (default: 512)"],
        ["stream",      "boolean", "No",  "Enable streaming (default: false)"],
        ["top_p",       "float",   "No",  "Nucleus sampling (default: 1.0)"],
        ["stop",        "array",   "No",  "Stop sequences"],
    ])
    elems += response_table([
        ["id",           "string",  "Response UUID"],
        ["object",       "string",  "chat.completion"],
        ["model",        "string",  "Model name used"],
        ["choices",      "array",   "[{index, message: {role, content}, finish_reason}]"],
        ["usage",        "object",  "{prompt_tokens, completion_tokens, total_tokens}"],
        ["created",      "integer", "Unix timestamp"],
    ])
    elems += section_note("⚠  ChatPanel.tsx currently uses mockResponses array with setTimeout. "
                          "Replace handleSend() with a fetch to this endpoint.")

    elems.append(H2("5.2 Model Listing"))
    elems.append(endpoint_block("GET", "/models", "List all models available for inference"))
    elems += response_table([
        ["id",          "string",  "Model identifier"],
        ["name",        "string",  "Display name"],
        ["task_type",   "string",  "Task category"],
        ["context_length","integer","Maximum context window in tokens"],
        ["status",      "string",  "available | loading | offline"],
        ["created",     "integer", "Unix timestamp"],
    ])
    elems.append(PageBreak())
    return elems


def sec_engine_deployment():
    elems = [H1("6. Engine Deployment Management"), HR(), SP(4)]
    elems.append(P(
        "Deployment operations create and manage GPU-backed inference endpoints. "
        "Endpoint metadata is mirrored to the Supabase deployed_endpoints table after creation."
    ))

    elems.append(H2("6.1 Create Endpoint"))
    elems.append(endpoint_block("POST", "/endpoints", "Provision a new inference endpoint"))
    elems += param_table([
        ["model_id",    "string",  "Yes", "Engine model ID (from completed training job)"],
        ["name",        "string",  "Yes", "Endpoint display name"],
        ["region",      "string",  "No",  "us-east-1 | us-west-2 | eu-west-1 | ap-southeast-1"],
        ["replicas",    "integer", "No",  "Initial replica count (default: 1, max: 8)"],
        ["instance_type","string", "No",  "gpu-small | gpu-medium | gpu-large (default: gpu-small)"],
        ["autoscale",   "boolean", "No",  "Enable auto-scaling (default: false)"],
        ["min_replicas","integer", "No",  "Minimum replicas for autoscaling (default: 1)"],
        ["max_replicas","integer", "No",  "Maximum replicas for autoscaling (default: 4)"],
    ])
    elems += response_table([
        ["endpoint_id", "string",    "Engine endpoint UUID (store as deployed_endpoints.id)"],
        ["url",         "string",    "HTTPS inference URL"],
        ["status",      "string",    "provisioning"],
        ["region",      "string",    "Provisioned region"],
        ["estimated_ready_seconds","integer","ETA for endpoint to become active"],
    ])

    elems.append(H2("6.2 Scale / Terminate Endpoint"))
    elems.append(H3("Scale Endpoint"))
    elems.append(endpoint_block("PATCH", "/endpoints/{endpoint_id}",
                                "Change replica count or instance type"))
    elems += param_table([
        ["endpoint_id", "string",  "Yes", "Engine endpoint UUID"],
        ["replicas",    "integer", "No",  "New replica count"],
        ["instance_type","string", "No",  "New instance type"],
    ])
    elems.append(H3("Terminate Endpoint"))
    elems.append(endpoint_block("DELETE", "/endpoints/{endpoint_id}",
                                "Shut down and deallocate an endpoint"))
    elems += param_table([["endpoint_id", "string", "Yes", "Engine endpoint UUID"]])

    elems.append(H2("6.3 Endpoint Health"))
    elems.append(endpoint_block("GET", "/endpoints/{endpoint_id}/health",
                                "Return real-time metrics for an endpoint"))
    elems += response_table([
        ["status",          "string",  "active | loading | error | offline"],
        ["replicas_ready",  "integer", "Number of healthy replicas"],
        ["requests_per_min","integer", "Requests in last 60 seconds"],
        ["avg_latency_ms",  "integer", "Rolling P50 latency"],
        ["p95_latency_ms",  "integer", "Rolling P95 latency"],
        ["error_rate",      "float",   "Fraction of 5xx responses in last 5 min"],
        ["gpu_utilisation", "float",   "Average GPU utilisation (0-1)"],
        ["checked_at",      "timestamp","ISO 8601"],
    ])
    elems += section_note("⚠  Deployment.tsx uses Math.random() for timeline values on every render. "
                          "Replace mockUsageTimeline with periodic calls to this health endpoint.")
    elems.append(PageBreak())
    return elems


def sec_billing():
    elems = [H1("7. Account & Billing APIs"), HR(), SP(4)]
    elems += engine_note(
        "⚙  Billing is managed by the Engine service. Supabase stores a cached copy of "
        "credits_remaining and plan_tier for fast UI display."
    )

    elems.append(H2("7.1 Get Account Info"))
    elems.append(endpoint_block("GET", "/account", "Return plan, credits, and usage summary"))
    elems += response_table([
        ["user_id",          "string",  "Supabase user UUID"],
        ["plan_tier",        "string",  "free | starter | pro | enterprise"],
        ["credits_remaining","float",   "GPU-compute credits remaining"],
        ["credits_used",     "float",   "Credits used this billing period"],
        ["training_jobs_used","integer","Jobs consumed this period"],
        ["training_jobs_limit","integer","Plan job limit (null = unlimited)"],
        ["endpoints_active", "integer", "Currently active endpoint count"],
        ["endpoints_limit",  "integer", "Plan endpoint limit"],
        ["renews_at",        "timestamp","Next billing period start"],
    ])

    elems.append(H2("7.2 Get Usage History"))
    elems.append(endpoint_block("GET", "/account/usage",
                                "Return credit consumption history by job/endpoint"))
    elems += param_table([
        ["range", "string", "No", "7d | 30d | 90d (default: 30d)"],
        ["type",  "string", "No", "training | inference | all (default: all)"],
    ])
    elems += response_table([
        ["events",         "array",  "Array of {timestamp, type, amount, description, job_id}"],
        ["total_consumed", "float",  "Total credits consumed in range"],
        ["by_type",        "object", "{training: float, inference: float}"],
    ])
    elems.append(PageBreak())
    return elems


def sec_db_schema():
    elems = [H1("8. Database Schema (Supabase)"), HR(), SP(4)]
    elems.append(P(
        "All tables use Supabase Row-Level Security. Rows are automatically filtered "
        "to the authenticated user via auth.uid()."
    ))

    elems += schema_table([
        ["id",            "uuid",         "PRIMARY KEY DEFAULT gen_random_uuid()"],
        ["user_id",       "uuid",         U("NOT NULL  FK → auth.users(id)  ON DELETE CASCADE")],
        ["name",          "text",         "NOT NULL  CHECK (length(name) <= 120)"],
        ["description",   "text",         ""],
        ["task_type",     "text",         "NOT NULL  CHECK (task_type IN ('classification','generation','summarisation','translation','qa','code'))"],
        ["base_model",    "text",         "NOT NULL"],
        ["status",        "text",         "DEFAULT 'draft'  CHECK (status IN ('draft','training','completed','failed'))"],
        ["epochs",        "integer",      "DEFAULT 5"],
        ["learning_rate", "numeric(10,8)","DEFAULT 0.0001"],
        ["dataset_size",  "integer",      "DEFAULT 0"],
        ["created_at",    "timestamptz",  "DEFAULT now()"],
        ["updated_at",    "timestamptz",  "DEFAULT now()"],
    ], title="TABLE: projects")

    elems += schema_table([
        ["id",             "uuid",         "PRIMARY KEY DEFAULT gen_random_uuid()"],
        ["user_id",        "uuid",         U("NOT NULL  FK → auth.users(id)  ON DELETE CASCADE")],
        ["project_id",     "uuid",         U("FK → projects(id)  ON DELETE SET NULL")],
        ["name",           "text",         "NOT NULL"],
        ["task_type",      "text",         "NOT NULL"],
        ["base_model",     "text",         "NOT NULL"],
        ["status",         "text",         "DEFAULT 'training'  CHECK (status IN ('training','completed','failed','deployed'))"],
        ["accuracy",       "numeric(5,4)", "NULLABLE — from Engine evaluation"],
        ["f1_score",       "numeric(5,4)", "NULLABLE"],
        ["inference_speed","numeric(8,2)", "NULLABLE — tokens/sec from Engine"],
        ["model_size_mb",  "integer",      "NULLABLE"],
        ["engine_model_id","text",         "Engine-side model UUID (set after training completes)"],
        ["created_at",     "timestamptz",  "DEFAULT now()"],
    ], title="TABLE: trained_models")

    elems += schema_table([
        ["id",           "uuid",         "PRIMARY KEY DEFAULT gen_random_uuid()"],
        ["user_id",      "uuid",         U("NOT NULL  FK → auth.users(id)  ON DELETE CASCADE")],
        ["project_id",   "uuid",         U("NULLABLE  FK → projects(id)  ON DELETE SET NULL")],
        ["name",         "text",         "NOT NULL"],
        ["description",  "text",         ""],
        ["rows",         "integer",      "DEFAULT 0"],
        ["columns",      "integer",      "DEFAULT 0"],
        ["file_size",    "text",         "Human-readable (e.g. '2.4 MB')"],
        ["format",       "text",         "csv | jsonl | parquet | txt"],
        ["quality_score","numeric(5,2)", "0-100 from Engine analysis"],
        ["engine_dataset_id","text",     "Engine-side dataset UUID"],
        ["created_at",   "timestamptz",  "DEFAULT now()"],
    ], title="TABLE: datasets")

    elems += schema_table([
        ["id",              "uuid",    "PRIMARY KEY DEFAULT gen_random_uuid()"],
        ["user_id",         "uuid",    U("NOT NULL  FK → auth.users(id)  ON DELETE CASCADE")],
        ["model_id",        "uuid",    U("FK → trained_models(id)  ON DELETE CASCADE")],
        ["name",            "text",    "NOT NULL"],
        ["status",          "text",    "DEFAULT 'inactive'  CHECK (status IN ('active','inactive','provisioning','error'))"],
        ["url",             "text",    "HTTPS endpoint URL"],
        ["region",          "text",    "Deployment region"],
        ["replicas",        "integer", "DEFAULT 1"],
        ["engine_endpoint_id","text",  "Engine-side endpoint UUID"],
        ["requests_per_min","integer", "Synced from Engine health check"],
        ["avg_latency_ms",  "integer", "Synced from Engine health check"],
        ["created_at",      "timestamptz","DEFAULT now()"],
    ], title="TABLE: deployed_endpoints")

    elems += schema_table([
        ["id",         "uuid",  "PRIMARY KEY DEFAULT gen_random_uuid()"],
        ["user_id",    "uuid",  U("NOT NULL  FK → auth.users(id)  ON DELETE CASCADE")],
        ["name",       "text",  "NOT NULL  User-given label"],
        ["key_prefix", "text",  "First 12 chars for display"],
        ["key_hash",   "text",  "SHA-256 hash of full key  NOT NULL"],
        ["scopes",     "text[]","Array of scopes"],
        ["last_used",  "timestamptz","NULLABLE"],
        ["expires_at", "timestamptz","NULLABLE — null means no expiry"],
        ["created_at", "timestamptz","DEFAULT now()"],
    ], title="TABLE: api_keys")

    elems += schema_table([
        ["id",           "uuid",       "PRIMARY KEY DEFAULT gen_random_uuid()"],
        ["user_id",      "uuid",       U("NOT NULL  FK → auth.users(id)  ON DELETE CASCADE")],
        ["endpoint_id",  "uuid",       U("NULLABLE  FK → deployed_endpoints(id)  ON DELETE SET NULL")],
        ["model_id",     "uuid",       U("NULLABLE  FK → trained_models(id)  ON DELETE SET NULL")],
        ["status_code",  "integer",    "HTTP status of the inference request"],
        ["latency_ms",   "integer",    "Total request latency"],
        ["prompt_tokens","integer",    "Input token count"],
        ["completion_tokens","integer","Output token count"],
        ["created_at",   "timestamptz","DEFAULT now()"],
    ], title="TABLE: api_call_events")

    elems.append(PageBreak())
    return elems


def sec_ui_mapping():
    elems = [H1("9. UI Component  →  API Mapping"), HR(), SP(4)]
    elems.append(P(
        "This table shows which UI components need real API calls and the current status of each. "
        "Components marked with a warning currently use mock/simulated data."
    ))

    status_col = {
        "ok":   U("✓ Real"),
        "mock": U("⚠ Mock"),
        "none": U("✗ Missing"),
    }

    rows = [
        [Paragraph("<b>UI Component</b>", S_BODY),
         Paragraph("<b>Data Source</b>", S_BODY),
         Paragraph("<b>Required API</b>", S_BODY),
         Paragraph("<b>Status</b>", S_BODY)],

        ["Dashboard / StatsCards",      "Supabase projects, models",
         "listProjects(), listModels()", Paragraph(status_col["ok"], S_BODY)],
        ["Dashboard / StatsCards (credits)", "Engine billing",
         "GET /account",                Paragraph(status_col["none"], S_BODY)],
        ["RecentProjects",              "Supabase projects",
         "listProjects()",              Paragraph(status_col["ok"], S_BODY)],
        ["ActivityChart",               "Supabase api_call_events",
         "bucketByTime()",              Paragraph(status_col["ok"], S_BODY)],
        ["NewProject / autoTune",       "Client heuristic",
         "POST /hpo (if use_hpo=true)", Paragraph(status_col["mock"], S_BODY)],
        ["TrainingMonitor / progress",  "useTrainingSimulator.ts",
         "GET /jobs/{id}/stream (SSE)", Paragraph(status_col["mock"], S_BODY)],
        ["TrainingMonitor / logs",      "mockTrainingLog",
         "SSE log events",              Paragraph(status_col["mock"], S_BODY)],
        ["TrainingMonitor / loss curve","mockLossCurve",
         "SSE progress events",         Paragraph(status_col["mock"], S_BODY)],
        ["ProjectDetail / metrics",     "Hash of project.id",
         "GET /models/{id}/evaluate",   Paragraph(status_col["mock"], S_BODY)],
        ["ProjectDetail / versions",    "mockVersionHistory",
         "GET /jobs?project_id={id}",   Paragraph(status_col["mock"], S_BODY)],
        ["Playground / ChatPanel",      "mockResponses + setTimeout",
         "POST /chat/completions",      Paragraph(status_col["mock"], S_BODY)],
        ["Deployment / usage chart",    "Math.random() each render",
         "GET /endpoints/{id}/health",  Paragraph(status_col["mock"], S_BODY)],
        ["Models / trained_models",     "Supabase trained_models",
         "listModels()",                Paragraph(status_col["ok"], S_BODY)],
        ["Analytics / latency",         "Supabase api_call_events",
         "endpointStats()",             Paragraph(status_col["ok"], S_BODY)],
        ["Settings / API Keys",         "Supabase api_keys",
         "listApiKeys(), createApiKey()","  " + status_col["ok"]],
        ["Leaderboard",                 "Supabase trained_models",
         "listModels() (sorted)",       Paragraph(status_col["ok"], S_BODY)],
        ["DatasetInsights",             "Supabase datasets",
         "GET /datasets/{id}/analysis", Paragraph(status_col["none"], S_BODY)],
    ]

    t = Table(rows, colWidths=[4.5*cm, 4*cm, 5.5*cm, 3*cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_NAVY),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
    ]))
    elems += [t, SP(6)]
    elems.append(PageBreak())
    return elems


def sec_errors():
    elems = [H1("10. Error Codes Reference"), HR(), SP(4)]
    elems.append(P(
        "All APIs (Supabase and Engine) return standard HTTP status codes. "
        "Engine errors include a machine-readable code field."
    ))
    errors = [
        ["HTTP", "Engine Code",            "Meaning",                               "Action"],
        ["400",  "INVALID_PARAMS",         "Missing or invalid request parameters", "Check required fields"],
        ["401",  "UNAUTHORIZED",           "Missing or invalid Bearer token",        "Check API key / session"],
        ["403",  "FORBIDDEN",              "Valid token but insufficient scope",      "Request wider-scope key"],
        ["404",  "NOT_FOUND",              "Resource does not exist",               "Verify IDs"],
        ["409",  "CONFLICT",              "Duplicate resource or state conflict",    "Check existing records"],
        ["413",  "PAYLOAD_TOO_LARGE",      "File exceeds 500 MB limit",              "Split or compress dataset"],
        ["422",  "VALIDATION_ERROR",       "Business rule violation",               "See error.details"],
        ["429",  "RATE_LIMITED",           "Too many requests",                      "Backoff and retry"],
        ["402",  "INSUFFICIENT_CREDITS",   "Not enough GPU credits",                "Top up or upgrade plan"],
        ["503",  "ENGINE_UNAVAILABLE",     "No GPU capacity available",              "Retry with exponential backoff"],
        ["500",  "INTERNAL_ERROR",         "Unexpected engine error",               "Contact support with job_id"],
    ]
    t = Table(errors, colWidths=[1.5*cm, 4.5*cm, 6*cm, 5*cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  C_NAVY),
        ("TEXTCOLOR",   (0,0), (-1,0),  C_WHITE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("FONTNAME",    (0,1), (1,-1),  "Courier"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_WHITE, C_LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.3, C_MGRAY),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ]))
    elems += [t, SP(4)]

    elems.append(H2("Retry Strategy"))
    elems.append(P(
        "For 429 and 503 errors, use exponential backoff: "
        "wait 1s, 2s, 4s, 8s, 16s before giving up. "
        "Include a Idempotency-Key header on POST requests to safely retry."
    ))
    elems.append(PageBreak())
    return elems


def sec_mock_migration():
    elems = [H1("11. Mock Data Locations & Migration Guide"), HR(), SP(4)]
    elems.append(P(
        "The following source files contain hardcoded/simulated data that must be replaced "
        "with real API calls before production deployment."
    ))

    items = [
        ("src/components/playground/ChatPanel.tsx",
         "mockResponses array + setTimeout in handleSend()",
         "Replace with fetch('https://api.slmstudio.ai/v1/chat/completions', {...})"),
        ("src/hooks/useTrainingSimulator.ts",
         "setTimeout loop every 2000ms, fake progress saved to Supabase",
         "Replace with EventSource subscription to GET /jobs/{id}/stream"),
        ("src/pages/ProjectDetail.tsx (lines 77-86)",
         "Metrics computed as 85 + ((seed + offset) % 12) from project.id hash",
         "Replace with GET /models/{engine_model_id}/evaluate response"),
        ("src/pages/ProjectDetail.tsx",
         "mockVersionHistory — hardcoded array of fake versions",
         "Replace with GET /jobs?project_id={id} (list training job history)"),
        ("src/pages/TrainingMonitor.tsx",
         "mockPipelineSteps, mockTrainingLog, mockLossCurve, mockComparisonResults",
         "Replace with SSE stream events; epoch hardcoded as '5 / 8'"),
        ("src/data/deploymentMockData.ts (lines 72-77)",
         "mockUsageTimeline — Math.random() called each render",
         "Replace with periodic GET /endpoints/{id}/health calls (poll every 30s)"),
        ("src/components/dashboard/StatsCards.tsx",
         "Missing credits_remaining and plan_tier — no billing API call",
         "Add GET /account call; display credits_remaining and plan_tier"),
        ("src/pages/DatasetInsights.tsx",
         "Dataset quality shown from Supabase metadata only",
         "Fetch full analysis from GET /datasets/{engine_dataset_id}/analysis"),
    ]

    for path, problem, fix in items:
        elems.append(H3(path))
        elems.append(Bul(f"<b>Current:</b>  {problem}"))
        elems.append(Bul(f"<b>Fix:</b>  {fix}"))
        elems.append(SP(4))

    elems += section_note("⚠  After migration, remove src/data/trainingMockData.ts, "
                          "src/data/deploymentMockData.ts, and the mockResponses array in ChatPanel.tsx.")
    return elems


# ─── Assemble story ───────────────────────────────────────────────────────────
def main():
    story = []
    story += cover_page()
    story += toc_page()
    story += sec_overview()
    story += sec_auth()
    story += sec_internal_projects()
    story += sec_internal_models()
    story += sec_internal_datasets()
    story += sec_internal_deployments()
    story += sec_internal_apikeys()
    story += sec_internal_analytics()
    story += sec_engine_intro()
    story += sec_engine_datasets()
    story += sec_engine_training()
    story += sec_engine_sse()
    story += sec_engine_eval()
    story += sec_engine_hpo()
    story += sec_inference()
    story += sec_engine_deployment()
    story += sec_billing()
    story += sec_db_schema()
    story += sec_ui_mapping()
    story += sec_errors()
    story += sec_mock_migration()

    build_doc(story)
    print(f"PDF generated: {OUTPUT}")

if __name__ == "__main__":
    main()
