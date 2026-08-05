"""TravelOps Copilot orchestration: safe intent routing, tools, and answers.

This module is read-only. It never mutates data, never runs free-form SQL, and
never lets the model invent queries. The model can only choose tool names from
the allowlist in ``tool_registry``; everything else is deterministic.
"""
from __future__ import annotations

import json
import re
from urllib import error, request

from django.conf import settings

from apps.audit_logs.services import create_audit_log

from .prompts import (
    ANSWER_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    answer_user_prompt,
    router_user_prompt,
)
from .tool_registry import get_tool, is_allowed_tool, tool_descriptions

SAFETY_NOTICE = {
    "en": "Read-only answer. No records were changed.",
    "ar": "إجابة للقراءة فقط. لم يتم تغيير أي سجلات.",
}

MAX_MESSAGE_LENGTH = 1000

# Arabic Unicode ranges used for lightweight language detection.
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")

# Action verbs the copilot must refuse in this read-only phase (English + Arabic).
ACTION_PATTERNS = (
    "create", "submit", "assign", "confirm", "approve", "generate", "issue",
    "send to finance", "mark paid", "mark as paid", "mark accepted", "accept ",
    "pay ", "delete", "remove", "edit", "update", "change the", "cancel ",
    "أنشئ", "انشئ", "أنشئي", "قدّم", "قدم", "أرسل", "ارسل", "اعتمد", "وافق",
    "ولّد", "ولد", "أصدر", "اصدر", "احذف", "امسح", "عدّل", "عدل", "ادفع",
    "خصّص", "خصص", "أكّد", "أكد", "اكد", "ألغِ", "ألغ", "الغ",
)

# If any of these inquiry words are present, the message is treated as a
# question (route to a read tool), not an action request (English + Arabic).
INQUIRY_WORDS = (
    "why", "what", "which", "who", "when", "show", "list", "find", "search",
    "is ", "are ", "can ", "does", "do ", "status", "explain", "summar",
    "how many", "tell me", "any ", "view",
    "ما", "ماذا", "لماذا", "أي", "أين", "متى", "من ", "هل", "اعرض", "أظهر",
    "اظهر", "ابحث", "كم", "اشرح", "لخص", "لخّص", "ملخص",
)

# Identifier patterns for the controlled record vocabulary.
TRV_RE = re.compile(r"TRV-[A-Z]{2}-\d{4}-\d{4,8}", re.IGNORECASE)
SIR_RE = re.compile(r"SIR-[A-Z]{2}-\d{4}-\d{4,8}", re.IGNORECASE)
TBCN_RE = re.compile(r"TBCN-[A-Z]{2}-\d{4}-\d{4,8}", re.IGNORECASE)

ROLE_SUGGESTED_QUESTIONS = {
    "en": {
        "HR": [
            "Which invoices are ready for TBCN?",
            "Which invoices have exceptions?",
            "Show travel cases pending booking.",
        ],
        "BookingOfficer": [
            "Show travel cases pending booking.",
            "Show my pending actions.",
            "Show ticket history for a case.",
        ],
        "Finance": [
            "Which TBCNs are unpaid?",
            "Show cost by supplier grouped by currency.",
            "Show my pending actions.",
        ],
    },
    "ar": {
        "HR": [
            "أي فواتير جاهزة لإصدار TBCN؟",
            "أي فواتير بها استثناءات؟",
            "اعرض حالات السفر بانتظار الحجز.",
        ],
        "BookingOfficer": [
            "اعرض حالات السفر بانتظار الحجز.",
            "اعرض مهامي المعلقة.",
            "اعرض تاريخ التذاكر لحالة سفر.",
        ],
        "Finance": [
            "أي إشعارات TBCN غير مدفوعة؟",
            "اعرض التكلفة حسب المورد لكل عملة.",
            "اعرض مهامي المعلقة.",
        ],
    },
}

DEFAULT_SUGGESTED_QUESTIONS = {
    "en": [
        "What needs my attention today?",
        "Which invoices have exceptions?",
        "Which TBCNs are unpaid?",
        "Show cost by supplier grouped by currency.",
    ],
    "ar": [
        "ما الذي يحتاج انتباهي اليوم؟",
        "أي فواتير بها استثناءات؟",
        "أي إشعارات TBCN غير مدفوعة؟",
        "اعرض التكلفة حسب المورد لكل عملة.",
    ],
}

CONTEXT_SUGGESTED_QUESTIONS = {
    "en": {
        "travelCaseDetail": ["Summarize this case.", "What is the next action?", "Are permits complete?", "Show ticket history."],
        "invoiceMatching": ["Why is this invoice blocked?", "Show exceptions.", "Is this ready for TBCN?"],
        "tbcnDetail": ["Can this be sent to Finance?", "Has this been paid?", "Is the PDF available?"],
    },
    "ar": {
        "travelCaseDetail": ["لخّص هذه الحالة.", "ما هو الإجراء التالي؟", "هل التصاريح مكتملة؟", "اعرض تاريخ التذاكر."],
        "invoiceMatching": ["لماذا هذه الفاتورة محظورة؟", "اعرض الاستثناءات.", "هل هذه جاهزة لإصدار TBCN؟"],
        "tbcnDetail": ["هل يمكن إرسالها إلى المالية؟", "هل تم دفعها؟", "هل ملف PDF متاح؟"],
    },
}

# Screen -> manual action link used when refusing an action request.
SCREEN_ACTION_LINKS = {
    "invoiceMatching": "/supplier-invoices",
    "tbcnDetail": "/tbcn",
    "travelCaseDetail": "/travel-requests",
    "bookingDesk": "/booking-desk",
    "dashboard": "/dashboard",
}


def detect_language(message: str) -> str:
    """Return 'ar' when the message contains Arabic script, else 'en'."""
    return "ar" if ARABIC_RE.search(message or "") else "en"


def run_copilot_chat(user, message: str, context: dict | None = None) -> dict:
    context = context or {}
    message = (message or "").strip()
    lang = detect_language(message)
    if not message:
        return _clarification(user, context, lang)
    message = message[:MAX_MESSAGE_LENGTH]

    used_openai = False

    # 1) Refuse mutation requests with a safe explanation and a manual link.
    if _is_action_request(message):
        response = _action_refusal(message, context, lang)
        _log_usage(user, message, response["_tools"], context, used_openai)
        return _public_response(response)

    # 2) Deterministic keyword routing first.
    tool_name, arguments = _route_deterministic(message, context)

    # 3) Optional OpenAI tool selection only when deterministic routing fails.
    if not tool_name:
        selected = _openai_select_tool(message)
        if selected:
            used_openai = True
            tool_name, arguments = selected

    if not tool_name:
        response = _clarification(user, context, lang)
        _log_usage(user, message, response["_tools"], context, used_openai)
        return _public_response(response)

    tool = get_tool(tool_name)
    if tool is None or not is_allowed_tool(tool_name):
        response = _clarification(user, context, lang)
        _log_usage(user, message, response["_tools"], context, used_openai)
        return _public_response(response)

    safe_arguments = {key: value for key, value in arguments.items() if key in tool.arguments}
    tool_result = tool.func(user, **safe_arguments)

    answer = tool_result["summary"]
    openai_answer = _openai_compose_answer(message, tool_result)
    if openai_answer:
        used_openai = True
        answer = openai_answer

    response = {
        "answer": answer,
        "cards": tool_result["cards"],
        "suggested_questions": _suggested_questions(user, context, lang),
        "safety_notice": SAFETY_NOTICE[lang],
        "_tools": [tool_name],
    }
    _log_usage(user, message, response["_tools"], context, used_openai)
    return _public_response(response)


def _public_response(response: dict) -> dict:
    return {key: value for key, value in response.items() if not key.startswith("_")}


def _is_action_request(message: str) -> bool:
    lowered = message.lower()
    if any(word in lowered for word in INQUIRY_WORDS):
        return False
    return any(pattern in lowered for pattern in ACTION_PATTERNS)


ACTION_REFUSAL_ANSWER = {
    "en": (
        "I can't perform actions in this read-only phase. I can explain status and next steps, "
        "but creating, approving, generating, sending, or paying must be done by a person on the relevant screen. "
        "Open the screen below to perform this action manually."
    ),
    "ar": (
        "لا يمكنني تنفيذ الإجراءات في هذه المرحلة المخصّصة للقراءة فقط. يمكنني شرح الحالة والخطوات التالية، "
        "أمّا الإنشاء أو الاعتماد أو التوليد أو الإرسال أو الدفع فيجب أن يقوم به شخص على الشاشة المعنية. "
        "افتح الشاشة أدناه لتنفيذ هذا الإجراء يدويًا."
    ),
}

ACTION_REFUSAL_CARD = {
    "en": {"title": "Action required on screen", "subtitle": "TravelOps Copilot is read-only in this phase."},
    "ar": {"title": "الإجراء مطلوب على الشاشة", "subtitle": "مساعد TravelOps للقراءة فقط في هذه المرحلة."},
}

CLARIFICATION_ANSWER = {
    "en": (
        "I'm not sure what you're asking. I can help with pending actions, travel case status, "
        "ticket history, permits, supplier invoices, invoice exceptions, TBCN readiness, unpaid TBCNs by currency, "
        "and why a record is blocked. Try one of the suggested questions."
    ),
    "ar": (
        "لست متأكدًا مما تسأل عنه. يمكنني المساعدة في المهام المعلقة، وحالة طلب السفر، وتاريخ التذاكر، والتصاريح، "
        "وفواتير المورّدين، واستثناءات الفواتير، وجاهزية TBCN، وإشعارات TBCN غير المدفوعة حسب العملة، "
        "وسبب توقّف أي سجل. جرّب أحد الأسئلة المقترحة."
    ),
}


def _action_refusal(message: str, context: dict, lang: str = "en") -> dict:
    link = SCREEN_ACTION_LINKS.get(context.get("screen"), "/dashboard")
    record_id = context.get("record_id")
    record_type = (context.get("record_type") or "").upper()
    if record_id and record_type == "SUPPLIER_INVOICE":
        link = f"/supplier-invoices/{record_id}/matching"
    elif record_id and record_type == "TBCN":
        link = f"/tbcn/{record_id}"
    elif record_id and record_type == "TRAVEL_CASE":
        link = f"/travel-requests/{record_id}"

    return {
        "answer": ACTION_REFUSAL_ANSWER[lang],
        "cards": [
            {
                "type": "SUMMARY",
                "title": ACTION_REFUSAL_CARD[lang]["title"],
                "subtitle": ACTION_REFUSAL_CARD[lang]["subtitle"],
                "status": "READ_ONLY",
                "url": link,
            }
        ],
        "suggested_questions": _suggested_questions(None, context, lang),
        "safety_notice": SAFETY_NOTICE[lang],
        "_tools": [],
    }


def _clarification(user, context: dict, lang: str = "en") -> dict:
    return {
        "answer": CLARIFICATION_ANSWER[lang],
        "cards": [],
        "suggested_questions": _suggested_questions(user, context, lang),
        "safety_notice": SAFETY_NOTICE[lang],
        "_tools": [],
    }


def _extract_identifier(message: str, context: dict) -> str:
    for pattern in (TRV_RE, SIR_RE, TBCN_RE):
        found = pattern.search(message)
        if found:
            return found.group(0)
    if context.get("record_id"):
        return str(context["record_id"])
    return ""


def _route_deterministic(message: str, context: dict) -> tuple[str | None, dict]:
    lowered = message.lower()
    identifier = _extract_identifier(message, context)

    record_type = (context.get("record_type") or "").upper()

    def has(*keywords: str) -> bool:
        return any(keyword in lowered for keyword in keywords)

    # Pending / attention
    if has("pending", "needs my attention", "need my attention", "attention today", "my action", "my task", "what should i do",
           "معلق", "انتباه", "اهتمام", "مهامي", "ماذا يجب", "ما يجب"):
        return "get_my_pending_actions", {}

    # Unpaid TBCNs by currency
    if has("unpaid", "غير مدفوع", "غير مدفوعة", "لم تدفع", "لم يتم دفع") and has("tbcn", "finance", "currency", "billing", "عملة", "إشعار", "تسوية", "مالية"):
        return "get_unpaid_tbcn_by_currency", {}
    if "unpaid tbcn" in lowered or ("unpaid" in lowered and "by currency" in lowered):
        return "get_unpaid_tbcn_by_currency", {}

    # Cost by supplier
    if has("cost by supplier", "cost per supplier", "spend by supplier", "supplier cost", "cost grouped by supplier",
           "التكلفة حسب المورد", "تكلفة المورد", "حسب المورد", "التكلفة لكل مورد"):
        return "get_cost_by_supplier", {}

    # Blocker explanation
    if has("blocked", "blocker", "stuck", "cannot move", "can't move", "not moving", "why is", "why can",
           "محظور", "محظورة", "متوقف", "متوقفة", "عالق", "عالقة", "لا يتحرك", "لماذا"):
        return "explain_blocker", {"record_type": record_type, "record_id": identifier}

    # Ready for TBCN
    if has("ready for tbcn", "ready for billing", "invoices ready", "ready to generate", "جاهز", "جاهزة"):
        return "get_ready_for_tbcn", {}

    # Invoice exceptions
    if has("exception", "mismatch", "difference", "استثناء", "استثناءات", "فرق", "اختلاف") and not identifier.upper().startswith("TBCN"):
        return "get_invoice_exceptions", {}

    # Ticket history
    if has("ticket history", "ticket version", "ticket versions", "history of tickets", "show ticket",
           "تاريخ التذكرة", "تاريخ التذاكر", "نسخ التذكرة", "التذاكر", "تذكرة"):
        return "get_ticket_history", {"identifier": identifier}

    # Permit status
    if has("permit", "تصريح", "تصاريح"):
        return "get_permit_status", {"identifier": identifier}

    # TBCN lookups
    if identifier.upper().startswith("TBCN") or has("tbcn", "billing confirmation", "تسوية", "إشعار"):
        return "search_tbcn", {"query": identifier}

    # Supplier invoice status vs search
    if identifier.upper().startswith("SIR"):
        return "explain_supplier_invoice_status", {"identifier": identifier}
    if has("invoice", "فاتورة", "فواتير"):
        if record_type == "SUPPLIER_INVOICE" and identifier and has("status", "ready", "blocked", "approved", "explain", "حالة", "جاهز", "اشرح"):
            return "explain_supplier_invoice_status", {"identifier": identifier}
        return "search_supplier_invoices", {"query": _search_text(message)}

    # Travel case status vs search
    if identifier.upper().startswith("TRV") or record_type == "TRAVEL_CASE":
        if has("summar", "status", "next action", "detail", "لخص", "لخّص", "ملخص", "حالة", "الإجراء التالي", "التالي"):
            return "get_travel_case_status", {"identifier": identifier}
        if identifier:
            return "get_travel_case_status", {"identifier": identifier}
    if has("travel case", "case ", "cases ", "trip", "booking", "حالة سفر", "حالات سفر", "طلب سفر", "رحلة", "سفر", "حجز", "الحجز"):
        if has("status", "summar", "next action", "حالة", "لخص", "ملخص") and identifier:
            return "get_travel_case_status", {"identifier": identifier}
        return "search_travel_cases", {"query": _search_text(message)}

    # Dashboard / overview
    if has("dashboard", "overview", "summary", "summarize", "how are we", "snapshot", "لوحة", "نظرة عامة", "ملخص عام"):
        return "get_dashboard_summary", {}

    # Context-aware fallback: a question asked while viewing a specific record.
    if identifier:
        if record_type == "TBCN":
            return "explain_blocker", {"record_type": "TBCN", "record_id": identifier}
        if record_type == "SUPPLIER_INVOICE":
            return "explain_supplier_invoice_status", {"identifier": identifier}
        if record_type == "TRAVEL_CASE":
            return "get_travel_case_status", {"identifier": identifier}

    return None, {}


_ARABIC_STOPWORDS = (
    "اعرض", "أظهر", "اظهر", "ابحث", "عن", "كل", "قائمة", "من", "عن",
    "حالة", "حالات", "سفر", "رحلة", "فاتورة", "فواتير", "مورد", "المورد", "لكل", "هل", "ما",
)


def _search_text(message: str) -> str:
    """Strip common command words so search queries are tighter (English + Arabic)."""
    cleaned = re.sub(
        r"\b(show|list|find|search|me|all|the|for|travel|case|cases|invoice|invoices|supplier|please|status|of)\b",
        " ",
        message,
        flags=re.IGNORECASE,
    )
    for stopword in _ARABIC_STOPWORDS:
        cleaned = cleaned.replace(stopword, " ")
    cleaned = re.sub(r"[?.!؟،]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _suggested_questions(user, context: dict, lang: str = "en") -> list[str]:
    screen = context.get("screen")
    context_questions = CONTEXT_SUGGESTED_QUESTIONS[lang]
    if screen in context_questions:
        return context_questions[screen]
    roles = set(user.groups.values_list("name", flat=True)) if user and getattr(user, "is_authenticated", False) else set()
    for role, questions in ROLE_SUGGESTED_QUESTIONS[lang].items():
        if role in roles:
            return questions
    return DEFAULT_SUGGESTED_QUESTIONS[lang]


def _log_usage(user, message: str, tools: list[str], context: dict, used_openai: bool) -> None:
    create_audit_log(
        user=user if getattr(user, "is_authenticated", False) else None,
        action="COPILOT_QUESTION_ASKED",
        entity_type="COPILOT",
        entity_id=getattr(user, "id", "anonymous") or "anonymous",
        metadata={
            "message_length": len(message or ""),
            "selected_tools": tools,
            "screen": context.get("screen"),
            "record_type": context.get("record_type"),
            "language": detect_language(message),
            "used_openai": used_openai,
        },
    )


# --- OpenAI integration (optional, safe fallback) ----------------------------


def _openai_enabled() -> bool:
    return bool(getattr(settings, "OPENAI_API_KEY", ""))


def _openai_chat(system_prompt: str, user_prompt: str, *, force_json: bool) -> str | None:
    """Call the chat completions API. Returns content or None on any failure.

    Never logs or returns the API key, and only sends the compact prompts it is
    given. Any network, timeout, or parsing error degrades to the deterministic
    fallback by returning None.
    """
    if not _openai_enabled():
        return None
    payload = {
        "model": settings.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    http_request = request.Request(
        f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=settings.OPENAI_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    content = body.get("choices", [{}])[0].get("message", {}).get("content")
    return content or None


def _openai_select_tool(message: str) -> tuple[str, dict] | None:
    content = _openai_chat(ROUTER_SYSTEM_PROMPT, router_user_prompt(message, tool_descriptions()), force_json=True)
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    tool_name = parsed.get("tool")
    if not tool_name or not is_allowed_tool(tool_name):
        return None
    arguments = parsed.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    return tool_name, arguments


def _openai_compose_answer(message: str, tool_result: dict) -> str | None:
    if not _openai_enabled():
        return None
    content = _openai_chat(
        ANSWER_SYSTEM_PROMPT,
        answer_user_prompt(message, tool_result["tool"], tool_result["summary"], tool_result["data"]),
        force_json=False,
    )
    if not content:
        return None
    return content.strip()
