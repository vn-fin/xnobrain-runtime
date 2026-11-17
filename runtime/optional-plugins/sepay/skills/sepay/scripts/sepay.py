#!/usr/bin/env python3
"""Read SePay API v2 through the active XNOBrain profile connection."""

from xnobrain.integrations.provider_reads import (
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

BASES = {
    "sandbox": "https://userapi-sandbox.sepay.vn/v2",
    "production": "https://userapi.sepay.vn/v2",
}
FIELDS = {
    "accounts": "id account_holder_name account_number accumulated last_transaction label account_type active bank_short_name bank_brand_name created_at updated_at".split(),
    "transactions": "id bank_account_id va_id account_number va transaction_date transfer_type amount_in amount_out accumulated transaction_content reference_number code bank_brand_name webhook_success".split(),
    "vas": "id bank_account_id va label active official static created_at updated_at".split(),
    "terminals": "xid tid name created_at".split(),
}
LIMITATIONS = (
    "Provider coverage depends on the bank and connection; this is not a complete bank statement.",
    "Reported accumulated amounts are observations, not independently refreshed bank balances.",
    "Throttling is per process; other callers sharing the outbound IP may trigger HTTP 429.",
)


def parser():
    root = SafeParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    for name in (
        "check",
        "accounts",
        "account",
        "transactions",
        "transaction",
        "vas",
        "va",
        "terminals",
    ):
        cmd = sub.add_parser(name)
        options(cmd)
        if name in {"account", "transaction", "va"}:
            cmd.add_argument("--id", required=True)
        if name in {"transactions", "transaction", "vas", "va", "terminals"}:
            cmd.add_argument("--account", required=True)
        if name in {"accounts", "transactions", "vas", "terminals"}:
            cmd.add_argument("--query")
        if name == "transactions":
            cmd.add_argument("--direction", choices=("in", "out"))
            cmd.add_argument("--from", dest="date_from")
            cmd.add_argument("--to", dest="date_to")
            cmd.add_argument("--today", action="store_true")
    return root


def clean(item, kind):
    item = project(item, FIELDS[kind])
    for key in ("amount_in", "amount_out", "accumulated"):
        if key in item and item[key] is not None and type(item[key]) is not int:
            raise ReadError("invalid_monetary_value")
    return item


def execute(args, factory=Reader):
    environment = configuration("RUNTIME_SEPAY_ENV")
    token = configuration("RUNTIME_SEPAY_API_TOKEN")
    allowed = {
        identifier(item.strip())
        for item in configuration("RUNTIME_SEPAY_ACCOUNT_IDS").split(",")
        if item.strip()
    }
    if environment not in BASES or not token or not allowed:
        raise ReadError("connection_not_configured")
    account = getattr(args, "account", None)
    if args.command == "account":
        account = args.id
    if account is not None and identifier(account) not in allowed:
        raise ReadError("account_not_allowed")
    item_id = identifier(args.id) if hasattr(args, "id") else None
    scope = {"command": args.command, "account_ids": sorted(allowed) if not account else [account]}
    if item_id:
        scope["id"] = item_id
    query = {"timestamp_format": "iso8601"}
    if hasattr(args, "query") and args.query:
        query["q"] = args.query
        scope["query"] = args.query
    kind = {
        "check": "accounts",
        "account": "accounts",
        "transaction": "transactions",
        "va": "vas",
    }.get(args.command, args.command)
    if kind == "accounts":
        path = "/bank-accounts" + (f"/{item_id}" if item_id else "")
    elif kind == "transactions":
        path = "/transactions" + (f"/{item_id}" if item_id else "")
        if not item_id:
            low, high = day_interval(args.date_from, args.date_to, args.today)
            query.update(
                bank_account_id=account,
                transfer_type=args.direction,
                transaction_date_from=low,
                transaction_date_to=high,
                transaction_date_sort="asc",
            )
            scope.update(date_from=low, date_to=high, direction=args.direction)
    elif kind == "vas":
        path = f"/bank-accounts/{account}/va" + (f"/{item_id}" if item_id else "")
    else:
        path = f"/bank-accounts/{account}/terminals"
    reader = factory(
        BASES[environment],
        {"Authorization": f"Bearer {token}"},
        timeout=args.timeout,
        deadline=args.deadline,
        interval=0.4,
    )
    try:
        if item_id:
            payload = reader.get(path, query)
            if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
                raise ReadError("invalid_response")
            rows = [payload["data"]]
        else:
            rows = reader.pages(
                path, query, key="data", max_pages=args.max_pages, page_size=100, sepay=True
            )
        selected = []
        for row in rows:
            if kind == "accounts":
                if str(row.get("id")) not in allowed:
                    continue
            elif kind in {"transactions", "vas"}:
                if str(row.get("bank_account_id")) != account:
                    raise ReadError("account_scope_mismatch")
            selected.append(clean(row, kind))
        if item_id and (len(selected) != 1 or selected[0].get("id") != item_id):
            raise ReadError("resource_scope_mismatch")
        return result("sepay", environment, selected, scope, reader, limitations=LIMITATIONS)
    except ReadError as exc:
        return result("sepay", environment, [], scope, reader, exc, LIMITATIONS)
    except (KeyError, TypeError, AttributeError):
        return result(
            "sepay", environment, [], scope, reader, ReadError("invalid_response"), LIMITATIONS
        )


def main(argv=None):
    try:
        payload = execute(parser().parse_args(argv))
    except ReadError as exc:
        payload = result("sepay", "unconfigured", [], {}, error=exc)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit("Use the sepay_read tool with a Discover plugin connection.")
