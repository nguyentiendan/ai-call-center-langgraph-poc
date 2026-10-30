"""Tool names the Phase 2 loader and policy gateway share. YAML cannot extend this set."""

PROTECTED_TOOLS = frozenset({"get_billing_info", "get_current_plan"})
PUBLIC_TOOLS = frozenset({"search_faq", "search_plan_catalog"})
CONTROLLED_TOOLS = frozenset({"request_handoff"})
KNOWN_TOOLS = PUBLIC_TOOLS | PROTECTED_TOOLS | CONTROLLED_TOOLS
