"""Columns a source system computes itself, so a producer never has to set them by hand.

The 9 Nov data model has none: every column in the business team's workbook is entered
or sent by its system. In particular WMS `On hand`, `Available quantity` and `Allocated
quantity` are kept independent on purpose - in the real system they can drift apart, and
that drift is rule R10. The seed and the source simulator still call `derive`, so a
derived column can be added here without touching them.
"""
from __future__ import annotations

import datetime as dt

# Columns `derive` computes, per entity (the simulator shows them read-only while derivation is on).
DERIVED: dict[str, tuple[str, ...]] = {
    "oracle.item": (),
    "buss.sales_order": (),
    "oracle.forecast": (),
    "odi.batch_mapping": (),
    "wms.inventory": (),
    "mips.work_order": (),
}


def derive(entity: str, row: dict, now: dt.datetime | None = None) -> dict:
    """Return `row` with its derived columns recomputed (none in this data model)."""
    return dict(row)
