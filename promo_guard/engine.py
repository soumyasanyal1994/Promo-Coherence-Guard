"""
Deterministic conflict detection (authority layer).
MVP: BELOW_FLOOR_PRICE, OVERLAP_SAME_SKU_SAME_WINDOW, CLEARANCE_PLUS_PROMO.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_CHANNEL = "web"


def _parse_ts(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _interval_intersection(
    starts: list[datetime], ends: list[datetime]
) -> tuple[datetime | None, datetime | None]:
    if not starts:
        return None, None
    lo = max(starts)
    hi = min(ends)
    if lo > hi:
        return None, None
    return lo, hi


def _two_intervals_overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return max(a0, b0) <= min(a1, b1)


def _pairwise_stackable(a: dict[str, Any], b: dict[str, Any]) -> bool:
    aid, bid = a["promo_id"], b["promo_id"]
    stack_a = set(a.get("stackable_with") or [])
    stack_b = set(b.get("stackable_with") or [])
    return bid in stack_a or aid in stack_b


def _subset_pairwise_stackable(promos: list[dict[str, Any]]) -> bool:
    for i in range(len(promos)):
        for j in range(i + 1, len(promos)):
            if not _pairwise_stackable(promos[i], promos[j]):
                return False
    return True


def _effective_price_percentage(base: float, promos: list[dict[str, Any]]) -> float:
    factors = np.array([(1.0 - float(p["discount_value"]) / 100.0) for p in promos], dtype=np.float64)
    return float(np.round(base * float(np.prod(factors)), 2))


@dataclass
class ConflictRecord:
    conflict_type: str
    severity: str
    sku: str | None
    promo_ids: list[str]
    evidence: dict[str, Any]
    deterministic_summary: str

    def to_row(self) -> dict[str, Any]:
        return {
            "conflict_type": self.conflict_type,
            "severity": self.severity,
            "sku": self.sku or "",
            "promo_ids": "|".join(self.promo_ids),
            "deterministic_summary": self.deterministic_summary,
            "evidence_json": json.dumps(self.evidence, default=str),
        }


def load_promotions_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _normalize_pricing_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    required = (
        "sku",
        "product_name",
        "category",
        "base_price_gbp",
        "cost_price_gbp",
        "floor_price_gbp",
        "current_clearance_active",
    )
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Pricing file is missing column(s): {missing}. "
            f"Expected headers: {', '.join(required)}"
        )
    col = df["current_clearance_active"]
    if col.dtype == object:
        df["current_clearance_active"] = col.astype(str).str.strip().str.lower().isin(
            ("1", "true", "yes", "y")
        )
    else:
        df["current_clearance_active"] = col.astype(bool)
    return df


def load_pricing_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return _normalize_pricing_columns(df)


def load_pricing_file(path: str | Path) -> pd.DataFrame:
    """Load pricing from CSV or Excel (.xlsx / .xls)."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(p, sheet_name=0)
    else:
        df = pd.read_csv(p)
    return _normalize_pricing_columns(df)


def promos_for_sku(
    promos: list[dict[str, Any]], sku: str, channel: str = DEFAULT_CHANNEL
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in promos:
        skus = p.get("applicable_skus") or []
        ch = p.get("channel") or []
        if sku in skus and (not ch or channel in ch):
            p2 = dict(p)
            p2["_start"] = _parse_ts(p["start_datetime"])
            p2["_end"] = _parse_ts(p["end_datetime"])
            out.append(p2)
    return out


def detect_conflicts(
    promos: list[dict[str, Any]],
    pricing: pd.DataFrame,
    *,
    channel: str = DEFAULT_CHANNEL,
    as_of: datetime | None = None,
    horizon_days: int = 7,
    max_subset_size: int = 12,
) -> list[ConflictRecord]:
    """
    Run MVP conflict detectors. Time-based checks use UTC `as_of` (default: now UTC).
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    horizon_end = as_of + timedelta(days=horizon_days)

    sku_to_row = pricing.set_index("sku").to_dict("index")
    conflicts: list[ConflictRecord] = []
    seen_overlap: set[tuple[str, str, str]] = set()
    seen_floor: set[tuple[str, frozenset[str]]] = set()
    seen_clearance: set[str] = set()

    for sku, row in sku_to_row.items():
        base = float(row["base_price_gbp"])
        floor_p = float(row["floor_price_gbp"])
        clearance = bool(row.get("current_clearance_active", False))

        plist = promos_for_sku(promos, sku, channel=channel)
        if not plist:
            continue

        # --- CLEARANCE_PLUS_PROMO ---
        if clearance:
            touching = [
                p
                for p in plist
                if _two_intervals_overlap(p["_start"], p["_end"], as_of, horizon_end)
            ]
            if touching and sku not in seen_clearance:
                seen_clearance.add(sku)
                pids = [p["promo_id"] for p in touching]
                conflicts.append(
                    ConflictRecord(
                        conflict_type="CLEARANCE_PLUS_PROMO",
                        severity="WARNING",
                        sku=sku,
                        promo_ids=sorted(set(pids)),
                        evidence={
                            "as_of": as_of.isoformat(),
                            "horizon_end": horizon_end.isoformat(),
                            "base_price_gbp": base,
                            "touching_promo_count": len(touching),
                        },
                        deterministic_summary=(
                            f"SKU {sku} is on clearance while {len(touching)} promotion(s) "
                            f"overlap the next {horizon_days} day window — extra discount risk."
                        ),
                    )
                )

        # --- OVERLAP_SAME_SKU_SAME_WINDOW (pairwise overlapping promos) ---
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                a, b = plist[i], plist[j]
                if not _two_intervals_overlap(a["_start"], a["_end"], b["_start"], b["_end"]):
                    continue
                key = tuple(sorted([a["promo_id"], b["promo_id"]])) + (sku,)
                if key in seen_overlap:
                    continue
                seen_overlap.add(key)
                conflicts.append(
                    ConflictRecord(
                        conflict_type="OVERLAP_SAME_SKU_SAME_WINDOW",
                        severity="WARNING",
                        sku=sku,
                        promo_ids=sorted([a["promo_id"], b["promo_id"]]),
                        evidence={
                            "sku": sku,
                            "promo_a": a["promo_id"],
                            "promo_b": b["promo_id"],
                            "window_a": [a["start_datetime"], a["end_datetime"]],
                            "window_b": [b["start_datetime"], b["end_datetime"]],
                            "pairwise_stackable": _pairwise_stackable(a, b),
                        },
                        deterministic_summary=(
                            f"SKU {sku}: promotions '{a['promo_id']}' and '{b['promo_id']}' "
                            f"have overlapping active windows."
                        ),
                    )
                )

        # --- BELOW_FLOOR_PRICE (stackable subsets with common time intersection) ---
        n = len(plist)
        if n > max_subset_size:
            plist_eval = sorted(plist, key=lambda p: float(p.get("discount_value", 0)), reverse=True)[
                :max_subset_size
            ]
        else:
            plist_eval = plist

        for r in range(1, len(plist_eval) + 1):
            for subset in itertools.combinations(plist_eval, r):
                subs = list(subset)
                if len(subs) > 1 and not _subset_pairwise_stackable(subs):
                    continue
                starts = [p["_start"] for p in subs]
                ends = [p["_end"] for p in subs]
                lo, hi = _interval_intersection(starts, ends)
                if lo is None:
                    continue
                eff = _effective_price_percentage(base, subs)
                if eff + 1e-6 < floor_p:
                    key = (sku, frozenset(p["promo_id"] for p in subs))
                    if key in seen_floor:
                        continue
                    seen_floor.add(key)
                    conflicts.append(
                        ConflictRecord(
                            conflict_type="BELOW_FLOOR_PRICE",
                            severity="CRITICAL",
                            sku=sku,
                            promo_ids=[p["promo_id"] for p in subs],
                            evidence={
                                "sku": sku,
                                "base_price_gbp": base,
                                "floor_price_gbp": floor_p,
                                "effective_price_gbp": eff,
                                "stack": [{"promo_id": p["promo_id"], "pct": p["discount_value"]} for p in subs],
                                "common_window_utc": [lo.isoformat(), hi.isoformat()],
                            },
                            deterministic_summary=(
                                f"SKU {sku}: stacked promotions can take the effective web price to £{eff:.2f}, "
                                f"below the floor £{floor_p:.2f} (base £{base:.2f})."
                            ),
                        )
                    )

    return conflicts


def conflicts_to_dataframe(conflicts: list[ConflictRecord]) -> pd.DataFrame:
    if not conflicts:
        return pd.DataFrame(
            columns=[
                "conflict_type",
                "severity",
                "sku",
                "promo_ids",
                "deterministic_summary",
                "evidence_json",
            ]
        )
    return pd.DataFrame([c.to_row() for c in conflicts])
