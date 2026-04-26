#!/usr/bin/env python3
"""
Generate sample promotions (JSONL) and product pricing (CSV) for Promo Coherence Guard.
Seeds: ~5 floor breaches, 3 overlapping promo pairs, 2 clearance+promo cases (within MVP detectors).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RNG = np.random.default_rng(42)
PY_RNG = random.Random(42)

CATEGORIES = ["Electronics", "Grocery", "Home", "Fashion", "Health", "Toys", "Seasonal"]
CHANNELS_POOL = [["web", "app"], ["web"], ["app"], ["web", "app", "pos"]]


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)

    # Anchor "scan day" in the future so demos stay valid
    anchor = datetime(2026, 11, 28, 18, 0, 0, tzinfo=timezone.utc)
    bf_start = datetime(2026, 11, 29, 0, 0, 0, tzinfo=timezone.utc)
    bf_end = datetime(2026, 11, 29, 23, 59, 59, tzinfo=timezone.utc)

    n_skus = 200
    skus = [f"SKU-{1000 + i}" for i in range(1, n_skus + 1)]

    # --- Pricing: baseline random, then tune conflict SKUs ---
    base = RNG.uniform(15, 450, n_skus).round(2)
    margin = RNG.uniform(0.18, 0.42, n_skus)
    cost = (base * (1 - margin)).round(2)
    floor = (base * RNG.uniform(0.72, 0.92, n_skus)).round(2)
    floor = np.minimum(floor, base - 0.01)
    clearance = np.zeros(n_skus, dtype=bool)

    # SKUs reserved for seeded conflicts
    floor_skus = skus[0:5]
    overlap_skus = skus[10:13]  # one SKU per overlap pair seed → 3 SKUs
    clearance_skus = skus[20:22]

    for i, s in enumerate(skus):
        if s in floor_skus:
            idx = skus.index(s)
            base[idx] = 120.0
            cost[idx] = 72.0
            floor[idx] = 45.0
        if s in clearance_skus:
            clearance[i] = True
            idx = skus.index(s)
            base[idx] = 55.0
            cost[idx] = 30.0
            floor[idx] = 35.0

    names = [f"Product {i}" for i in range(1, n_skus + 1)]
    cats = [CATEGORIES[i % len(CATEGORIES)] for i in range(n_skus)]

    pricing = pd.DataFrame(
        {
            "sku": skus,
            "product_name": names,
            "category": cats,
            "base_price_gbp": base,
            "cost_price_gbp": cost,
            "floor_price_gbp": floor,
            "current_clearance_active": clearance,
        }
    )
    pricing_path = DATA / "sample_pricing.csv"
    pricing.to_csv(pricing_path, index=False)

    promos: list[dict] = []
    pid = 0

    def next_id(prefix: str) -> str:
        nonlocal pid
        pid += 1
        return f"{prefix}-{pid:03d}"

    # --- Seeded: floor breach — two stackable % promos on floor_skus (multiplicative) ---
    floor_a_id = next_id("PROMO-FLOOR-A")
    floor_b_id = "PROMO-FLOOR-B-STACK"
    promos.append(
        {
            "promo_id": floor_a_id,
            "name": "Loyalty 35% selected lines",
            "type": "PERCENTAGE_DISCOUNT",
            "discount_value": 35.0,
            "applicable_skus": floor_skus,
            "channel": ["web", "app"],
            "start_datetime": iso(bf_start - timedelta(days=7)),
            "end_datetime": iso(bf_end),
            "stackable_with": [floor_b_id],
            "priority": 5,
        }
    )
    promos.append(
        {
            "promo_id": floor_b_id,
            "name": "Black Friday flash 45% electronics",
            "type": "PERCENTAGE_DISCOUNT",
            "discount_value": 45.0,
            "applicable_skus": floor_skus,
            "channel": ["web", "app"],
            "start_datetime": iso(bf_start),
            "end_datetime": iso(bf_end),
            "stackable_with": [floor_a_id],
            "priority": 10,
        }
    )

    # --- Seeded: three overlapping windows (same SKU, two promos, not mutually stackable) ---
    for j, sku in enumerate(overlap_skus):
        p1 = next_id("PROMO-OVR-A")
        p2 = next_id("PROMO-OVR-B")
        w0 = anchor - timedelta(days=3 + j)
        promos.append(
            {
                "promo_id": p1,
                "name": f"Overlapping window A {j+1}",
                "type": "PERCENTAGE_DISCOUNT",
                "discount_value": float(10 + j * 3),
                "applicable_skus": [sku],
                "channel": ["web", "app"],
                "start_datetime": iso(w0),
                "end_datetime": iso(w0 + timedelta(days=5)),
                "stackable_with": [],
                "priority": 3 + j,
            }
        )
        promos.append(
            {
                "promo_id": p2,
                "name": f"Overlapping window B {j+1}",
                "type": "PERCENTAGE_DISCOUNT",
                "discount_value": float(12 + j * 2),
                "applicable_skus": [sku],
                "channel": ["web", "app"],
                "start_datetime": iso(w0 + timedelta(days=2)),
                "end_datetime": iso(w0 + timedelta(days=7)),
                "stackable_with": [],
                "priority": 7 + j,
            }
        )

    # --- Seeded: clearance + promo on clearance_skus ---
    for sku in clearance_skus:
        promos.append(
            {
                "promo_id": next_id("PROMO-CLR"),
                "name": f"Extra 15% off clearance (stack)",
                "type": "PERCENTAGE_DISCOUNT",
                "discount_value": 15.0,
                "applicable_skus": [sku],
                "channel": ["web"],
                "start_datetime": iso(anchor - timedelta(days=1)),
                "end_datetime": iso(anchor + timedelta(days=14)),
                "stackable_with": [],
                "priority": 20,
            }
        )

    # --- Fill up to 80 promos: non-overlapping windows + safe discounts (keeps scan noise low) ---
    used_ids = {p["promo_id"] for p in promos}
    all_skus = list(skus)
    base_by_sku = dict(zip(skus, base.tolist()))
    floor_by_sku = dict(zip(skus, floor.tolist()))

    def max_safe_pct(sku_list: list[str]) -> float:
        """Largest uniform % discount so no SKU in list goes below its floor (single-promo)."""
        m = 60.0
        for s in sku_list:
            b, fl = base_by_sku[s], floor_by_sku[s]
            if b <= 0:
                continue
            max_pct = 100.0 * (1.0 - fl / b) - 1.0  # 1pt buffer
            m = min(m, max_pct)
        return max(3.0, float(np.floor(max(3.0, m))))

    slot = 0
    while len(promos) < 80:
        k = int(RNG.integers(3, 14))
        pick = list(RNG.choice(all_skus, size=k, replace=False))
        pct = float(RNG.integers(5, int(max_safe_pct(pick)) + 1))
        slot += 1
        start = anchor - timedelta(days=200 - slot)
        end = start + timedelta(days=1)
        ch = PY_RNG.choice(CHANNELS_POOL)
        promo_id = next_id("PROMO-GEN")
        if promo_id in used_ids:
            continue
        used_ids.add(promo_id)
        stack: list[str] = []

        promos.append(
            {
                "promo_id": promo_id,
                "name": f"Campaign {len(promos)+1}: {pct:.0f}% off {pick[0][:8]}…",
                "type": "PERCENTAGE_DISCOUNT",
                "discount_value": pct,
                "applicable_skus": pick,
                "channel": ch,
                "start_datetime": iso(start),
                "end_datetime": iso(end),
                "stackable_with": stack,
                "priority": int(RNG.integers(1, 30)),
            }
        )

    promos = promos[:80]
    jsonl_path = DATA / "sample_promotions.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in promos:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Sanity check with numpy for floor SKUs at BF window
    b = np.array([120.0] * len(floor_skus))
    eff = b * (1 - 0.35) * (1 - 0.45)
    print("Sample floor SKUs effective (BF stack):", eff.tolist(), "floor=45")

    print(f"Wrote {jsonl_path} ({len(promos)} promos)")
    print(f"Wrote {pricing_path} ({len(pricing)} rows)")


if __name__ == "__main__":
    main()
