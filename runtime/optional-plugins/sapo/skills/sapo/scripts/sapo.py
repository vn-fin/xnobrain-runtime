#!/usr/bin/env python3
"""Read a configured Sapo store without exporting customer contacts or costs."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from xnobrain.integrations.provider_reads import (
    VN,
    Reader,
    ReadError,
    SafeParser,
    configuration,
    day_interval,
    emit,
    identifier,
    options,
    project,
    result,
)

FIELDS = {
    "store": "id name currency timezone".split(),
    "locations": "id name label active".split(),
    "suppliers": "id name code active".split(),
    "products": "id name title product_type vendor".split(),
    "variants": "id product_id inventory_item_id title sku barcode price unit lot_management".split(),
    "inventory_levels": "id inventory_item_id location_id on_hand available committed incoming reserved".split(),
    "lot_inventory_levels": "id inventory_item_id location_id lot_id lot_number on_hand available expiry_date".split(),
    "orders": "id name code created_on modified_on financial_status fulfillment_status status total_price currency".split(),
    "receive_inventories": "id code created_on modified_on status".split(),
}
LIMITATIONS = (
    "Live compatibility with this store has not been verified by this package.",
    "POS inventory, lots and receipt endpoints depend on the store product and app permissions.",
    "Prices and fractional quantities use decimal strings; receipt costs and customer contacts are omitted.",
)


def parser():
    root = SafeParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    for name in (
        "check",
        "store",
        "locations",
        "suppliers",
        "products",
        "lookup",
        "stock",
        "low",
        "expiring",
        "orders",
        "receipts",
    ):
        cmd = sub.add_parser(name)
        options(cmd)
        if name in {"products", "lookup", "stock", "low"}:
            cmd.add_argument("--query", required=name == "lookup")
        if name in {"stock", "low", "expiring"}:
            cmd.add_argument("--location")
        if name == "low":
            cmd.add_argument("--threshold", default="5")
        if name == "expiring":
            cmd.add_argument(
                "--days", type=int, choices=range(0, 367), default=45, metavar="0..366"
            )
        if name in {"orders", "receipts"}:
            cmd.add_argument("--from", dest="date_from")
            cmd.add_argument("--to", dest="date_to")
            cmd.add_argument("--today", action="store_true")
            cmd.add_argument("--days", type=int)
    return root


def expiry(value, today):
    if not value:
        return None, None, "unknown"
    try:
        if len(str(value)) == 10:
            expires = date.fromisoformat(value)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None, None, "unknown"
            expires = stamp.astimezone(VN).date()
        left = (expires - today).days
        return (
            expires.isoformat(),
            left,
            "expired" if left < 0 else ("expires_today" if left == 0 else "upcoming"),
        )
    except (ValueError, TypeError):
        return None, None, "unknown"


def number(value):
    try:
        parsed = Decimal(str(value))
        if parsed.is_finite():
            return parsed
    except InvalidOperation:
        pass
    raise ReadError("invalid_numeric_value")


def execute(args, factory=Reader):
    store = configuration("RUNTIME_SAPO_STORE")
    token = configuration("RUNTIME_SAPO_ACCESS_TOKEN")
    if not store or not token:
        raise ReadError("connection_not_configured")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", store):
        raise ReadError("invalid_store_slug")
    location = identifier(args.location) if getattr(args, "location", None) else None
    scope = {"command": args.command, "store": store}
    if location:
        scope["location"] = location
    query = getattr(args, "query", None)
    if query:
        scope["query"] = query
    reader = factory(
        f"https://{store}.mysapo.net/admin",
        {"X-Sapo-Access-Token": token},
        timeout=args.timeout,
        deadline=args.deadline,
    )

    def listing(resource, params=None, key=None):
        return reader.pages(
            f"/{resource}.json",
            params or {},
            key=key or resource,
            max_pages=args.max_pages,
            page_size=250,
        )

    def products():
        return listing("products", {"query": query})

    try:
        command = args.command
        if command in {"check", "store"}:
            rows = [project(reader.get("/store.json").get("store"), FIELDS["store"])]
        elif command in {"locations", "suppliers"}:
            rows = [project(row, FIELDS[command]) for row in listing(command)]
        elif command in {"products", "lookup"}:
            found = products()
            # Scan the retrieved variants too; the store's product query may not index barcodes.
            if command == "lookup":
                variants = listing("variants", {"barcode": query})
                variants += listing("variants", {"sku": query})
                exact = {
                    str(v.get("id")): v
                    for v in variants
                    if query in (v.get("barcode"), v.get("sku"))
                }
                rows = [project(row, FIELDS["variants"]) for row in exact.values()]
                if not rows:
                    rows = [
                        project(v, FIELDS["variants"])
                        | {"product_name": p.get("name") or p.get("title")}
                        for p in found
                        for v in p.get("variants", [])
                    ]
            else:
                rows = [
                    project(p, FIELDS["products"])
                    | {"variants": [project(v, FIELDS["variants"]) for v in p.get("variants", [])]}
                    for p in found
                ]
        elif command in {"stock", "low"}:
            threshold = number(args.threshold) if command == "low" else None
            index = {
                str(v["inventory_item_id"]): project(v, FIELDS["variants"])
                | {"product_name": p.get("name") or p.get("title")}
                for p in products()
                for v in p.get("variants", [])
                if v.get("inventory_item_id") is not None
            }
            rows = []
            ids = list(index)
            for offset in range(0, len(ids), 50):
                for row in listing(
                    "inventory_levels",
                    {
                        "inventory_item_ids": ",".join(ids[offset : offset + 50]),
                        "location_ids": location,
                    },
                ):
                    item = str(row.get("inventory_item_id"))
                    if item not in index or (location and str(row.get("location_id")) != location):
                        raise ReadError("resource_scope_mismatch")
                    if threshold is not None and (
                        row.get("on_hand") is None or number(row["on_hand"]) > threshold
                    ):
                        continue
                    rows.append(project(row, FIELDS["inventory_levels"]) | {"product": index[item]})
            reader.partial("inventory_pagination_not_verified")
        elif command == "expiring":
            # Do not use an expiry filter: it would hide lots with unknown dates.
            lots = listing("lot_inventory_levels", {"location_ids": location})
            dates = {}
            ids = sorted({str(lot["lot_id"]) for lot in lots if lot.get("lot_id") is not None})
            for offset in range(0, len(ids), 100):
                dates.update(
                    {
                        str(row["id"]): row
                        for row in listing(
                            "lot_dates", {"ids": ",".join(ids[offset : offset + 100])}, "lots"
                        )
                        if row.get("id") is not None
                    }
                )
            rows = []
            today = datetime.now(VN).date()
            scope.update(as_of_date=today.isoformat(), days=args.days)
            for lot in lots:
                if location and str(lot.get("location_id")) != location:
                    raise ReadError("resource_scope_mismatch")
                if lot.get("on_hand") is None or number(lot["on_hand"]) <= 0:
                    continue
                raw = lot.get("expiry_date") or dates.get(str(lot.get("lot_id")), {}).get(
                    "expiry_date"
                )
                value, left, status = expiry(raw, today)
                if left is None or left <= args.days:
                    rows.append(
                        project(lot, FIELDS["lot_inventory_levels"])
                        | {"expiry_date": value, "days_remaining": left, "expiry_status": status}
                    )
            reader.partial("lot_pagination_not_verified")
        else:
            low, high = day_interval(args.date_from, args.date_to, args.today, args.days)
            scope.update(date_from=low, date_to=high)
            resource = "orders" if command == "orders" else "receive_inventories"
            rows = [
                project(row, FIELDS[resource])
                for row in listing(resource, {"created_on_min": low, "created_on_max": high})
            ]
        return result("sapo", "store", rows, scope, reader, limitations=LIMITATIONS)
    except ReadError as exc:
        return result("sapo", "store", [], scope, reader, exc, LIMITATIONS)
    except (KeyError, TypeError, AttributeError):
        return result(
            "sapo", "store", [], scope, reader, ReadError("invalid_response"), LIMITATIONS
        )


def main(argv=None):
    try:
        payload = execute(parser().parse_args(argv))
    except ReadError as exc:
        payload = result("sapo", "unconfigured", [], {}, error=exc)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit("Use the sapo_read tool with a Discover plugin connection.")
