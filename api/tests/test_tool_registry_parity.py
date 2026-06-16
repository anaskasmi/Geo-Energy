"""Shared agent-tool REGISTRY parity guard (GEO-42).

ONE source of truth (app.agent_tools.REGISTRY) defines the shared tool contract + the text surface;
the voice surface is an enforced mirror in the frontend tree (voiceTools.json + voiceExecutors.ts).
These checks FAIL CI if the two surfaces drift:

  1. the frontend mirror files exist (hard-fail, never skip — drift must never pass silently);
  2. the LIVE Gemini tool schema (from the @agent.tool wrappers) matches REGISTRY — names, property
     sets, required sets, and enums — so a wrapper drifting from the registry fails;
  3. voiceTools.json's tool names == the registry's voice tools + the VOICE_ONLY allowlist;
  4. the VoiceToolName union in voiceExecutors.ts == voiceTools.json's names (tsc separately ties the
     union to the executor map, so a missing executor fails `npm run build`);
  5. for shared tools the voice param-name set mirrors the registry, and every voice use_case enum
     equals scoring.SUPPORTED_USE_CASES;
  + PHASE_LABELS in agentClient.ts covers every registry phase.

The voice-side missing-implementation guard is compile-time (Record<VoiceToolName, VoiceExecutor> in
voiceExecutors.ts). This test is hermetic: no network, no new dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app import agent as agent_mod
from app import agent_tools as at
from app import scoring
from app.main import app

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "frontend" / "src" / "agent"
VOICE_TOOLS_JSON = AGENT_DIR / "voiceTools.json"
VOICE_EXECUTORS_TS = AGENT_DIR / "voiceExecutors.ts"
AGENT_CLIENT_TS = AGENT_DIR / "agentClient.ts"


# --- helpers ----------------------------------------------------------------------------------
def _text_tools() -> dict[str, at.ToolDef]:
    return {t.name: t for t in at.REGISTRY if "text" in t.surfaces}


def _voice_tool_names() -> set[str]:
    """The names voiceTools.json MUST contain: registry voice tools + the voice-only allowlist."""
    return {t.name for t in at.REGISTRY if "voice" in t.surfaces} | set(at.VOICE_ONLY_TOOLS)


def _props(schema: dict) -> set[str]:
    return set((schema or {}).get("properties", {}) or {})


def _required(schema: dict) -> set[str]:
    return set((schema or {}).get("required", []) or [])


def _find_enum(node, root: dict, depth: int = 0) -> set[str] | None:
    """Enum values of a property schema, resolving $ref into root['$defs'] + anyOf/allOf wrappers.

    pydantic-ai may hoist a Literal type alias (UseCase) into `$defs` and reference it, or wrap an
    Optional in anyOf — so a naive top-level `enum` lookup misses it. This follows those one or two
    hops so the check still verifies the use_case values.
    """
    if depth > 5 or not isinstance(node, dict):
        return None
    if "enum" in node:
        return set(node["enum"])
    if "$ref" in node:
        ref = str(node["$ref"]).split("/")[-1]
        return _find_enum(root.get("$defs", {}).get(ref, {}), root, depth + 1)
    for key in ("anyOf", "allOf", "oneOf"):
        for member in node.get(key, []) or []:
            found = _find_enum(member, root, depth + 1)
            if found:
                return found
    return None


@pytest.fixture
def client(scored_data_dir):
    with TestClient(app) as c:
        yield c


# --- 1) the voice mirror must be present (no silent skip) -------------------------------------
def test_frontend_mirror_files_exist():
    for path in (VOICE_TOOLS_JSON, VOICE_EXECUTORS_TS, AGENT_CLIENT_TS):
        assert path.is_file(), f"voice mirror file missing: {path} (drift must not pass silently)"


# --- 2) the LIVE Gemini schema must match the registry (kills the registry-vs-wrapper fork) ---
def test_text_live_schema_matches_registry(client):
    captured: dict[str, dict] = {}

    async def capture(messages: list, info: AgentInfo):
        captured.update({t.name: t.parameters_json_schema for t in info.function_tools})
        yield "ok"  # plain text, no tool call -> the run ends immediately

    with agent_mod.get_agent().override(model=FunctionModel(stream_function=capture)):
        resp = client.post("/api/agent", json={"message": "hi"})
    assert resp.status_code == 200, resp.text
    assert captured, "no live tool schemas were captured from the agent"

    registry = _text_tools()
    assert set(captured) == set(registry), (
        "registered @agent.tool wrappers diverge from REGISTRY text tools: "
        f"live={sorted(captured)} registry={sorted(registry)}"
    )
    for name, tool in registry.items():
        live = captured[name]
        assert _props(live) == _props(tool.parameters), f"{name}: property set drift"
        assert _required(live) == _required(tool.parameters), f"{name}: required set drift"
        for pname, pschema in tool.parameters.get("properties", {}).items():
            reg_enum = _find_enum(pschema, tool.parameters)
            if reg_enum is not None:
                live_enum = _find_enum(live.get("properties", {}).get(pname, {}), live)
                assert live_enum == reg_enum, f"{name}.{pname}: enum drift {live_enum} != {reg_enum}"


# --- 3) voice tool names must match the registry's voice surface ------------------------------
def test_voice_names_match_registry():
    voice = json.loads(VOICE_TOOLS_JSON.read_text(encoding="utf-8"))
    names = {t["name"] for t in voice}
    assert names == _voice_tool_names(), (
        "voiceTools.json names diverge from the registry voice surface: "
        f"json={sorted(names)} expected={sorted(_voice_tool_names())}"
    )


# --- 4) the VoiceToolName union must equal the json names (tsc ties union -> executors) --------
def test_voice_union_matches_json():
    ts = VOICE_EXECUTORS_TS.read_text(encoding="utf-8")
    m = re.search(r"export type VoiceToolName\s*=\s*([^;]+);", ts, re.S)
    assert m, "could not find the VoiceToolName union in voiceExecutors.ts"
    union = set(re.findall(r'"([A-Za-z0-9_]+)"', m.group(1)))
    names = {t["name"] for t in json.loads(VOICE_TOOLS_JSON.read_text(encoding="utf-8"))}
    assert union == names, f"VoiceToolName union != voiceTools.json names: union={sorted(union)} json={sorted(names)}"


# --- 5) shared-tool param sets + enums must mirror the registry -------------------------------
def test_voice_props_and_enums_match_registry():
    voice = {t["name"]: t for t in json.loads(VOICE_TOOLS_JSON.read_text(encoding="utf-8"))}
    use_cases = set(scoring.SUPPORTED_USE_CASES)
    for tool in at.REGISTRY:
        if "voice" not in tool.surfaces or tool.parity != "props":
            continue
        v = voice[tool.name]
        assert _props(v["parameters"]) == _props(tool.parameters), f"{tool.name}: voice property set drift"
    # Every use_case enum on the voice side must equal the canonical preset list.
    for name, t in voice.items():
        for pname, pschema in (t["parameters"].get("properties", {}) or {}).items():
            if pname == "use_case":
                assert set(pschema.get("enum", [])) == use_cases, f"{name}.use_case enum drift"


# --- bonus) the UI must have a label for every registry phase ----------------------------------
def test_phase_labels_cover_registry_phases():
    ts = AGENT_CLIENT_TS.read_text(encoding="utf-8")
    m = re.search(r"PHASE_LABELS[^{]*\{(.*?)\};", ts, re.S)
    assert m, "could not find PHASE_LABELS in agentClient.ts"
    labels = set(re.findall(r"(\w+)\s*:", m.group(1)))
    phases = {t.phase for t in at.REGISTRY if t.phase}
    missing = phases - labels
    assert not missing, f"PHASE_LABELS is missing labels for registry phases: {sorted(missing)}"
