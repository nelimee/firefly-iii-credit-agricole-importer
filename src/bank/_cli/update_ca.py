from pathlib import Path
import argparse
import os
import typing as ty

from bank.accessors.ca import initialise_firefly_accounts
from bank._paths import CA_RULES


def main():
    parser = argparse.ArgumentParser("Update Firefly instance with the given account")

    parser.add_argument(
        "firefly_instance", type=str, help="URL of the Firefly III instance to update."
    )
    parser.add_argument(
        "credit_agricole_region",
        type=str,
        help="Region in which your Crédit Agricole account is located.",
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
    ca_account_number: str = input("Enter your Crédit Agricole account number: ")
    ca_account_password: str = input("Enter your Crédit Agricole password: ")

    initialise_firefly_accounts(
        ca_account_number,
        ca_account_password,
        args.credit_agricole_region,
        args.firefly_instance,
        token,
        args.rules,
    )
