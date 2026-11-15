# -*- coding: utf-8 -*-
"""CSV reports: streets (with use counts and address span) and addresses by community."""

import csv
from collections import Counter, defaultdict
from typing import Optional

from ..streets import names as N
from .engine import AddressContext, parse_number
from .ssap import COMMUNITY_FIELDS


def streets_report(path: str, service, ctx: Optional[AddressContext] = None) -> int:
    """One row per master street: status, scope, alias of, segments, addresses, lowest/highest number."""
    counts = service.counts()
    spans = defaultdict(list)
    if ctx is not None and ctx.addr_acc.has("StreetID"):
        for f in ctx.addr.getFeatures():
            num, _s = parse_number(ctx.addr_acc.get(f, "Add_Number"))
            sid = N.clean(ctx.addr_acc.get(f, "StreetID"))
            if num is not None and sid:
                spans[sid].append(num)
    recs = sorted(service.repo.records(), key=lambda r: r.full_name)
    by_id = {r.street_id: r for r in recs}
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["StreetID", "FullName", "Status", "Scope", "AliasOf", "Segments", "Addresses",
                    "LowestNumber", "HighestNumber"])
        for r in recs:
            seg, adr = counts.get(r.street_id, (0, 0))
            nums = spans.get(r.street_id, [])
            alias = by_id.get(r.alias_of).full_name if r.alias_of in by_id else ""
            w.writerow([r.street_id, r.full_name, r.status, r.scope, alias, seg, adr,
                        min(nums) if nums else "", max(nums) if nums else ""])
    return len(recs)


def addresses_by_community_report(path: str, ctx: AddressContext) -> int:
    """Address counts per community field value (MSAG, ZIP, ESN, municipality …)."""
    tallies = {k: Counter() for k in COMMUNITY_FIELDS}
    total = 0
    for f in ctx.addr.getFeatures():
        total += 1
        for key, (fld, _l, _r) in COMMUNITY_FIELDS.items():
            if ctx.addr_acc.has(fld):
                tallies[key][N.clean(ctx.addr_acc.get(f, fld)) or "(empty)"] += 1
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Field", "Value", "Addresses"])
        for key, counter in tallies.items():
            for value, n in sorted(counter.items()):
                w.writerow([COMMUNITY_FIELDS[key][0], value, n])
        w.writerow(["(all)", "", total])
    return total
