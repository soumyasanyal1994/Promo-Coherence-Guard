"""Optional LLM narrative layer — explanations only; conflicts remain rule-based."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import anthropic
import google.generativeai as genai
import requests
from google.generativeai.types import HarmBlockThreshold, HarmCategory


# Retail pricing explanations are often flagged as "financial harm" / generic safety noise.
# Relax thresholds so legitimate B2B conflict reports are not blanked.
_RETAIL_SAFETY = [
    {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
]


BATCH_SYSTEM = """You are a retail pricing analyst for a large UK retailer.
A deterministic engine already found promotion conflicts. You receive JSON with key "conflicts" (array).

For each conflict in order (index 0, 1, 2, …), write a plain-English explanation for a trading or eCommerce manager:
- What happens at till / on the website
- Commercial risk (margin, loss-making, ambiguity)
- One or two concrete fixes (promo IDs, SKU exclusions, dates, stack rules)

Rules:
- Treat "evidence" and "deterministic_summary" as fact; do not invent SKUs, prices, or promo IDs.
- About 100–170 words per conflict.
- Start each block with one line echoing the severity label (e.g. "CRITICAL — …").

OUTPUT (critical — machine-parsed):
For each index i from 0 to N-1, output EXACTLY one block in this form, in order, with no text before the first block or after the last:

<<<CONFLICT i START>>>
your narrative here
<<<CONFLICT i END>>>

Use the integer i matching the conflict order. Do not use markdown code fences. Do not output JSON.
"""


SINGLE_SYSTEM = """You are a retail pricing analyst for a large UK retailer.
Explain the conflict below in plain English for a trading manager (120–200 words):
what happens at till/online, commercial risk, and 1–2 remediation steps.
Treat evidence as fact. Do not invent SKUs or prices.
Start with one line that echoes the severity label.
No markdown code fences. Plain text only."""


CLAUDE_HAIKU_MODEL = "claude-3-5-haiku-20241022"


def _evidence_from_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("evidence_json")
    if raw is None or (isinstance(raw, float) and str(raw) == "nan"):
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return {}


def _truncate_str(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _compact_row_for_llm(row: dict[str, Any]) -> dict[str, Any]:
    ev = _evidence_from_row(row)
    ev_s = json.dumps(ev, default=str)
    if len(ev_s) > 3500:
        ev_out: Any = {"_note": "evidence truncated for model context", "preview": ev_s[:3500]}
    else:
        ev_out = ev
    return {
        "conflict_type": row.get("conflict_type", ""),
        "severity": row.get("severity", ""),
        "deterministic_summary": _truncate_str(str(row.get("deterministic_summary", "")), 900),
        "evidence": ev_out,
    }


def _gen_config(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {"temperature": 0.2, "max_output_tokens": 12288}
    if extra:
        cfg.update(extra)
    return cfg


def _finish_reason(resp: Any) -> str | None:
    try:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return None
        fr = getattr(cands[0], "finish_reason", None)
        if fr is None:
            return None
        return str(fr)
    except Exception:
        return None


def _prompt_block_reason(resp: Any) -> str | None:
    try:
        pf = getattr(resp, "prompt_feedback", None)
        if pf is None:
            return None
        br = getattr(pf, "block_reason", None)
        if br is None:
            return None
        return str(br)
    except Exception:
        return None


def _response_text(resp: Any) -> str:
    """Read text from parts first — ``resp.text`` raises when candidates are blocked/empty."""
    try:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return ""
        c0 = cands[0]
        content = getattr(c0, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if not parts:
            return ""
        return "".join(getattr(p, "text", "") or "" for p in parts).strip()
    except Exception:
        pass
    try:
        return (resp.text or "").strip()
    except Exception:
        return ""


def _parse_delimited_batch(text: str, n: int) -> list[str]:
    """Extract narratives between <<<CONFLICT i START>>> and <<<CONFLICT i END>>>."""
    out = [""] * n
    for i in range(n):
        start_tag = f"<<<CONFLICT {i} START>>>"
        end_tag = f"<<<CONFLICT {i} END>>>"
        lo = text.find(start_tag)
        hi = text.find(end_tag)
        if lo == -1 or hi == -1 or hi <= lo:
            break
        body = text[lo + len(start_tag) : hi].strip()
        if not body:
            break
        out[i] = body
    if all(out):
        return out

    pat = re.compile(
        r"<<<\s*CONFLICT\s+(\d+)\s+START\s*>>>\s*(.*?)\s*<<<\s*CONFLICT\s+\1\s+END\s*>>>",
        re.DOTALL | re.IGNORECASE,
    )
    found: dict[int, str] = {}
    for m in pat.finditer(text):
        idx = int(m.group(1))
        body = (m.group(2) or "").strip()
        if body and 0 <= idx < n:
            found[idx] = body
    if len(found) == n:
        return [found[i] for i in range(n)]
    merged = [""] * n
    for i in range(n):
        merged[i] = found.get(i, "")
    if sum(1 for x in merged if x) == n:
        return merged
    raise ValueError("Could not parse delimited conflict blocks")


def _generate(model: genai.GenerativeModel, prompt: str) -> Any:
    return model.generate_content(
        prompt,
        generation_config=_gen_config(),
        safety_settings=_RETAIL_SAFETY,
        request_options={"timeout": 45},
    )


def _gemini_usage_from_resp(resp: Any) -> dict[str, int]:
    meta = getattr(resp, "usage_metadata", None)
    if meta is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    inp = int(getattr(meta, "prompt_token_count", 0) or 0)
    out = int(getattr(meta, "candidates_token_count", 0) or 0)
    total = int(getattr(meta, "total_token_count", inp + out) or (inp + out))
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


def _zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _add_usage(acc: dict[str, int], inc: dict[str, int]) -> None:
    acc["input_tokens"] += int(inc.get("input_tokens", 0) or 0)
    acc["output_tokens"] += int(inc.get("output_tokens", 0) or 0)
    acc["total_tokens"] += int(inc.get("total_tokens", 0) or 0)


def _normalize_model_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    return n[7:] if n.startswith("models/") else n


def _resolve_model_name(requested_model: str) -> str:
    """
    Resolve requested model to an available generateContent model.
    Falls back to preferred Flash variants when requested model is unavailable.
    """
    req_norm = _normalize_model_name(requested_model)
    try:
        models = list(genai.list_models())
    except Exception:
        return requested_model

    available: set[str] = set()
    for m in models:
        name = getattr(m, "name", "") or ""
        methods = set(getattr(m, "supported_generation_methods", []) or [])
        if not name or "generateContent" not in methods:
            continue
        available.add(name)
        available.add(_normalize_model_name(name))

    if requested_model in available:
        return requested_model
    if req_norm in available:
        return req_norm
    if f"models/{req_norm}" in available:
        return f"models/{req_norm}"

    preferred = [
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash",
        "models/gemini-2.0-flash-lite",
        "models/gemini-1.5-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
    ]
    for cand in preferred:
        if cand in available:
            return cand
    return requested_model


def _explain_chunk_delimited(
    model: genai.GenerativeModel,
    chunk_rows: list[dict[str, Any]],
) -> tuple[list[str], dict[str, int]]:
    conflicts_payload = [_compact_row_for_llm(r) for r in chunk_rows]
    user_text = json.dumps({"conflicts": conflicts_payload}, default=str)
    prompt = BATCH_SYSTEM + "\n\n" + user_text
    resp = _generate(model, prompt)
    usage = _gemini_usage_from_resp(resp)
    raw = _response_text(resp)
    if not raw:
        fr = _finish_reason(resp)
        br = _prompt_block_reason(resp)
        detail = []
        if fr:
            detail.append(f"finish_reason={fr}")
        if br:
            detail.append(f"prompt_block={br}")
        msg = "; ".join(detail) if detail else "no candidates returned"
        raise ValueError(f"Empty model body ({msg})")
    return _parse_delimited_batch(raw, len(chunk_rows)), usage


def _explain_one_plain(model: genai.GenerativeModel, row: dict[str, Any]) -> tuple[str, dict[str, int]]:
    c = _compact_row_for_llm(row)
    body = (
        f"conflict_type: {c['conflict_type']}\n"
        f"severity: {c['severity']}\n"
        f"summary: {c['deterministic_summary']}\n"
        f"evidence: {json.dumps(c['evidence'], default=str)}\n"
    )
    prompt = SINGLE_SYSTEM + "\n\n" + body
    resp = _generate(model, prompt)
    raw = _response_text(resp)
    if not raw:
        fr = _finish_reason(resp)
        br = _prompt_block_reason(resp)
        parts: list[str] = []
        if fr:
            parts.append(f"finish_reason={fr}")
        if br:
            parts.append(f"prompt_block={br}")
        msg = "; ".join(parts) or "unknown"
        raise ValueError(f"Empty model body ({msg})")
    return raw.strip(), _gemini_usage_from_resp(resp)


def _fallback_narrative(row: dict[str, Any], err: str | None) -> str:
    sev = row.get("severity", "")
    summ = row.get("deterministic_summary", "")
    tail = f" (API detail: {err})" if err else ""
    return (
        f"{sev} — automated narrative fallback (Gemini returned no text). "
        f"{summ} "
        f"Use this deterministic finding as the source of truth; try model **gemini-1.5-flash** "
        f"or reduce “Max conflicts to send to Gemini” if the API was overloaded.{tail}"
    )


def _normalize_endpoint_for_chat(base: str) -> str:
    b = (base or "").strip().rstrip("/")
    if not b:
        return ""
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1"):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


def _openai_compat_chat_completion(
    *,
    endpoint: str,
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: int = 45,
) -> tuple[str, dict[str, int]]:
    url = _normalize_endpoint_for_chat(endpoint)
    if not url:
        raise ValueError("Custom endpoint is empty.")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    resp.raise_for_status()
    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("No choices in custom endpoint response.")
    msg = choices[0].get("message") or {}
    content = msg.get("content", "")
    if isinstance(content, list):
        # Some providers return content blocks.
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text_parts.append(str(part.get("text", "")))
            else:
                text_parts.append(str(part))
        text = "".join(text_parts).strip()
    else:
        text = str(content).strip()
    if not text:
        raise ValueError("Empty text from custom endpoint.")
    usage_raw = body.get("usage") or {}
    inp = int(usage_raw.get("prompt_tokens", 0) or 0)
    out = int(usage_raw.get("completion_tokens", 0) or 0)
    total = int(usage_raw.get("total_tokens", inp + out) or (inp + out))
    return text, {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


def _batch_explain_custom_endpoint(
    rows: list[dict[str, Any]],
    *,
    api_key: str,
    model_name: str,
    custom_endpoint: str,
    max_conflicts: int = 35,
    chunk_size: int = 2,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[str], str, dict[str, int]]:
    n = len(rows)
    out: list[str] = [""] * n
    usage = _zero_usage()
    if n == 0:
        return out, model_name, usage

    limit = min(n, max(1, max_conflicts))
    work = rows[:limit]
    chunk_size = max(1, min(chunk_size, 3))
    done = 0

    for start in range(0, len(work), chunk_size):
        chunk = work[start : start + chunk_size]
        conflicts_payload = [_compact_row_for_llm(r) for r in chunk]
        user_text = json.dumps({"conflicts": conflicts_payload}, default=str)
        texts: list[str] = []
        try:
            raw, chunk_usage = _openai_compat_chat_completion(
                endpoint=custom_endpoint,
                api_key=api_key,
                model_name=model_name,
                system_prompt=BATCH_SYSTEM,
                user_prompt=user_text,
            )
            texts = _parse_delimited_batch(raw, len(chunk))
            _add_usage(usage, chunk_usage)
        except Exception as chunk_err:
            chunk_err_s = str(chunk_err)[:300]
            for row in chunk:
                try:
                    payload = _compact_row_for_llm(row)
                    single_prompt = json.dumps({"conflicts": [payload]}, default=str)
                    raw_one, row_usage = _openai_compat_chat_completion(
                        endpoint=custom_endpoint,
                        api_key=api_key,
                        model_name=model_name,
                        system_prompt=SINGLE_SYSTEM,
                        user_prompt=single_prompt,
                    )
                    texts.append(raw_one)
                    _add_usage(usage, row_usage)
                except Exception as row_err:
                    texts.append(_fallback_narrative(row, str(row_err)[:200] or chunk_err_s))
        for i, txt in enumerate(texts):
            out[start + i] = txt
        done += len(chunk)
        if progress_callback:
            progress_callback(done, limit)

    return out, model_name, usage


def _batch_explain_gemini(
    rows: list[dict[str, Any]],
    *,
    api_key: str,
    model_name: str,
    custom_endpoint: str = "",
    max_conflicts: int = 35,
    chunk_size: int = 2,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[str], str, dict[str, int]]:
    """
    Generate Gemini narratives for up to ``max_conflicts`` rows (rest get empty string).
    Delimiter batches with per-row plain-text fallback and a deterministic echo if both fail.
    """
    n = len(rows)
    out: list[str] = [""] * n
    usage = _zero_usage()
    if n == 0:
        return out, model_name, usage

    limit = min(n, max(1, max_conflicts))
    work = rows[:limit]

    endpoint = (custom_endpoint or "").strip()
    if endpoint:
        # Custom endpoints (for example LiteLLM proxy) are typically OpenAI-compatible.
        # Use direct HTTP with explicit timeout to avoid hanging SDK calls.
        return _batch_explain_custom_endpoint(
            rows,
            api_key=api_key,
            model_name=model_name,
            custom_endpoint=endpoint,
            max_conflicts=max_conflicts,
            chunk_size=chunk_size,
            progress_callback=progress_callback,
        )

    genai.configure(api_key=api_key)
    resolved_model_name = _resolve_model_name(model_name)
    model = genai.GenerativeModel(resolved_model_name)
    chunk_size = max(1, min(chunk_size, 3))

    done = 0
    for start in range(0, len(work), chunk_size):
        chunk = work[start : start + chunk_size]
        texts: list[str] = []
        try:
            texts, chunk_usage = _explain_chunk_delimited(model, chunk)
            _add_usage(usage, chunk_usage)
        except Exception as chunk_err:
            chunk_err_s = str(chunk_err)[:300]
            for row in chunk:
                try:
                    txt, row_usage = _explain_one_plain(model, row)
                    texts.append(txt)
                    _add_usage(usage, row_usage)
                except Exception as row_err:
                    texts.append(_fallback_narrative(row, str(row_err)[:200] or chunk_err_s))
        for i, txt in enumerate(texts):
            out[start + i] = txt
        done += len(chunk)
        if progress_callback:
            progress_callback(done, limit)

    return out, resolved_model_name, usage


def _claude_message(
    client: anthropic.Anthropic,
    model_name: str,
    prompt: str,
) -> Any:
    return client.messages.create(
        model=model_name,
        max_tokens=1200,
        temperature=0.2,
        system=BATCH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )


def _claude_usage(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    if u is None:
        return _zero_usage()
    inp = int(getattr(u, "input_tokens", 0) or 0)
    out = int(getattr(u, "output_tokens", 0) or 0)
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}


def _claude_text(resp: Any) -> str:
    blocks = getattr(resp, "content", None) or []
    text_parts = []
    for b in blocks:
        if getattr(b, "type", "") == "text":
            text_parts.append(getattr(b, "text", ""))
    return "".join(text_parts).strip()


def _batch_explain_claude(
    rows: list[dict[str, Any]],
    *,
    api_key: str,
    model_name: str,
    max_conflicts: int = 35,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[str], str, dict[str, int]]:
    n = len(rows)
    out: list[str] = [""] * n
    usage = _zero_usage()
    if n == 0:
        return out, model_name, usage

    client = anthropic.Anthropic(api_key=api_key)
    limit = min(n, max(1, max_conflicts))
    done = 0
    for i in range(limit):
        row = rows[i]
        payload = _compact_row_for_llm(row)
        prompt = json.dumps({"conflicts": [payload]}, default=str)
        try:
            resp = _claude_message(client, model_name, prompt)
            text = _claude_text(resp)
            if not text:
                text = _fallback_narrative(row, "empty Claude response")
            out[i] = text
            _add_usage(usage, _claude_usage(resp))
        except Exception as exc:
            out[i] = _fallback_narrative(row, str(exc)[:200])
        done += 1
        if progress_callback:
            progress_callback(done, limit)
    return out, model_name, usage


def batch_explain_with_provider(
    rows: list[dict[str, Any]],
    *,
    provider: str,
    api_key: str,
    model_name: str,
    custom_endpoint: str = "",
    max_conflicts: int = 35,
    chunk_size: int = 2,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[str], str, dict[str, int]]:
    provider_key = (provider or "gemini").strip().lower()
    if provider_key == "claude":
        selected = model_name or CLAUDE_HAIKU_MODEL
        return _batch_explain_claude(
            rows,
            api_key=api_key,
            model_name=selected,
            max_conflicts=max_conflicts,
            progress_callback=progress_callback,
        )
    return _batch_explain_gemini(
        rows,
        api_key=api_key,
        model_name=model_name,
        custom_endpoint=custom_endpoint,
        max_conflicts=max_conflicts,
        chunk_size=chunk_size,
        progress_callback=progress_callback,
    )


def batch_explain(
    rows: list[dict[str, Any]],
    *,
    api_key: str,
    model_name: str,
    custom_endpoint: str = "",
    max_conflicts: int = 35,
    chunk_size: int = 2,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[str], str]:
    texts, used_model, _ = batch_explain_with_provider(
        rows,
        provider="gemini",
        api_key=api_key,
        model_name=model_name,
        custom_endpoint=custom_endpoint,
        max_conflicts=max_conflicts,
        chunk_size=chunk_size,
        progress_callback=progress_callback,
    )
    return texts, used_model
