from datetime import datetime, date, timedelta
import typing as ty
import requests
import logging
from dataclasses import dataclass, field
from copy import deepcopy

logger = logging.getLogger("bank.firefly")

JSON = ty.Dict

T = ty.TypeVar("T")


def _merge_dicts(prev: ty.Dict, new: ty.Dict) -> ty.Dict:
    d = deepcopy(prev)
    d.update(new)
    return d


@dataclass
class FireflyAPIDataClass:

    """Base class for all Firefly API data classes.

    This base class provides methods to translate any implemented Firefly
    API dataclass to and from a JSON (dict-like) representation.

    Class variables that should be provided by the inheriting classes:

    _ATTRIBUTE_TO_API_MAPPING: a mapping that maps attribute names (keys) to
        api names (values). This is needed because some API names are "type"
        (which is a Python built-in) or are not as explicit as they should be.
    _IGNORED_ATTRIBUTES: a set of the names of all the attributes that should
        not be forwarded to the API (i.e. not included in the dictionnary
        translation).
    _DATETIME_FORMAT: a callable that takes a datetime object and returns a
        string. Needed to format datetimes when encountered.
    _DATETIME_PARSE: a callable that takes a string object and return a datetime.
    """

    _ATTRIBUTE_TO_API_MAPPING: ty.ClassVar[ty.Mapping[str, str]] = {"instance_id": "id"}
    _IGNORED_ATTRIBUTES: ty.ClassVar[ty.Set[str]] = {
        "_ATTRIBUTE_TO_API_MAPPING",
        "_IGNORED_ATTRIBUTES",
        "_DATETIME_FORMAT",
        "_DATETIME_PARSE",
        "instance_id",
    }
    _DATETIME_FORMAT: ty.ClassVar[
        ty.Callable[[datetime], str]
    ] = lambda d: d.isoformat()
    _DATETIME_PARSE: ty.ClassVar[
        ty.Callable[[str], datetime]
    ] = lambda s: datetime.fromisoformat(s)

    instance_id: ty.Optional[int]

    @staticmethod
    def _compute_inherited_classvar(
        type_: ty.Type["FireflyAPIDataClass"],
        attribute_name: str,
        attribute_update: ty.Callable[[T, T], T],
    ) -> T:
        if not issubclass(type_, FireflyAPIDataClass):
            raise RuntimeError(
                f"Got an instance of type '{type_.__name__}' which is not a "
                "subclass of FireflyAPIDataClass."
            )
        inherited_classvar: T = getattr(type_, attribute_name)
        while type_ is not FireflyAPIDataClass:
            type_ = type_.__mro__[1]
            inherited_classvar = attribute_update(
                inherited_classvar, getattr(type_, attribute_name)
            )
        return inherited_classvar

    @property
    def attributes(self) -> ty.Iterable[str]:
        IGNORED_ATTRIBUTES = FireflyAPIDataClass._compute_inherited_classvar(
            type(self), "_IGNORED_ATTRIBUTES", lambda s1, s2: set.union(s1, s2)
        )
        yield from (
            name
            for name in type(self).__annotations__
            if name not in IGNORED_ATTRIBUTES
        )

    def to_dict(self) -> ty.Dict[str, ty.Any]:
        """Generic method to translate an instance to a dictionnary."""
        # Recover the right variables (the one from the actual type of the
        # provided instance).

        ATTR_TO_API_MAP = FireflyAPIDataClass._compute_inherited_classvar(
            type(self), "_ATTRIBUTE_TO_API_MAPPING", _merge_dicts
        )
        DATETIME_FORMAT: ty.Callable[
            [datetime], str
        ] = FireflyAPIDataClass._compute_inherited_classvar(
            type(self), "_DATETIME_FORMAT", lambda _, d2: d2
        )
        # Build the resulting dictionnary.
        res: ty.Dict[str, ty.Any] = dict()
        for attribute in self.attributes:
            value = getattr(self, attribute)
            if isinstance(value, datetime):
                value = DATETIME_FORMAT(value)
            if attribute in ATTR_TO_API_MAP:
                attribute = ATTR_TO_API_MAP[attribute]
            if value is not None:
                res[attribute] = value
        return res

    @staticmethod
    def from_json(type_: ty.Type[T], json: ty.Mapping[str, ty.Any], id_: int) -> T:
        """Generic method to construct an instance of type_ from a dictionnary."""
        if not issubclass(type_, FireflyAPIDataClass):
            raise RuntimeError(
                f"Type '{type_.__name__}' is not a subclass of FireflyAPIDataClass."
            )
        ATTR_TO_API_MAP = FireflyAPIDataClass._compute_inherited_classvar(
            type_, "_ATTRIBUTE_TO_API_MAPPING", _merge_dicts
        )
        API_TO_ATTR_MAP = {v: k for k, v in ATTR_TO_API_MAP.items()}
        DATETIME_PARSER: ty.Callable[
            [str], datetime
        ] = FireflyAPIDataClass._compute_inherited_classvar(
            type_, "_DATETIME_PARSE", lambda old, new: new
        )

        init_dict: ty.Dict[str, ty.Any] = {"instance_id": id_}
        for api_attribute, value in json.items():
            if api_attribute in API_TO_ATTR_MAP:
                attr_attribute = API_TO_ATTR_MAP[api_attribute]
            else:
                attr_attribute = api_attribute
            # If the type is a datetime, create the datetime object
            if attr_attribute not in type_.__annotations__:
                # This is a non-registered attribute, we ignore it.
                logger.info(f"Ignoring non-implemented attribute '{api_attribute}'.")
                continue
            annotation = type_.__annotations__[attr_attribute]
            if datetime in ty.get_args(annotation) and value is not None:
                value = DATETIME_PARSER(value)
            init_dict[attr_attribute] = value
        return type_(**init_dict)

    def is_valid(self) -> bool:
        raise NotImplementedError()

    def _get_representation(self) -> str:
        attributes_repr: ty.List[str] = list()
        for attribute in self.attributes:
            val = getattr(self, attribute)
            if val:
                attributes_repr.append(f"{attribute}={val}")
        return type(self).__name__ + "(" + ", ".join(attributes_repr) + ")"


@dataclass
class FireflyTransaction(FireflyAPIDataClass):
    """A Firefly transaction.

    Implemented entries:

    - 'transaction_type': type of the transaction represented. Can be any of
        "withdrawal", "deposit", "transfer", "reconciliation" or
        "opening balance".
        See https://docs.firefly-iii.org/firefly-iii/support/transaction_types/.
    - 'date': date of the transaction.
    - 'value_date': date when the transaction value has been transfered. Might
        be after the date of the transaction. Stored in Firefly as
        'process_date'.
    - 'amount': amout of the transaction. Should be positive when sent to the
        API.
    - 'description': description of the transaction.
    - 'source_id': ID of the source account. For a withdrawal or a transfer,
        this must always be an asset account. For deposits, this must be a
        revenue account. Either source_id or source_name should be set to a
        non-None value.
    - 'source_name': Name of the source account. For a withdrawal or a transfer,
        this must always be an asset account. For deposits, this must be a
        revenue account. Can be used instead of the source_id. If the
        transaction is a deposit, the source_name can be filled in freely: the
        account will be created based on the name. Either source_id or
        source_name should be set to a non-None value.
    - 'destination_id': ID of the destination account. For a deposit or a
        transfer, this must always be an asset account. For withdrawals this
        must be an expense account. Either destination_id or destination_name
        should be set to a non-None value.
    - 'destination_name': Name of the destination account. You can submit the
        name instead of the ID. For everything except transfers, the account
        will be auto-generated if unknown, so submitting a name is enough.
        Either destination_id or destination_name should be set to a non-None
        value.
    - 'budget_id': internal ID of the budget linked with this transaction.
    - 'budget_name': name of the budget linked with this transaction.
    - 'category_name': name of the category to be used. If the category is
        unknown, it will be created.
    - 'bill_name': name of the bill.
    - 'tags': array of tags.
    - 'notes': notes to attach to the transaction.
    - 'sepa_mandate_identifier': SEPA mandate identifier.
    - 'sepa_creditor_identifier': SEPA creditor identifier.


    Missing entries (see https://api-docs.firefly-iii.org/):
    - 'order': Order of this entry in the list of transactions.
    - 'currency_id': Currency ID. Default is the source account's currency, or
        the user's default currency. The value you submit may be overruled by
        the source or destination account.
    - 'currency_code': Currency code. Default is the source account's currency,
        or the user's default currency. The value you submit may be overruled
        by the source or destination account.
    - 'foreign_amount': The amount in a foreign currency.
    - 'foreign_currency_id': Currency ID of the foreign currency. Default is
        null. Is required when you submit a foreign amount.
    - 'foreign_currency_code': Currency code of the foreign currency. Default
        is NULL. Can be used instead of the foreign_currency_id, but this or
        the ID is required when submitting a foreign amount.
    - 'category_id': The category ID for this transaction.
    - 'reconciled': If the transaction has been reconciled already. When you
        set this, the amount can no longer be edited by the user.
    - 'piggy_bank_id': Optional. Use either this or the piggy_bank_name.
    - 'piggy_bank_name': Optional. Use either this or the piggy_bank_id.
    - 'bill_id': Optional. Use either this or the bill_name.
    - 'internal_reference': Reference to internal reference of other systems.
    - 'external_id': Reference to external ID in other systems.
    - 'bund_payment_id': Internal ID of bunq transaction. Field is no longer used
        but still works.
    - 'sepa_cc': SEPA Clearing Code.
    - 'sepa_ct_id': SEPA end-to-end Identifier.
    - 'sepa_country': SEPA Country.
    - 'sepa_ep': SEPA External Purpose indicator.
    - 'sepa_batch_id': SEPA Batch ID.
    - 'interest_date'
    - 'book_date'
    - 'due_date'
    - 'payment_date'
    - 'invoice_date'
    """

    _ATTRIBUTE_TO_API_MAPPING: ty.ClassVar[ty.Mapping[str, str]] = {
        "transaction_type": "type",
        "value_date": "process_date",
        "sepa_mandate_identifier": "sepa_db",
        "sepa_creditor_identifier": "sepa_ci",
    }
    _IGNORED_ATTRIBUTES: ty.ClassVar[ty.Set[str]] = {"budget_name"}

    transaction_type: ty.Literal[
        "withdrawal", "deposit", "transfer", "reconciliation", "opening balance"
    ]
    date: datetime
    value_date: datetime
    amount: float
    description: str
    source_id: ty.Optional[int] = None
    source_name: ty.Optional[str] = None
    destination_id: ty.Optional[int] = None
    destination_name: ty.Optional[str] = None
    budget_id: ty.Optional[int] = None
    budget_name: ty.Optional[str] = None
    category_name: ty.Optional[str] = None
    bill_name: ty.Optional[str] = None
    tags: ty.List[str] = field(default_factory=list)
    notes: ty.Optional[str] = None
    sepa_mandate_identifier: ty.Optional[str] = None
    sepa_creditor_identifier: ty.Optional[str] = None

    def is_valid(self):
        return (self.source_id is not None or self.source_name is not None) and (
            self.destination_id is not None or self.destination_name is not None
        )

    def resolve(self, api: "FireflyClient"):
        if self.source_name:
            self.source_id = api.get_account(self.source_name).instance_id

        if self.destination_name:
            self.destination_id = api.get_account(self.destination_name).instance_id

        if self.budget_name:
            self.budget_id = api.get_budget(self.budget_name)["id"]

    def is_equivalent(self, other: "FireflyTransaction") -> bool:
        return all(
            getattr(self, attr) == getattr(other, attr)
            for attr in [
                "transaction_type",
                "date",
                "value_date",
                "amount",
                "sepa_mandate_identifier",
                "sepa_creditor_identifier",
            ]
        )

    def update_with(self, other: "FireflyTransaction") -> None:
        for attr in self.attributes:
            val_other = getattr(other, attr)
            if val_other is not None:
                setattr(self, attr, val_other)

    def __repr__(self) -> str:
        return self._get_representation()


@dataclass
class FireflyAccount(FireflyAPIDataClass):

    _ATTRIBUTE_TO_API_MAPPING: ty.ClassVar[ty.Mapping[str, str]] = {
        "account_type": "type",
    }
    _IGNORED_ATTRIBUTES: ty.ClassVar[ty.Set[str]] = {"created_at", "updated_at"}

    def __hash__(self):
        return hash(self.iban) if self.iban is not None else hash(self.name)

    def __eq__(self, other):
        return isinstance(other, FireflyAccount) and (
            self.iban == other.iban
            if (self.iban is not None and other.iban is not None)
            else self.name == other.name
        )

    name: str
    account_type: ty.Literal[
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
    iban: ty.Optional[str] = None
    bic: ty.Optional[str] = None
    account_number: ty.Optional[str] = None
    opening_balance: ty.Optional[float] = None
    opening_balance_date: ty.Optional[datetime] = None
    currency_code: ty.Optional[str] = None
    account_role: ty.Optional[
        ty.Literal[
            "defaultAsset",
            "sharedAsset",
            "savingAsset",
            "ccAsset",
            "cashWalletAsset",
        ]
    ] = None
    credit_card_type: ty.Optional[ty.Literal["monthlyFull"]] = None
    monthly_payment_date: ty.Optional[datetime] = None
    liability_type: ty.Optional[ty.Literal["loan", "debt", "mortgage"]] = None
    liability_direction: ty.Optional[ty.Literal["debit", "credit"]] = None
    interest: ty.Optional[float] = None
    interest_period: ty.Optional[ty.Literal["daily", "monthly", "yearly"]] = None
    notes: ty.Optional[str] = None
    created_at: ty.Optional[datetime] = None
    updated_at: ty.Optional[datetime] = None

    def is_valid(self) -> bool:
        return (
            (self.account_type != "asset" or self.account_role is not None)
            and (
                self.account_role != "ccAsset"
                or (
                    self.credit_card_type is not None
                    and self.monthly_payment_date is not None
                )
            )
            and (
                self.account_type != "liability"
                or (
                    self.liability_type is not None
                    and self.interest is not None
                    and self.interest_period is not None
                )
            )
        )

    def __eq__(self, other) -> bool:
        return isinstance(other, FireflyAccount) and self.name == other.name

    def __repr__(self) -> str:
        return self._get_representation()


class FireflyApi:
    def __init__(self, api_hostname: str, token: str):
        self.session = requests.sessions.Session()
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self.api_url = f"{api_hostname}/api/v1/"

    def __build_uri(self, endpoint: str):
        return f"{self.api_url}{endpoint}"

    def post(self, endpoint: str, payload: JSON) -> JSON:
        uri = self.__build_uri(endpoint)
        response = self.session.post(url=uri, json=payload, headers=self.headers)
        log = logger.debug if (response.status_code == 200) else logger.warning
        log(f"POST {uri} returned {response.status_code}.")
        return response.json()

    def get(self, endpoint, params=None):
        uri = self.__build_uri(endpoint)
        response = self.session.get(url=uri, params=params, headers=self.headers)
        log = logger.debug if (response.status_code == 200) else logger.warning
        log(f"GET {uri} returned {response.status_code}.")
        return response.json()

    def delete(self, endpoint, params=None) -> None:
        uri = self.__build_uri(endpoint)
        response = self.session.delete(url=uri, params=params, headers=self.headers)
        log = logger.debug if (response.status_code == 204) else logger.warning
        log(f"DELETE {uri} returned {response.status_code}.")

    def put(self, endpoint: str, payload: JSON):
        uri = self.__build_uri(endpoint)
        response = self.session.put(url=uri, json=payload, headers=self.headers)
        log = logger.debug if (response.status_code == 200) else logger.warning
        log(f"PUT {uri} returned {response.status_code}.")
        return response.json()


class FireflyClient:
    def __init__(self, api_hostname: str, token: str):
        self.api = FireflyApi(api_hostname, token)

    def get_custom(self, endpoint: str, params: ty.Optional[ty.Dict] = None):
        response = self.api.get(endpoint, params=params)
        if "message" in response:
            raise Exception(response["message"])
        return response

    def _iterate_over(
        self, endpoint: str, params: ty.Optional[ty.Dict] = None
    ) -> ty.Iterable[ty.Dict]:
        if params is None:
            params = {}
        if "page" in params:
            del params["page"]

        api_answer = self.get_custom(endpoint, params)
        yield from api_answer["data"]
        for page_number in range(
            api_answer["meta"]["pagination"]["current_page"] + 1,
            api_answer["meta"]["pagination"]["total_pages"] + 1,
        ):
            params["page"] = page_number
            yield from self.get_custom(endpoint, params)["data"]

    def iterate_over_accounts(
        self,
        params: ty.Optional[ty.Dict] = None,
    ) -> ty.Iterable[ty.Tuple[int, ty.Dict]]:
        if params is None:
            params = dict()
        yield from (
            (account_dict["id"], account_dict["attributes"])
            for account_dict in self._iterate_over("accounts", params=params)
        )

    def iterate_over_transactions(
        self,
        params: ty.Optional[ty.Dict] = None,
    ) -> ty.Iterable[ty.Tuple[int, ty.Dict]]:
        if params is None:
            params = dict()
        for transactions_dict in self._iterate_over("transactions", params=params):
            for transaction in transactions_dict["attributes"]["transactions"]:
                yield transaction["transaction_journal_id"], transaction

    def get_account(self, account_name: str) -> FireflyAccount:
        account: ty.List[ty.Tuple[int, ty.Dict[str, ty.Any]]] = [
            (id_, account)
            for id_, account in self.iterate_over_accounts()
            if account["name"] == account_name
        ]

        if len(account) == 0:
            raise Exception(f"account '{account_name}' not found.")

        return FireflyAPIDataClass.from_json(
            FireflyAccount, account[0][1], account[0][0]
        )

    def get_accounts(self) -> ty.List[FireflyAccount]:
        """Perform an API call to recover all the accounts."""
        accounts: ty.List[FireflyAccount] = [
            FireflyAPIDataClass.from_json(FireflyAccount, account, id_)
            for id_, account in self.iterate_over_accounts()
        ]
        return accounts

    def create_account(self, account: FireflyAccount) -> None:
        response = self.api.post(endpoint="accounts", payload=account.to_dict())

        if "errors" in response:
            logger.error(response)
            raise Exception(
                f"request error: {response['message']} Fields {list(response['errors'].keys())}"
            )

        data = response["data"]
        logger.info(
            f" => Account {account} added with id {data['id']} at {data['attributes']['created_at']}"
        )

    def create_account_if_not_present(self, account: FireflyAccount) -> None:
        account_name = account.name
        potential_accounts = [
            account
            for _, account in self.iterate_over_accounts()
            if account["name"] == account_name
        ]
        if not potential_accounts:
            self.create_account(account)

    def delete_all_accounts(self) -> None:
        for account in self._iterate_over("accounts"):
            logger.info(f"Deleting account '{account['attributes']['name']}'.")
            self.api.delete(endpoint=f"accounts/{account['id']}")

    def get_budget(self, budget_name: str) -> ty.Dict:
        budgets = self.get_custom("budgets")
        budget = [
            budget
            for budget in budgets["data"]
            if budget["attributes"]["name"] == budget_name
        ]

        if len(budget) == 0:
            raise Exception(f"budget '{budget_name}' not found.")

        return budget[0]

    def insert_transaction(self, transaction: "FireflyTransaction"):
        response = self.api.post(
            endpoint="transactions", payload={"transactions": [transaction.to_dict()]}
        )

        if "errors" in response:
            logger.error(response)
            raise Exception(
                f"request error: {response['message']} Fields {list(response['errors'].keys())}\n"
                f"Transaction: {transaction}."
            )

        data = response["data"]
        logger.info(
            f" => Transaction {transaction} added with id {data['id']} at {data['attributes']['created_at']}"
        )

    def insert_or_update_transaction(self, transaction: FireflyTransaction):
        tr_date: date = transaction.date
        incr = timedelta(days=1)
        for (
            potential_transaction_id,
            potential_transaction_dict,
        ) in self.iterate_over_transactions(
            params={
                "type": transaction.transaction_type,
                "start": (tr_date - incr).isoformat(),
                "end": (tr_date + incr).isoformat(),
            },
        ):
            potential_transaction: FireflyTransaction = FireflyAPIDataClass.from_json(
                FireflyTransaction, potential_transaction_dict, potential_transaction_id
            )
            if transaction.is_equivalent(potential_transaction):
                potential_transaction.update_with(transaction)
                print(f"Updating transaction {potential_transaction} ...")
                self.api.put(
                    f"transactions/{potential_transaction.instance_id}",
                    {"transactions": [potential_transaction.to_dict()]},
                )
                break
        # If no break fired in the for loop, i.e. no transaction updated.
        else:
            self.insert_transaction(transaction)
