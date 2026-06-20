"""Allowlist registry of approved read-only copilot tools.

The model and the deterministic router may ONLY select tool names that exist in
this registry. There is no free-form SQL and no arbitrary ORM access: every
entry maps to a vetted Python function that reads through existing services or
the ORM and returns a compact, permission-filtered ``ToolResult``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .tools import blockers, dashboard, finance, invoices, permits, tbcn, tickets, travel_cases


@dataclass(frozen=True)
class CopilotTool:
    name: str
    func: Callable
    description: str
    # Argument names (besides ``user``) the tool accepts from the router.
    arguments: tuple[str, ...] = field(default_factory=tuple)


_TOOLS: tuple[CopilotTool, ...] = (
    CopilotTool("get_my_pending_actions", dashboard.get_my_pending_actions, "List the items needing the current user's attention."),
    CopilotTool("get_dashboard_summary", dashboard.get_dashboard_summary, "Summarize operations and finance, with currency grouping."),
    CopilotTool("search_travel_cases", travel_cases.search_travel_cases, "Search travel cases by case number, employee, badge, project, or route.", ("query",)),
    CopilotTool("get_travel_case_status", travel_cases.get_travel_case_status, "Status, route, permits, tickets, and next action for a travel case.", ("identifier",)),
    CopilotTool("get_ticket_history", tickets.get_ticket_history, "List immutable ticket versions for a travel case.", ("identifier",)),
    CopilotTool("get_permit_status", permits.get_permit_status, "Egypt and Libya permit status for a travel case.", ("identifier",)),
    CopilotTool("search_supplier_invoices", invoices.search_supplier_invoices, "Search supplier invoices by SIR number, supplier invoice number, supplier, or status.", ("query",)),
    CopilotTool("explain_supplier_invoice_status", invoices.explain_supplier_invoice_status, "Explain matching, exceptions, HR approval, and TBCN readiness for an invoice.", ("identifier",)),
    CopilotTool("get_invoice_exceptions", invoices.get_invoice_exceptions, "List invoice lines with open exceptions."),
    CopilotTool("get_ready_for_tbcn", invoices.get_ready_for_tbcn, "List HR-approved invoices ready for TBCN generation."),
    CopilotTool("search_tbcn", tbcn.search_tbcn, "Search TBCNs by number, supplier invoice number, or supplier.", ("query",)),
    CopilotTool("get_unpaid_tbcn_by_currency", finance.get_unpaid_tbcn_by_currency, "Unpaid TBCN finance totals grouped by currency."),
    CopilotTool("get_cost_by_supplier", finance.get_cost_by_supplier, "Cost grouped by supplier and currency."),
    CopilotTool("explain_blocker", blockers.explain_blocker, "Explain why a travel case, invoice, or TBCN cannot move forward.", ("record_type", "record_id")),
)

TOOL_REGISTRY: dict[str, CopilotTool] = {tool.name: tool for tool in _TOOLS}

ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(TOOL_REGISTRY.keys())


def is_allowed_tool(name: str) -> bool:
    return name in ALLOWED_TOOL_NAMES


def get_tool(name: str) -> CopilotTool | None:
    return TOOL_REGISTRY.get(name)


def tool_descriptions() -> list[dict]:
    return [{"name": tool.name, "description": tool.description, "arguments": list(tool.arguments)} for tool in _TOOLS]
