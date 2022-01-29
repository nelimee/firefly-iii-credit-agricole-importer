from pathlib import Path
import typing as ty
from datetime import date, datetime, timedelta
import json
import logging
import itertools

from creditagricole_particuliers import Authenticator, Accounts
from creditagricole_particuliers.accounts import Account
from creditagricole_particuliers.operations import Operation
from creditagricole_particuliers.iban import Iban

from bank.firefly import (
    FireflyAPIDataClass,
    FireflyClient,
    FireflyTransaction,
    FireflyAccount,
    update_transaction_with_rules,
)
from bank.rules import (
    Rules,
    extract_information_credit_agricole,
    update_information_keys_to_firefly_inplace,
    InformationContainer,
)
from bank._utils import RunningOperation

logger = logging.getLogger("bank.accessors.ca")

_CA_DATE_FORMAT = "%b %d, %Y %H:%M:%S %p"


def get_authenticator(
    username: str, password: str, region: str = "pyrenees-gascogne"
) -> Authenticator:
    """Build and return the authenticator needed to access the data.

    :param username: the account number (e.g. "01234567890").
    :param password: the 6-digit password (e.g. "123456").
    :param region: the code for the region the account is located in.
    """
    return Authenticator(
        username=username, password=list(map(int, password)), region=region
    )


def get_ca_accounts(authenticator: Authenticator) -> Accounts:
    """Return the accounts of the given authenticator."""
    return Accounts(authenticator)


def _ca_operation_to_firefly_transaction(
    operation: Operation, account: Account, rules: Rules
) -> FireflyTransaction:
    """Build a Firefly transaction out of a given Crédit Agricole operation.

    Operation instances only offer a limited access to the underlying data
    via the Python API, but a full access via the to_json method. As such,
    this method parses the return JSON to build the transaction.

    Example JSON:

        {'dateOperation': 'Jan 5, 2022 12:00:00 AM',
         'dateValeur': 'Jan 5, 2022 12:00:00 AM',
         'typeOperation': '5',
         'codeTypeOperation': '50',
         'familleTypeOperation': '5',
         'libelleOperation': 'Description of the operations',
         'libelleTypeOperation': 'PRELEVEMENT             ',
         'montant': -600.0,
         'idDevise': 'EUR',
         'libelleDevise': '€',
         'libelleComplementaire': "Your room's rent",
         'referenceMandat': 'reference-of-the-sepa-mandat       ',
         'idCreancier': 'identifier-of-the-creancier        ',
         'libelleCash1': '',
         'libelleCash2': '',
         'idCarte': '',
         'indexCarte': -1,
         'referenceClient': 'SOMECAPITALLETTERS                 ',
         'pictogrammeCSS': 'npc-debit',
         'fitid': 'ANIDWITHNUMS0'}

    Below is an explanation of each field:
    - 'dateOperation' is the date at which the operation was recorded. For
      the Crédit Agricole it seems that all the operations are done at
      12:00:00 AM. No idea if it is UTC or Europe/Paris timezone.
    - 'dateValeur' is the date at which the operation entered in value. For
      the Crédit Agricole it seems that all the operations are done at
      12:00:00 AM. No idea if it is UTC or Europe/Paris timezone.
    - 'typeOperation' is the type of the operation. Guessed values are:
      - "5" for bank debits (prélèvements).
      - "6" for issued bank transfers.
      - "7" for received bank transfers.
      - "9" for withdrawals from an ATM.
      - "11" for issued card payment.
      - "12" for contributions or bank fees (card fee for example).
    - 'codeTypeOperation' is an additional code that specifies the actual
      operation. The following codes were found in my operations:
      --------------------------------------
      'typeOperation' -> 'codeTypeOperation'
      --------------------------------------
      "5"             -> "50"
      "6"             -> "26"
      "7"             -> "00"
      "7"             -> "16"
      "9"             -> "36"
      "11"            -> "52"
      "12"            -> "67"
      --------------------------------------
    - 'familleTypeOperation' seems to be exactly equal to typeOperation.
    - 'libelleOperation' is the actual description of the operation.
    - 'libelleTypeOperation' is a description of the type of the operation
      with the following observed values:
      -----------------------------------------
      'codeTypeOperation' -> 'libelleTypeOperation'
      -----------------------------------------
      "50"                -> "PRELEVEMENT"
      "26"                -> "VIREMENT EMIS"
      "00"                -> "VIREMENT EN VOTRE FAVEUR"
      "16"                -> "VI RECU DE L'ETRANGER"
      "36"                -> "RETRAIT AU DISTRIBUTEUR"
      "52"                -> "PAIEMENT PAR CARTE"
      "67"                -> "COTISATION"
      -----------------------------------------
    - 'montant' is the amount of the operation that can be positive (when the
      account receives money) or negative (when the money is transfered out of
      the account).
    - 'idDevise' is the identificator of the used devise. Should probably be
      always 'EUR'.
    - 'libelleDevise' is an UTF-8 representation of the devise. Should probably
      be always '€'.
    - 'libelleComplementaire' is a description for the operation that comes in
      addition to libelleOperation.
    - 'referenceMandat' is the reference of the SEPA mandate if applicable, else
      it is an empty string. Warning: might contain spaces at the end.
    - 'idCreancier' is the IBAN of the creditor. Warning: might contain spaces
      at the end.
    - 'libelleCash1' TODO, seems to be always empty.
    - 'libelleCash2' TODO, seems to be always empty.
    - 'idCarte' TODO, seems to be always empty.
    - 'indexCarte' TODO, seems to be always -1.
    - 'referenceClient' seems to be a field for incoming or outgoing transfers
      that is filled with a client number that is dependent on the other party.
    - 'pictogrammeCSS' identificator of the icon used in the Crédit Agricole web
      page.
    - 'fitid' TODO.

    :param operation: the operation to translate.
    :param rules: instance containing all the rules needed to classify operations.
    """
    opjs = json.loads(operation.as_json())

    information: InformationContainer = InformationContainer(
        {
            "instance_id": None,
            "date": datetime.strptime(
                opjs["dateOperation"] + "+0100", _CA_DATE_FORMAT + "%z"
            ),
            "value_date": datetime.strptime(
                opjs["dateValeur"] + "+0100", _CA_DATE_FORMAT + "%z"
            ),
            "amount": opjs["montant"],
            "description": opjs["libelleOperation"].strip(),
            "notes": opjs["libelleComplementaire"].strip(),
            "sepa_mandate_identifier": opjs["referenceMandat"].strip(),
            "sepa_creditor_identifier": opjs["idCreancier"].strip(),
            "tags": "",
        },
        {
            "operation_type": opjs["libelleTypeOperation"],
            "linked_account": account.account["libelleProduit"],
        },
    )
    information = extract_information_credit_agricole(rules, information)
    missing_keys: ty.Set[str] = information.get_missing_keys()
    if missing_keys:
        # message: str = "Warning: missing key!\n"
        # message += "The key{} {} {} missing from the extracted information.\n".format(
        #     "s" if len(missing_keys) > 1 else "",
        #     ", ".join(f"'{mk}'" for mk in missing_keys),
        #     "are" if len(missing_keys) > 1 else "is",
        # )
        # message += (
        #     f"Please update the rules in '{rules.path}' and restart the import.\n"
        # )
        # message += "Transaction:\n"
        # message += str(opjs)
        message = "{0:<40} {1:<32} {2:<6}".format(
            ",".join(missing_keys), opjs["libelleOperation"], opjs["montant"]
        )
        # logger.warning(message)

    information["tags"] = [
        t.strip() for t in information["tags"].split(",") if t.strip()
    ]
    update_information_keys_to_firefly_inplace(information)

    return FireflyTransaction(**information)


def get_operations(
    account: Account,
    start: ty.Optional[datetime] = None,
    end: ty.Optional[datetime] = None,
    count: int = 100000,
) -> ty.Iterable[Operation]:
    """Get all the matching operations on the given account.

    By default the function will search for operations in the last 3650 days
    (approximately 10 years).

    :param account: the account to recover operations from.
    :param start: all the returned operations should happen after this date.
    :param end: all the returned operations should happen before this date.
    :param count: will return at most count operations.
    """
    if start is None:
        start = datetime.now() - timedelta(days=365 * 10)
    if end is None:
        end = datetime.now()
    if not start < end:
        raise RuntimeError(
            "The timespan in which operations should be recovered is empty."
        )
    yield from account.get_operations(
        date_start=start.strftime("%Y-%m-%d"),
        date_stop=end.strftime("%Y-%m-%d"),
        count=count,
    )


def get_transactions(
    account: Account,
    rules_file: Path,
    start: ty.Optional[datetime] = None,
    end: ty.Optional[datetime] = None,
    count: int = 100000,
) -> ty.Iterable[FireflyTransaction]:
    """Get all the matching transactions on the given account."""
    rules = Rules(rules_file)
    yield from map(
        lambda op: _ca_operation_to_firefly_transaction(op, account, rules),
        get_operations(account, start, end, count),
    )


def get_account_oldest_balance(account: Account) -> ty.Tuple[float, datetime]:
    """Compute and return the oldest possible account balance."""
    current_balance: float = account.get_solde()
    now = datetime.now()
    long_before = now - timedelta(weeks=100 * 52)

    operations = list(get_operations(account, start=long_before, end=now, count=100000))
    for op in operations:
        current_balance -= op.montantOp
    if operations:
        oldest_operation: Operation = operations[-1]
        return current_balance, datetime.strptime(
            oldest_operation.dateOp, _CA_DATE_FORMAT
        )
    else:
        return current_balance, now


AccountType = ty.Literal[
    "asset",
    "expense",
    "import",
    "revenue",
    "cash",
    "liability",
    "liabilities",
    "initial-balance",
    "reconciliation",
]
_ACCOUNT_TYPES: ty.Mapping[str, AccountType] = {
    "EPADIS": "asset",
    "EPABOU": "asset",
    "CPTDAV": "asset",
}


def _get_account_type(account_dict: ty.Mapping[str, ty.Any]) -> AccountType:
    product_familly_code: str = account_dict["codeFamilleProduit"]
    if product_familly_code not in _ACCOUNT_TYPES:
        raise RuntimeError(
            f"Found an account with code '{product_familly_code}' that "
            "is not in _ACCOUNT_TYPES. Please update."
        )
    return _ACCOUNT_TYPES[product_familly_code]


AccountRole = ty.Literal[
    "defaultAsset", "sharedAsset", "savingAsset", "ccAsset", "cashWalletAsset"
]
_ACCOUNT_ROLES: ty.Mapping[str, AccountRole] = {
    "EPADIS": "savingAsset",
    "EPABOU": "savingAsset",
    "CPTDAV": "defaultAsset",
}


def _get_account_role(account_dict: ty.Mapping[str, ty.Any]) -> AccountRole:
    product_familly_code: str = account_dict["codeFamilleProduit"]
    if product_familly_code not in _ACCOUNT_ROLES:
        raise RuntimeError(
            f"Found an account with code '{product_familly_code}' that "
            "is not in _ACCOUNT_ROLES. Please update."
        )
    return _ACCOUNT_ROLES[product_familly_code]


def _ca_account_to_firefly_account(account: Account) -> FireflyAccount:
    """Build a Firefly account out of a given Crédit Agricole account.

    Account instances only offer a limited access to the underlying data
    via the Python API, but a full access via the 'account' attribute. As
    such, this method uses the 'account' attribute dictionnary to build
    the firefly account instance.

    Example dictionnary:

          {'cartes': [],
           'cartesDD': [],
           'codeFamilleContratBam': '30',
           'codeFamilleProduit': 'CPTDAV',
           'codeFamilleProduitBam': '30',
           'codeNatureCompteBam': '1',
           'codeProduit': '1',
           'compteDepotATerme': False,
           'familleProduit': {'code': 'NPC13000',
                              'grandeFamilleProduits': 'COMPTES',
                              'libelle': 'MES COMPTES',
                              'niveau': 3,
                              'pertinence': 99},
           'formulesNBQ': [],
           'grandeFamilleProduitCode': '1',
           'grandeFamilleProduits': 'COMPTES',
           'idDevise': 'EUR',
           'idElementContrat': 'SOMENUMBERSANDLETTERS',
           'idPartenaire': 'SOMENUMBERS',
           'idRoleClient': '1',
           'idSepaMail': 'SOMENUMBERSANDLETTERS',
           'identifiantCompteSupportBam': '',
           'index': 0,
           'indexList': 0,
           'libelleCompte': 'M.       NOM '
                            'PRENOM                                            ',
           'libelleDevise': '€',
           'libellePartenaire': 'M.       NOM PRENOM               ',
           'libellePartenaireBam': 'M.       NOM PRENOM                      ',
           'libelleProduit': 'Compte de Dépôt',
           'libelleUsuelProduit': 'CCHQ      ',
           'meteo': 'SOLEIL',
           'natureCompteBam': 'CCHQ      ',
           'numeroCompte': 'SOMENUMBERS',
           'numeroCompteBam': 'SOMENUMBERS',
           'numeroCompteSupportCreditBam': 'SOMENUMBERS',
           'numeroSousCompteBam': 'SOMENUMBERS',
           'operations': [],
           'operationsInfo': {'hasNext': False, 'listeOperations': []},
           'rolePartenaireCalcule': 'TITULAIRE',
           'solde': 6546489431.98,
           'sousFamilleProduit': {'code': 'RDPx3100',
                                  'grandeFamilleProduits': 'COMPTES',
                                  'libelle': 'Compte',
                                  'niveau': 4,
                                  'pertinence': 99},
           'typeEcranBam': '20',
           'typePartenaire': 'PP',
           'typeProduit': 'compte',
           'valorise': True}

    :param account: the account to translate.
    """
    account_dict: ty.Mapping[str, ty.Any] = account.account
    iban: Iban = account.get_iban()

    opening_balance, opening_balance_date = get_account_oldest_balance(account)
    return FireflyAccount(
        instance_id=None,
        name=account_dict["libelleProduit"],
        account_type=_get_account_type(account_dict),
        iban=iban.ibanCode,
        bic=iban.iban["ibanData"]["ibanData"]["bicCode"],
        account_number=account_dict["numeroCompte"],
        opening_balance=opening_balance,
        # Offseting the creation date by 1 day to avoid issues with
        # transactions being added at exactly the same time the account is
        # created.
        opening_balance_date=opening_balance_date - timedelta(days=1),
        currency_code=account_dict["idDevise"],
        account_role=_get_account_role(account_dict),
    )


def get_accounts(auth: Authenticator) -> ty.Iterable[FireflyAccount]:
    """Get all the accounts."""
    yield from map(_ca_account_to_firefly_account, get_ca_accounts(auth))


def find_matching_transaction(
    transaction: FireflyTransaction,
    other_account_transactions: ty.Mapping[date, ty.List[FireflyTransaction]],
) -> ty.Optional[FireflyTransaction]:
    """Find a matching transaction in the provided transactions.

    A matching transaction is currently defined as a transaction that:
    - happened the same day and
    - has the same amount in absolute value.

    If more than one transaction matches the given transaction, a RuntimeError
    is thrown.

    :param transaction: the transaction we want to match against others.
    :param other_account_transactions: transactions from other accounts that might
        match with the given transaction.
    """
    d: date = transaction.date.date()
    if d not in other_account_transactions:
        return None
    potential_same_day_transactions: ty.List[
        FireflyTransaction
    ] = other_account_transactions[d]
    abs_amount: float = abs(transaction.amount)
    potential_transactions: ty.List[FireflyTransaction] = [
        t for t in potential_same_day_transactions if abs(t.amount) == abs_amount
    ]
    if len(potential_transactions) == 0:
        return None
    elif len(potential_transactions) == 1:
        return potential_transactions[0]
    else:
        # message = f"{len(potential_transactions)} transactions might match! Picking the first one.\n"
        # message += f"Transaction under consideration: {transaction}.\n"
        # message += "Found potentially matching transactions:\n\t- "
        # message += "\n\t- ".join(str(t) for t in potential_transactions)

        # logger.warning(message)
        return potential_transactions[0]


def find_and_replace_by_transfers(
    transactions: ty.Mapping[FireflyAccount, ty.List[FireflyTransaction]]
) -> None:
    """Find matching transactions and replace them by a transfer.

    Matching transactions are transactions that check all of the conditions below:
    1. They are performed on two different accounts.
    2. They are performed at the same date (with a potential small delay).
    3. They have the same amount in absolute value.

    This function explores all the transactions in the given parameter and try to
    find pairs of transactions that verify all of the above conditions.
    """
    # Construct an internal representation that is more convenient.
    dict_transactions: ty.Dict[
        FireflyAccount, ty.Dict[date, ty.List[FireflyTransaction]]
    ] = dict()
    for account, transaction_list in transactions.items():
        dict_transactions[account] = dict()
        for transaction in transaction_list:
            d: date = transaction.date.date()
            dict_transactions[account].setdefault(d, []).append(transaction)
    # Recover a list of the accounts we will explore, and explore each
    # non-ordered pair.
    accounts: ty.List[FireflyAccount] = list(transactions.keys())
    for i, account1 in enumerate(accounts):
        for account2 in accounts[i + 1 :]:
            # For all the transactions found in account 1...
            for transaction in itertools.chain.from_iterable(
                dict_transactions[account1].values()
            ):
                # Try to find a matching transaction
                matching_transaction: ty.Optional[
                    FireflyTransaction
                ] = find_matching_transaction(transaction, dict_transactions[account2])
                if matching_transaction:
                    if transaction.transaction_type == "deposit":
                        transaction.source_name = account2.name
                        transaction.destination_name = account1.name
                    elif transaction.transaction_type == "withdrawal":
                        transaction.source_name = account1.name
                        transaction.destination_name = account2.name
                    transaction.transaction_type = "transfer"
                    # Remove the matching transaction from the 2 data structures at hand.
                    transactions[account2].remove(matching_transaction)
                    dict_transactions[account2][
                        matching_transaction.date.date()
                    ].remove(matching_transaction)


def initialise_or_update_firefly_accounts(
    username: str,
    password: str,
    region: str,
    firefly_url: str,
    firefly_token: str,
    rules_path: Path,
) -> None:
    with RunningOperation("Connecting to the different web services"):
        auth = get_authenticator(username, password, region)
        client = FireflyClient(firefly_url, firefly_token)

    transactions: ty.Dict[FireflyAccount, ty.List[FireflyTransaction]] = dict()

    with RunningOperation("Recovering data from Crédit Agricole"):
        for ca_account in get_ca_accounts(auth):
            firefly_account = _ca_account_to_firefly_account(ca_account)
            transactions[firefly_account] = list()
            for firefly_transaction in get_transactions(
                ca_account, rules_file=rules_path
            ):
                if firefly_transaction.transaction_type == "withdrawal":
                    firefly_transaction.amount = -firefly_transaction.amount
                transactions[firefly_account].append(firefly_transaction)

    # Find transfers
    with RunningOperation("Finding transfers"):
        find_and_replace_by_transfers(transactions)

    with RunningOperation(
        "Creating non-existant accounts on Firefly III"
    ) as running_op:
        new_transactions = dict()
        for account, transaction_list in transactions.items():
            if account.instance_id is None:
                running_op.print(f"Creating account if not present '{account.name}'")
            account = client.create_account_if_not_present(account)
            new_transactions[account] = transaction_list
        transactions = new_transactions

    with RunningOperation("Updating Firefly-III database"):
        for account, transaction_list in transactions.items():
            with RunningOperation(f"Updating account '{account.name}'") as rop:
                last_registered_transaction_date = client.last_transaction_date(account)
                rop.print(
                    f"Account was last updated the '{last_registered_transaction_date}'"
                )
                # We reverse the transaction list to start by the oldest transaction.
                for transaction in reversed(transaction_list):
                    if transaction.date > last_registered_transaction_date:
                        rop.print(
                            f"Inserting '{transaction.description:<32}' of {transaction.amount:>8.2f}€ done the {transaction.date.date()}"
                        )
                        client.insert_transaction(transaction)


def update_firefly_transactions(
    firefly_url: str,
    firefly_token: str,
    rules_path: Path,
) -> None:
    with RunningOperation("Connecting to the different web services"):
        client = FireflyClient(firefly_url, firefly_token)

    rules = Rules(rules_path)

    with RunningOperation("Updating transactions on Firefly III") as running_op:
        for (id_, dict_) in client.iterate_over_transactions():
            old_transaction = FireflyAPIDataClass.from_json(
                FireflyTransaction, dict_, id_
            )
            if old_transaction.transaction_type == "withdrawal":
                old_transaction.amount = -old_transaction.amount
            new_transaction = update_transaction_with_rules(old_transaction, rules)
            if not new_transaction.is_equivalent(old_transaction):
                running_op.print(f"Updating     {new_transaction.description}")
            else:
                running_op.print(f"No change in {new_transaction.description}")


def list_firefly_transactions(
    firefly_url: str,
    firefly_token: str,
    filters: ty.List[ty.Callable[[FireflyTransaction], bool]],
) -> None:
    with RunningOperation("Connecting to the Firefly III web services"):
        client = FireflyClient(firefly_url, firefly_token)

    with RunningOperation(
        "Listing matching transactions from Firefly III"
    ) as running_op:
        for (id_, dict_) in client.iterate_over_transactions():
            transaction = FireflyAPIDataClass.from_json(FireflyTransaction, dict_, id_)
            if all(f(transaction) for f in filters):
                running_op.print(transaction.summary_str())
