# -*- coding: utf-8 -*-
"""
Pure rules for access points.

An access point is where the road or driveway is left to reach one or more
structures. Address points and driveways store its id. An access point that
is still referenced cannot be deleted.
"""

import math


class GuardState:
    """While the plugin itself removes an access point, the guard stays quiet."""

    suppress = False


class suppressed_guard:
    def __enter__(self):
        GuardState.suppress = True
        return self

    def __exit__(self, *_exc):
        GuardState.suppress = False
        return False


def nearest_access(candidates, x, y, tolerance):
    """
    The id of the closest candidate within ``tolerance``.

    ``candidates`` is an iterable of ``(access_id, x, y)`` in one coordinate
    system. An empty id is ignored. No candidate in range returns ``""``.
    """
    best = ""
    best_d = None
    tol = float(tolerance)
    if tol < 0:
        tol = 0.0
    px, py = float(x), float(y)
    for aid, cx, cy in candidates:
        text = "" if aid is None else str(aid).strip()
        if not text:
            continue
        dist = math.hypot(float(cx) - px, float(cy) - py)
        if dist <= tol and (best_d is None or dist < best_d):
            best = text
            best_d = dist
    return best


def blocked_deletes(deleted_access_ids, address_ids, driveway_ids=()):
    """
    Access point ids that still have a live address or driveway.

    Each argument is an iterable of AccessPointID strings. Address and driveway
    ids must already exclude features sitting in the edit buffer's delete list,
    so undoing an address is allowed to remove the access point it just created.
    """
    deleted = []
    seen = set()
    for raw in deleted_access_ids:
        text = "" if raw is None else str(raw).strip()
        if text and text not in seen:
            seen.add(text)
            deleted.append(text)
    addr_counts = {aid: 0 for aid in deleted}
    drv_counts = {aid: 0 for aid in deleted}
    for raw in address_ids:
        text = "" if raw is None else str(raw).strip()
        if text in addr_counts:
            addr_counts[text] += 1
    for raw in driveway_ids:
        text = "" if raw is None else str(raw).strip()
        if text in drv_counts:
            drv_counts[text] += 1
    blocked = []
    for aid in deleted:
        addresses = addr_counts[aid]
        driveways = drv_counts[aid]
        if addresses or driveways:
            blocked.append({"id": aid, "addresses": addresses, "driveways": driveways})
    return blocked


def _count_phrase(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def deletion_message(blocked):
    """The sentence shown when a delete is undone. Empty when nothing is blocked."""
    if not blocked:
        return ""
    parts = []
    for row in blocked:
        used = []
        if row["addresses"]:
            used.append(_count_phrase(row["addresses"], "address point", "address points"))
        if row["driveways"]:
            used.append(_count_phrase(row["driveways"], "driveway", "driveways"))
        parts.append(f"{row['id']} is still used by " + " and ".join(used))
    lead = "Cannot delete this access point." if len(blocked) == 1 else "Cannot delete these access points."
    return lead + " " + " ".join(parts) + ". Delete or relink them first. The delete was undone."
