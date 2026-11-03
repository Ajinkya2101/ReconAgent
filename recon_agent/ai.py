"""OCI Generative AI reasoning layer (Gemini 2.5 Pro on OCI by default).

* narrate(): turns ranked rule findings into operational explanations (what
  happened, why, who acts) plus a shift-handoff briefing - one structured
  (JSON-schema) output call per run.
* ask():     a tool-using copilot that answers planner questions ("why does
  MIPS still want 2,000 of 150080?") by querying the reconciled data - it never
  answers from memory.

Numbers always come from the deterministic engine; the model explains, links
and prioritises them. Credentials come from the OCI_* variables in .env (or the
~/.oci/config profile). Without them, or when the call fails, the agent falls
back to rule-generated text, so the pipeline never depends on the LLM.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import oci
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference import models as genai

from . import config
from .detectors import Finding
from .position import SkuPosition

SYSTEM_PROMPT = """You are the Inventory Reconciliation Agent for Bimbo Bakeries USA's Lehigh Valley \
(Breinigsville, PA) bakery. You reconcile three systems:
- MIPS (in-house planning): sales-order lines (Source System BUSS) and supply-planning lines \
(OCloud Region2), production schedule, runs, and inventory applied to demand.
- ODI / Oracle Cloud: finished-goods production results per run (net units ordered, wrapped units, \
expected ending inventory) and batch data.
- WMS: customer orders, ticket allocation, fill rate, shipments, freezer inventory.

Your readers are production planners and shift supervisors across 3 shifts. Write in plain \
operational language: name the SKU, the quantities, the system that is wrong, and the exact correction \
(e.g. "Apply the 1,200 units to the active sales-order lines and reduce the production requirement to \
800"). Use only the numbers you are given or retrieve with tools - never estimate or invent quantities. \
If the data cannot explain a variance, say what additional record (ticket, return, freezer lot, Oracle \
transaction) would resolve it."""

OWNERS = ["Production Planner", "Shift Supervisor", "Warehouse / WMS", "IT / Integration", "Master Data"]

NARRATION_SCHEMA = {
    "type": "object",
    "properties": {
        "shift_briefing": {"type": "string"},
        "top_actions": {"type": "array", "items": {"type": "string"}},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "explanation": {"type": "string"},
                    "root_cause": {"type": "string"},
                    "owner": {"type": "string", "enum": OWNERS},
                },
                "required": ["id", "explanation", "root_cause", "owner"],
            },
        },
    },
    "required": ["shift_briefing", "top_actions", "findings"],
}


class AIError(RuntimeError):
    """The model could not be reached or gave an unusable answer."""


# --------------------------------------------------------------------------
# OCI client
# --------------------------------------------------------------------------

def _env_config() -> dict | None:
    e = os.environ
    if not (e.get("OCI_USER_OCID") and e.get("OCI_TENANCY_OCID") and e.get("OCI_FINGERPRINT")):
        return None
    cfg = {"user": e["OCI_USER_OCID"], "tenancy": e["OCI_TENANCY_OCID"],
           "fingerprint": e["OCI_FINGERPRINT"], "region": config.OCI_REGION}
    if e.get("OCI_PRIVATE_KEY"):
        # A PEM kept on one line in .env arrives with literal "\n" sequences.
        cfg["key_content"] = e["OCI_PRIVATE_KEY"].replace("\\n", "\n")
    elif e.get("OCI_PRIVATE_KEY_PATH"):
        cfg["key_file"] = e["OCI_PRIVATE_KEY_PATH"]
    else:
        return None
    if e.get("OCI_PRIVATE_KEY_PASSPHRASE"):
        cfg["pass_phrase"] = e["OCI_PRIVATE_KEY_PASSPHRASE"]
    return cfg


def _oci_config() -> dict:
    cfg = _env_config()
    if cfg is None:
        cfg = oci.config.from_file(profile_name=os.environ.get("OCI_CONFIG_PROFILE") or "DEFAULT")
    oci.config.validate_config(cfg)
    return cfg


def credentials_available() -> bool:
    if not config.AI_ENABLED or not config.OCI_COMPARTMENT_ID:
        return False
    return _env_config() is not None or Path(oci.config.DEFAULT_LOCATION).expanduser().exists()


@lru_cache(maxsize=1)
def _client() -> GenerativeAiInferenceClient:
    endpoint = config.OCI_SERVICE_ENDPOINT or \
        f"https://inference.generativeai.{config.OCI_REGION}.oci.oraclecloud.com"
    return GenerativeAiInferenceClient(_oci_config(), service_endpoint=endpoint,
                                       retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
                                       timeout=(10, config.OCI_TIMEOUT_SECONDS))


def _chat(messages: list, **request) -> genai.ChatChoice:
    chat_request = genai.GenericChatRequest(api_format=genai.BaseChatRequest.API_FORMAT_GENERIC,
                                            messages=messages, **request)
    details = genai.ChatDetails(compartment_id=config.OCI_COMPARTMENT_ID,
                                serving_mode=genai.OnDemandServingMode(model_id=config.OCI_MODEL_ID),
                                chat_request=chat_request)
    try:
        choices = _client().chat(details).data.chat_response.choices or []
    except (oci.exceptions.ServiceError, oci.exceptions.RequestException, oci.exceptions.ClientError,
            oci.exceptions.InvalidConfig, ValueError, OSError) as e:
        raise AIError(f"OCI Generative AI call failed: {e}") from e
    if not choices:
        raise AIError("OCI Generative AI returned no choices")
    return choices[0]


def _text(choice: genai.ChatChoice) -> str:
    return "".join(c.text for c in (choice.message.content or []) if getattr(c, "text", None)).strip()


def _system(text: str):
    return genai.SystemMessage(content=[genai.TextContent(text=text)])


def _user(text: str):
    return genai.UserMessage(content=[genai.TextContent(text=text)])


def model_label() -> str:
    return f"OCI Generative AI ({config.OCI_MODEL_ID})"


def _finding_payload(f: Finding) -> dict:
    return {"id": f.id, "rule": f.title, "scenario": f.scenario, "sku": f.sku, "item": f.description,
            "severity": f.severity, "units": round(f.units), "dollar_exposure": round(f.dollars),
            "systems": f.systems, "evidence": f.evidence, "rule_recommendation": f.recommendation}


# --------------------------------------------------------------------------
# Narration
# --------------------------------------------------------------------------

def narrate(findings: list[Finding], kpis: dict, limit: int = 40) -> dict:
    """Return {"shift_briefing", "top_actions", "by_id": {id: {...}}, "source"}."""
    top = findings[:limit]
    if not credentials_available():
        return offline_narration(top, kpis)
    try:
        return narrate_with_model(top, kpis)
    except AIError as e:
        print(f"{e} - using rule-generated narratives.")
        return offline_narration(top, kpis)


def narrate_with_model(top: list[Finding], kpis: dict) -> dict:
    """One structured call; raises AIError when the model cannot produce usable output."""
    prompt = (
        "Here is today's reconciliation run. KPIs:\n" + json.dumps(kpis, default=str) +
        "\n\nFindings, highest priority first:\n" + json.dumps([_finding_payload(f) for f in top], default=str) +
        "\n\nFor every finding, write a 2-3 sentence explanation a supervisor can act on, the most likely "
        "root cause (which system handoff failed and why), and the owner. Where findings on the same SKU "
        "or run are connected (e.g. a first-line allocation issue and the resulting carry-over), say so. "
        "Then write a shift-handoff briefing (under 150 words) and the 3-5 actions that remove the most "
        "over-production and inventory risk today. Return one entry per finding id, using the ids exactly "
        "as given.")
    choice = _chat([_system(SYSTEM_PROMPT), _user(prompt)],
                   response_format=genai.JsonSchemaResponseFormat(json_schema=genai.ResponseJsonSchema(
                       name="reconciliation_narration", schema=NARRATION_SCHEMA, is_strict=True)),
                   max_tokens=config.OCI_MAX_TOKENS)
    if choice.finish_reason not in (None, "stop", "STOP", "COMPLETE"):
        raise AIError(f"model stopped with '{choice.finish_reason}'")
    try:
        data = json.loads(_text(choice))
        by_id = {x["id"]: x for x in data["findings"] if x.get("owner") in OWNERS}
        briefing, actions = data["shift_briefing"], data["top_actions"]
    except (ValueError, KeyError, TypeError) as e:
        raise AIError(f"unusable narration from the model: {e}") from e
    return {"shift_briefing": briefing, "top_actions": actions, "by_id": by_id, "source": model_label()}


def offline_narration(findings: list[Finding], kpis: dict) -> dict:
    owner_by_rule = {"R01": OWNERS[0], "R02": OWNERS[0], "R03": OWNERS[0], "R04": OWNERS[0],
                     "R05": OWNERS[1], "R06": OWNERS[0], "R07": OWNERS[2], "R08": OWNERS[0],
                     "R09": OWNERS[3], "R10": OWNERS[2], "R11": OWNERS[4], "R12": OWNERS[2],
                     "R13": OWNERS[3], "R14": OWNERS[1], "R15": OWNERS[3], "R16": OWNERS[3],
                     "R17": OWNERS[1], "R18": OWNERS[3], "R19": OWNERS[2]}
    by_id = {f.id: {"id": f.id, "explanation": f.recommendation, "root_cause": f.scenario,
                    "owner": owner_by_rule[f.rule]} for f in findings}
    high = [f for f in findings if f.severity == "High"]
    briefing = (
        f"{kpis['findings']} reconciliation exceptions for sales date {kpis['sales_date']} "
        f"({len(high)} high severity), ${kpis['dollar_exposure']:,.0f} of product at risk. "
        f"{kpis['overproduction_risk_units']:,.0f} units of over-production risk from inventory applied "
        f"to single order lines or recovery runs; {kpis['carryover_units']:,.0f} surplus units must be "
        f"carried to the next sales day; {kpis['untracked_supply_units']:,.0f} units were shipped from "
        f"stock MIPS cannot see.")
    return {"shift_briefing": briefing, "top_actions": [f"{f.sku or f.description}: {f.recommendation}"
                                                       for f in findings[:5]],
            "by_id": by_id, "source": "Rule engine (offline - set the OCI_* credentials in .env for AI narratives)"}


# --------------------------------------------------------------------------
# Copilot (tool-using agent)
# --------------------------------------------------------------------------

TOOLS = [
    genai.FunctionDefinition(
        name="search_items",
        description="Find SKUs whose description or number contains the text (case-insensitive).",
        parameters={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string", "description": 'Part of an item description or SKU number, e.g. "brioche" or "150080".'}}}),
    genai.FunctionDefinition(
        name="get_sku_position",
        description="Reconciled MIPS/ODI/WMS position for one SKU, with every MIPS line and ODI run.",
        parameters={"type": "object", "required": ["sku"], "properties": {
            "sku": {"type": "string", "description": 'Oracle FG number, e.g. "150080".'}}}),
    genai.FunctionDefinition(
        name="list_findings",
        description="List reconciliation findings, highest priority first. All filters optional.",
        parameters={"type": "object", "properties": {
            "rule": {"type": "string", "description": 'Rule code R01-R17 (e.g. "R05" recovery runs, "R07" untracked/freezer supply).'},
            "severity": {"type": "string", "enum": ["High", "Medium", "Low"]},
            "sku": {"type": "string", "description": "Restrict to one SKU."},
            "limit": {"type": "integer", "description": "Maximum findings to return (default 20)."}}}),
    genai.FunctionDefinition(
        name="plant_summary",
        description="Totals by rule: number of findings, units and dollar exposure.",
        parameters={"type": "object", "properties": {}}),
]


def run_tool(name: str, args: dict, positions: dict[str, SkuPosition], findings: list[Finding]) -> str:
    """Tools read only this run's reconciled data store."""
    if name == "search_items":
        t = str(args.get("text", "")).lower()

        def names(p: SkuPosition) -> str:
            # Each system abbreviates differently (MIPS "BRIOCHE" vs WMS "BRI").
            return " | ".join({p.description, *(l.description for l in p.lines),
                               *(r.description for r in p.runs)}).lower()

        hits = [{"sku": p.sku, "description": p.description} for p in positions.values()
                if t in names(p) or t in p.sku]
        return json.dumps(hits[:25])

    if name == "get_sku_position":
        sku = str(args.get("sku", ""))
        p = positions.get(sku.strip().lstrip("0"))
        if not p:
            return json.dumps({"error": f"SKU {sku} not found; use search_items"})
        return json.dumps({
            "position": p.to_row(),
            "mips_lines": [{"qty": l.order_qty, "source": l.source_system, "moved": l.order_moved,
                            "offset_sales_date": l.offset_sales_date, "container": l.container_code,
                            "last_modified": l.last_modified} for l in p.lines],
            "odi_runs": [{"formula": r.formula_type, "line": r.line, "shift": r.shift, "start": r.start,
                          "net_ordered": r.net_units_ordered, "wrapped": r.wrapped_units,
                          "expected_ending_inventory": r.expected_ending_inventory} for r in p.runs],
            "wms_orders": [vars(o) for o in p.wms.orders] if p.wms else [],
            "findings": [f.id for f in findings if f.sku == p.sku],
        }, default=str)

    if name == "list_findings":
        rule, severity, sku = (str(args.get(k) or "") for k in ("rule", "severity", "sku"))
        out = [f for f in findings
               if (not rule or f.rule == rule.upper()) and (not severity or f.severity == severity.title())
               and (not sku or f.sku == sku.strip())]
        return json.dumps([_finding_payload(f) for f in out[:int(args.get("limit") or 20)]], default=str)

    if name == "plant_summary":
        summary: dict[str, dict] = {}
        for f in findings:
            s = summary.setdefault(f.rule, {"rule": f.title, "count": 0, "units": 0, "dollars": 0})
            s["count"] += 1
            s["units"] += round(abs(f.units))
            s["dollars"] += round(f.dollars)
        return json.dumps(summary)

    return json.dumps({"error": f"unknown tool {name}"})


def ask(question: str, positions: dict[str, SkuPosition], findings: list[Finding], max_turns: int = 6) -> str:
    """Answer from tool results only. Raises AIError when the model is unavailable."""
    if not credentials_available():
        raise AIError("OCI credentials are not configured. Set the OCI_* values in .env.")
    messages = [_system(SYSTEM_PROMPT + "\nAlways look the data up with the tools before you answer."),
                _user(question)]
    for turn in range(max_turns):
        # Gemini on OCI rarely calls a tool unprompted, so the first turn requires one.
        choice = _chat(messages, tools=TOOLS, max_tokens=config.OCI_MAX_TOKENS,
                       tool_choice=genai.ToolChoiceRequired() if turn == 0 else genai.ToolChoiceAuto())
        calls = choice.message.tool_calls or []
        if not calls:
            text = _text(choice)
            if not text:
                raise AIError(f"empty answer (finish reason '{choice.finish_reason}')")
            return text
        messages.append(genai.AssistantMessage(content=choice.message.content, tool_calls=calls))
        for call in calls:
            try:
                args = json.loads(call.arguments or "{}")
            except ValueError:
                args = {}
            result = run_tool(call.name, args, positions, findings)
            messages.append(genai.ToolMessage(tool_call_id=call.id, content=[genai.TextContent(text=result)]))
    raise AIError(f"no answer after {max_turns} tool rounds")
