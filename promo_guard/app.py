"""
Promo Coherence Guard — Streamlit UI.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

if __package__ in (None, ""):
    # Support `streamlit run promo_guard/app.py` when cwd is not project root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from promo_guard.engine import (
    conflicts_to_dataframe,
    detect_conflicts,
    load_pricing_csv,
    load_pricing_file,
    load_promotions_jsonl,
)
from promo_guard.llm_client import batch_explain

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = ROOT / "logs"
DEFAULT_PROMO = DATA / "sample_promotions.jsonl"
DEFAULT_PRICE = DATA / "sample_pricing.csv"
TAXONOMY = DATA / "conflict_taxonomy.json"
# Bundled synthetic promos peak around this instant (Black Friday weekend 2026).
DEMO_AS_OF = datetime(2026, 11, 29, 12, 0, 0, tzinfo=timezone.utc)

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _badge_style(sev: str) -> str:
    palette = {
        "CRITICAL": ("#7f1d1d", "#fecaca"),
        "WARNING": ("#854d0e", "#fef08a"),
        "INFO": ("#1e3a8a", "#bfdbfe"),
    }
    fg, bg = palette.get(sev, ("#111827", "#e5e7eb"))
    return f"color:{fg};background:{bg};padding:4px 10px;border-radius:6px;font-weight:600;"


def _append_audit(entry: dict) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / "scans.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def main() -> None:
    st.set_page_config(page_title="Promo Coherence Guard", layout="wide")
    st.title("Promo Coherence Guard")
    st.caption("Deterministic promotion conflicts first — LLM narratives are advisory explanations only.")

    with st.sidebar:
        st.subheader("Gemini")
        api_key = st.text_input("Gemini API key", type="password", help="Stored only in this browser session.")
        model_name = st.selectbox("Model", GEMINI_MODELS, index=0)
        use_llm_in_scan = st.checkbox(
            "Use Gemini during scan",
            value=True,
            help="When enabled, clicking 'Run conflict scan' also generates LLM explanations.",
        )
        max_llm_conflicts = st.number_input(
            "Max conflicts to send to Gemini",
            min_value=1,
            max_value=200,
            value=35,
            help="Large scans can have hundreds of rows; Gemini is run in batches up to this many to avoid timeouts.",
        )
        st.subheader("Scan parameters")
        channel = st.selectbox("Channel for pricing simulation", ["web", "app", "pos"], index=0)
        as_of = st.datetime_input(
            "As-of (UTC)",
            value=DEMO_AS_OF,
            help="Bundled sample data is anchored to Nov 2026; use this default to see seeded conflicts.",
        )
        horizon_days = st.number_input("Clearance horizon (days)", min_value=1, max_value=90, value=7)

    col_tax, col_help = st.columns([1, 2])
    with col_tax:
        if TAXONOMY.exists():
            tax = json.loads(TAXONOMY.read_text(encoding="utf-8"))
            st.metric("MVP conflict types", len(tax.get("mvp_active_types", [])))
    with col_help:
        st.info(
            "Upload promotions JSONL and pricing as **CSV or Excel (.xlsx)**, or use bundled samples. "
            "Click 'Run conflict scan' to run deterministic detection and (optionally) Gemini explanations in one flow."
        )

    promos_file = st.file_uploader("Promotions (JSONL)", type=["jsonl", "txt"])
    pricing_file = st.file_uploader(
        "Product pricing (CSV or Excel)",
        type=["csv", "xlsx", "xls"],
        help="Excel must use the same column headers as the sample CSV on row 1.",
    )

    use_samples = st.checkbox("Use bundled sample data if no upload", value=True)

    if st.button("Run conflict scan", type="primary"):
        try:
            if promos_file is not None:
                promo_bytes = promos_file.getvalue()
                promos_path = Path("/tmp/pcg_upload_promos.jsonl")
                promos_path.write_bytes(promo_bytes)
                promos = load_promotions_jsonl(promos_path)
                promos_hash = _sha256_bytes(promo_bytes)
            elif use_samples and DEFAULT_PROMO.exists():
                promos = load_promotions_jsonl(DEFAULT_PROMO)
                promos_hash = _sha256_bytes(DEFAULT_PROMO.read_bytes())
            else:
                st.error("Provide a promotions file or enable bundled samples.")
                return

            if pricing_file is not None:
                price_bytes = pricing_file.getvalue()
                suffix = Path(pricing_file.name).suffix.lower()
                if suffix not in (".csv", ".xlsx", ".xls"):
                    suffix = ".csv"
                price_path = Path(f"/tmp/pcg_upload_pricing{suffix}")
                price_path.write_bytes(price_bytes)
                pricing = load_pricing_file(price_path)
                pricing_hash = _sha256_bytes(price_bytes)
            elif use_samples and DEFAULT_PRICE.exists():
                pricing = load_pricing_csv(DEFAULT_PRICE)
                pricing_hash = _sha256_bytes(DEFAULT_PRICE.read_bytes())
            else:
                st.error("Provide a pricing file or enable bundled samples.")
                return

            if as_of.tzinfo is None:
                as_of_utc = as_of.replace(tzinfo=timezone.utc)
            else:
                as_of_utc = as_of.astimezone(timezone.utc)

            with st.spinner("Running deterministic engine…"):
                conflicts = detect_conflicts(
                    promos,
                    pricing,
                    channel=channel,
                    as_of=as_of_utc,
                    horizon_days=int(horizon_days),
                )
                df = conflicts_to_dataframe(conflicts)

            llm_used = False
            if use_llm_in_scan and not df.empty:
                if not api_key:
                    st.warning("Gemini is enabled but API key is missing. Showing deterministic results only.")
                else:
                    rows = df.to_dict("records")
                    n_conf = len(rows)
                    cap = int(max_llm_conflicts)
                    prog = st.progress(0)
                    gemini_status = st.empty()
                    gemini_status.caption("Gemini: starting…")
                    try:

                        def _prog(done: int, total: int) -> None:
                            frac = done / total if total else 0.0
                            prog.progress(min(1.0, frac))
                            gemini_status.caption(f"Gemini: {done}/{total} conflicts explained…")

                        texts, used_model_name = batch_explain(
                            rows,
                            api_key=api_key,
                            model_name=model_name,
                            max_conflicts=cap,
                            chunk_size=2,
                            progress_callback=_prog,
                        )
                        prog.progress(1.0)
                        gemini_status.caption("Gemini: done.")
                        df = df.copy()
                        df["llm_narrative"] = texts
                        llm_used = True
                        st.session_state["last_llm_model_used"] = used_model_name
                        if n_conf > cap:
                            st.warning(
                                f"Gemini ran on the first **{cap}** of **{n_conf}** conflicts only "
                                f"(increase “Max conflicts to send to Gemini” in the sidebar if needed). "
                                f"The full deterministic list is still in the table and export."
                            )
                    except Exception as e:
                        st.error("Gemini call failed — check API key/model and retry.")
                        st.exception(e)

            st.session_state["last_df"] = df
            st.session_state["last_promos_hash"] = promos_hash
            st.session_state["last_pricing_hash"] = pricing_hash
            st.session_state["last_as_of"] = as_of_utc.isoformat()
            st.session_state["last_llm_used"] = llm_used

            audit = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "as_of_utc": as_of_utc.isoformat(),
                "channel": channel,
                "promotions_sha256": promos_hash,
                "pricing_sha256": pricing_hash,
                "conflict_count": int(len(df)),
                "promotion_count": len(promos),
                "sku_count": len(pricing),
            }
            _append_audit(audit)
            st.success(
                f"Scan complete — {len(df)} conflict(s) logged. Audit row appended to {LOGS / 'scans.jsonl'}."
            )
            if llm_used:
                st.info(
                    f"Gemini explanations generated using model "
                    f"`{st.session_state.get('last_llm_model_used', model_name)}`."
                )
        except Exception as e:
            st.exception(e)
            return

    df = st.session_state.get("last_df")
    if df is None or df.empty:
        if df is not None and df.empty:
            st.write("No conflicts detected for the selected channel and as-of time.")
        return

    st.subheader("Conflict report")

    display_df = st.session_state["last_df"].copy()

    for _, row in display_df.iterrows():
        sev = row.get("severity", "INFO")
        st.markdown(
            f"<span style='{_badge_style(sev)}'>{sev}</span> "
            f"<strong>{row['conflict_type']}</strong> — SKU **{row.get('sku') or '—'}**",
            unsafe_allow_html=True,
        )
        st.write(row["deterministic_summary"])
        if "llm_narrative" in row and pd.notna(row["llm_narrative"]) and str(row["llm_narrative"]).strip():
            with st.expander("LLM narrative (Gemini)", expanded=True):
                st.write(row["llm_narrative"])
        st.divider()

    export_df = display_df.copy()
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Export report to CSV",
        data=csv_bytes,
        file_name=f"promo_coherence_report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv",
        mime="text/csv",
    )

    with st.expander("Audit metadata (last scan)"):
        st.json(
            {
                "promotions_sha256": st.session_state.get("last_promos_hash"),
                "pricing_sha256": st.session_state.get("last_pricing_hash"),
                "as_of_utc": st.session_state.get("last_as_of"),
            }
        )


if __name__ == "__main__":
    main()
