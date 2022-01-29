from pathlib import Path
import argparse
import os
import typing as ty

from bank.firefly import update_firefly_transactions
from bank._paths import CA_RULES


def main():
    parser = argparse.ArgumentParser("Update all transactions on the Firefly account.")

    parser.add_argument(
        "firefly_instance", type=str, help="URL of the Firefly III instance to update."
    )
    parser.add_argument(
        "-r",
        "--rules",
        type=Path,
        default=CA_RULES,
        help="Path to a file containing rules to classify transactions.",
    )

    args = parser.parse_args()

    token: ty.Optional[str] = os.environ.get("FIREFLY_TOKEN")
    if token is None:
        token = input("Enter your Firefly III token:\n")

    update_firefly_transactions(args.firefly_instance, token, args.rules)


if __name__ == "__main__":
    main()
