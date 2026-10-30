"""Suggested products for one company, from the recommendation catalogue.

The catalogue (`reco_catalogue.json`) is built outside the app by
`tools/build_reco.py` from the SM-reviewed segment workbook, and read from the
same Drive folder as the lead files -- never from this repo, which is public
while the cards carry internal prices. This module only applies its rules:

    TSIC code         -> journey (4-digit class override, else 2-digit division;
                         a lead with only a `tsic_division`, like the industrial
                         list, goes by the division)
    description       -> secondary journeys and flags (Online seller, Fleet, ...)
    registered capital-> size tier, which picks the package within a family
                         (Internet, Mobile plan)

Every product of the matched journeys is shown, grouped by category, each with
the reasons it is there. Products the SM struck through never reach the
catalogue, so nothing here filters them.

No Streamlit in this module: it is plain functions over dicts, so it can be
tested without running a page.
"""
from __future__ import annotations

import re

# Category display order: connectivity first, the products most AEs pitch.
CATEGORY_ORDER = [
    "Connectivity", "Mobile & Device", "Mobile & IoT", "Telephony & Contact Center",
    "Messaging & MarTech", "Retail & Operations", "Cloud & Collaboration", "AI & Data",
    "Smart Technology & IoT", "Smart Energy", "Cybersecurity", "Entertainment",
]


def tsic(raw) -> str:
    """'56101' / '56101.0' / 56101 -> '56101'; anything unusable -> ''."""
    s = str(raw if raw is not None else "").strip().split(".")[0]
    return s.zfill(5) if s.isdigit() else ""


UNKNOWN_INDUSTRY = "Unknown industry"


def division(raw) -> str:
    """'20' / '20.0' / 20 -> '20'; anything unusable -> ''."""
    s = str(raw if raw is not None else "").strip().split(".")[0]
    return s.zfill(2) if s.isdigit() and len(s) <= 2 else ""


def industry(raw, cat: dict) -> str:
    """The TSIC division name, e.g. 'Wholesale trade', for the industry filter."""
    code = tsic(raw)
    return (cat.get("divisions", {}).get(code[:2], {}).get("name") if code else None) \
        or UNKNOWN_INDUSTRY


def capital(raw) -> float | None:
    try:
        v = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return v if v == v and v >= 0 else None          # NaN is not a capital


def tier_for(cap: float | None, cat: dict) -> str | None:
    if cap is None:
        return None
    name = None
    for t in cat["tiers"]:                            # ascending lower bounds
        if cap >= t["min_capital"]:
            name = t["name"]
    return name


def journey_for(code: str, desc: str, cat: dict) -> dict | None:
    """Primary journey, how it was decided, and the secondary journeys."""
    if not code:
        return None
    cls, div = cat["classes"].get(code[:4]), cat["divisions"].get(code[:2])
    rule = cls or div
    if rule is None:
        return None
    j, conf = str(rule["journey"]), rule["confidence"]
    # A bare division is not a real TSIC code, so it is not shown as one.
    head = f"TSIC {code}" if len(code) == 5 else "Industry"
    why = f"{head} · {rule['name']}" if rule["name"] else head
    hits = []
    for k in cat["journey_keywords"]:
        jj = str(k["journey"])
        if jj not in hits and re.search(k["pattern"], desc or "", re.I):
            hits.append(jj)
    # A weak code gives way to a description that points one other way.
    if conf == "Low" and len(hits) == 1 and hits[0] != j:
        return {"journey": hits[0], "why": f"business description (TSIC {code} is ambiguous)",
                "confidence": "Medium", "secondary": []}
    return {"journey": j, "why": why, "confidence": conf,
            "secondary": [h for h in hits if h != j]}


def flags_for(desc: str, cat: dict) -> list[str]:
    return [f for f, spec in cat["flags"].items() if re.search(spec["pattern"], desc or "", re.I)]


def recommend(lead, cat: dict) -> dict | None:
    """Everything the Suggested products box shows, or None when the lead has no
    usable TSIC code or the catalogue is missing."""
    if not cat:
        return None
    code = tsic(lead.get("tsic_code")) or division(lead.get("tsic_division"))
    desc = str(lead.get("business_description", "") or "")
    desc = "" if desc == "nan" else desc
    m = journey_for(code, desc, cat)
    if m is None:
        return None
    J = cat["journeys"]
    # A lead list may name the journey outright where its TSIC division misleads:
    # the industrial list's landlords are real estate (68), but they let factories
    # and warehouses, not homes.
    jo = str(lead.get("journey", "") or "").split(".")[0]
    if jo in J and jo != m["journey"]:
        m = {**m, "journey": jo, "why": f"{m['why']} · set by the lead list",
             "secondary": [s for s in m["secondary"] if s != jo]}
    tier = tier_for(capital(lead.get("registered_capital")), cat)
    flags = flags_for(desc, cat)

    picked: dict[str, dict] = {}                      # product -> entry, in arrival order

    def add(name: str, reason: str, needs=()):
        if name not in cat["products"]:
            return
        e = picked.setdefault(name, {"name": name, "card": cat["products"][name],
                                     "reasons": [], "needs": [], "size_fit": False})
        if reason not in e["reasons"]:
            e["reasons"].append(reason)
        for n in needs:
            if n not in e["needs"]:
                e["needs"].append(n)

    for jid, label in [(m["journey"], J[m["journey"]]["en"])] + \
                      [(s, J[s]["en"] + " (from description)") for s in m["secondary"]]:
        for row in cat["journey_products"].get(jid, []):
            add(row["product"], label, row.get("needs", []))
    for f in flags:
        for p in cat["flags"][f]["products"]:
            add(p, f"company {cat['flags'][f]['label']}")
    if tier:
        for fam in cat["families"].values():
            if any(p in picked for p in fam["members"]):
                for p in fam["pick"].get(tier, []):
                    add(p, f"package for a {tier.lower()} company")
                    picked[p]["size_fit"] = True

    rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    groups: dict[str, list[dict]] = {}
    for e in picked.values():
        groups.setdefault(e["card"].get("category") or "Other", []).append(e)
    for items in groups.values():
        items.sort(key=lambda e: not e["size_fit"])  # stable: journey order otherwise
    ordered = sorted(groups.items(), key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))

    tier_help = next((t["help"] for t in cat["tiers"] if t["name"] == tier), "")
    return {"journey": J[m["journey"]]["en"], "journey_th": J[m["journey"]]["th"],
            "why": m["why"], "confidence": m["confidence"],
            "secondary": [J[s]["en"] for s in m["secondary"]],
            "tier": tier, "tier_help": tier_help,
            "flags": [cat["flags"][f]["label"] for f in flags],
            "groups": ordered, "count": len(picked)}
