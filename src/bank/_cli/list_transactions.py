from pathlib import Path
import argparse
import os
import typing as ty

from bank.firefly import list_firefly_transactions


def main():
    parser = argparse.ArgumentParser(
        "List all the transactions that match a given filter."
    )

    parser.add_argument(
        "firefly_instance", type=str, help="URL of the Firefly III instance to update."
    )
    parser.add_argument(
        "--no-tag",
        action="store_true",
        help="Only list transactions that do not have any tag.",
    )
    parser.add_argument(
        "--no-category",
        action="store_true",
        help="Only list transactions that do not have any category.",
    )
    parser.add_argument(
        "--include-transfers",
        action="store_false",
        default=False,
        help="Also include transfers in the list of transactions.",
    )

    args = parser.parse_args()

    token: ty.Optional[str] = os.environ.get("FIREFLY_TOKEN")
    if token is None:
        token = input("Enter your Firefly III token:\n")

    filters = []
    if args.no_tag:
        filters.append(lambda t: not t.tags)
    if args.no_category:
        filters.append(lambda t: not t.category_name)
    if not args.include_transfers:
        filters.append(lambda t: t.transaction_type != "transfer")
    list_firefly_transactions(args.firefly_instance, token, filters)


if __name__ == "__main__":
    main()
