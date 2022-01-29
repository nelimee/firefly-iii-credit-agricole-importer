"""This module provides a way to filter transactions according to rules.

The main function of this module is "extract_information" that will take a 
JSON-like dictionnary obtained from the banking institution and return a
JSON-like structure that contains the inferred information according to a 
set of rules.

Rules are written in a INI file as can be parsed with Python's configparser 
module.
Each section in the INI file is considered to be a rule and each rule can
contain some keys:
- 'priority' is a mandatory integer-valued key that is used to know when
  the rule should be applied. 
  Warning: rules with a lower priority are applied **first** but the information
  they extract might be overriden by rules with an higher priority, applied 
  after.
- 'condition' is a mandatory string-valued key used to check if the current rule
  should be applied to the entry under consideration.
  The value attached to the 'condition' key should be a valid rule-engine rule.
  See https://zerosteiner.github.io/rule-engine/index.html for more information.
- 'type' is an optional string-valued key that can be any of "withdrawal", 
  "deposit", "transfer", "reconciliation" or "opening balance".
  In theory this is already set beforehand, but the pre-computed value will be 
  overriden if this key is used in a matching rule.
- 'description' is an optional string-valued key that describes the transaction.
  In theory this is already set beforehand, but the pre-computed value will be 
  overriden if this key is used in a matching rule.
- 'source' is an optional string-valued key that contains as value the name of
  the source account.
- 'destination' is an optional string-valued key that contains as value the name 
  of the destination account.
- 'budget' is an optional string-valued key that contains as value the name of 
  the budget that should be attached to the transaction.
- 'category' is an optional string-valued key that contains as value the name of 
  the category the transaction should be attached to.
- 'bill' is an optional string-valued key that contains as value the name of 
  the bill that should be attached to the transaction.
- 'tags' is an optional string-valued key that contains as value a list of
  comma-separated strings that are added as tags to the matching transactions.
  Tags can contain spaces but will be left and right trimmed. As such:
    tags: tag1, t2,t47 , this is a long tag ,last_tag
  will result in the tags:
    ["tag1", "t2", "t47", "this is a long tag", "last_tag"]
- 'notes' is an optional string-valued key that describes further the 
  transaction. In theory this is already set beforehand, but the pre-computed 
  value will be overriden if this key is used in a matching rule.

Notes:
  If multiple rules match and set the same key, the last matching rule will 
  overwrite the content set by the previous ones.
  The only exception to this is the "tags" attribute that is appended to the 
  already existing list of tags for each matching rules.
"""

import configparser
from copy import deepcopy
import typing as ty
from pathlib import Path

import rule_engine as re


class InformationContainer(ty.MutableMapping[str, ty.Any]):
    """This is a helper class that behaves like a dictionnary.

    The goal of this class is to track when a default value is updated
    and to be able to query the non-updated values (i.e. still default)
    if needed.
    """

    _IMPORTANT_RULE_KEYS: ty.Set[str] = {"destination", "source", "type", "category"}

    def __init__(
        self,
        data: ty.MutableMapping[str, ty.Any],
        non_exported_data: ty.Optional[ty.Mapping[str, ty.Any]] = None,
    ):
        self._data: ty.MutableMapping[str, ty.Any] = data
        self._non_exported_data: ty.Mapping[str, ty.Any] = non_exported_data or dict()
        self._default_initialised_keys: ty.Set[str] = set(data.keys())

    def __delitem__(self, key: str) -> None:
        del self._data[key]
        if key in self._default_initialised_keys:
            self._default_initialised_keys.remove(key)

    def __getitem__(self, key: str) -> ty.Any:
        if key in self._data:
            return self._data[key]
        elif key in self._non_exported_data:
            return self._non_exported_data[key]
        else:
            raise KeyError(key)

    def __iter__(self) -> ty.Iterator[ty.Any]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setitem__(self, key: str, val: ty.Any) -> None:
        self._data[key] = val
        if key in self._default_initialised_keys:
            self._default_initialised_keys.remove(key)

    def get_default_initialised_keys(self) -> ty.Set[str]:
        return self._default_initialised_keys

    def rename_key(self, old: str, new: str) -> None:
        """Rename a key of the dictionnary.

        This method allows to rename a key of the dictionnary without
        removing the default-initialised information.
        """
        value = self._data[old]
        del self._data[old]
        self._data[new] = value
        if old in self._default_initialised_keys:
            self._default_initialised_keys.remove(old)
            self._default_initialised_keys.add(new)

    def get_missing_keys(self) -> ty.Set[str]:
        return InformationContainer._IMPORTANT_RULE_KEYS - self._data.keys()

    def get_all_entries(self) -> ty.Mapping[str, ty.Any]:
        res = deepcopy(self._data)
        res.update(self._non_exported_data)
        return res


JSON = ty.MutableMapping[str, ty.Any]

_RULE_KEY_TO_FIREFLYTRANSACTION_ARGS: ty.Mapping[str, str] = {
    "source": "source_name",
    "destination": "destination_name",
    "type": "transaction_type",
    "category": "category_name",
    "bill": "bill_name",
    "budget": "budget_name",
}


def update_information_keys_to_firefly_inplace(
    information: InformationContainer,
) -> None:
    """Update the keys of the given mapping.

    :py:meth:`bank.firefly.FireflyTransaction.__init__` is declared with
    some parameters names that are combersome or long for different reasons.
    For example:
    - 'transaction_type' is not simply 'type' because the identifier 'type'
      is already used for a Python built-in.
    - 'source_{id,name}' are split in 2 for internal reasons and it should
      stay like that for the moment.
    - 'destination_{id,name}' same as above.

    As it is convenient to be able to update the `information` instance
    in-place and to build the :py:class:`FireflyTransaction` instance with
    `FireflyTransaction(**information)`, some of the aforementionned keys
    should be renamed to comply with the
    :py:meth:`bank.firefly.FireflyTransaction.__init__`
    parameter naming.
    """
    for rule_key, firefly_key in _RULE_KEY_TO_FIREFLYTRANSACTION_ARGS.items():
        if rule_key in information:
            information.rename_key(rule_key, firefly_key)


def update_firefly_to_information_keys_inplace(
    information: InformationContainer,
) -> None:
    """Update the keys of the given mapping.

    Perform the reverse of
    :py:meth:`update_information_keys_to_firefly_inplace`.
    """
    for rule_key, firefly_key in _RULE_KEY_TO_FIREFLYTRANSACTION_ARGS.items():
        if firefly_key in information:
            information.rename_key(firefly_key, rule_key)


class Rule:
    _MANDATORY_KEYS = {"priority", "condition"}

    @staticmethod
    def _replace_value(raw_value: str, data: InformationContainer) -> str:
        try:
            return raw_value.format(**data.get_all_entries())
        except KeyError as e:
            (first_missing_key,) = e.args
            raise RuntimeError(
                f"Could not find the key '{first_missing_key}' in the provided data."
            )

    @staticmethod
    def _check_mandatory_fields(
        rule_name: str, config: ty.Mapping[str, ty.Any]
    ) -> None:
        """Raise an exception if a mandatory field is missing."""
        missing_keys = Rule._MANDATORY_KEYS - config.keys()
        if missing_keys:
            raise RuntimeError(
                f"Rule '{rule_name}' is missing the mandatory field(s) "
                + " and ".join(f"'{mkey}'" for mkey in missing_keys)
            )

    def __init__(self, rule_name: str, config: ty.Mapping[str, ty.Any]) -> None:
        Rule._check_mandatory_fields(rule_name, config)
        self._priority: int = int(config["priority"])
        self._condition: re.Rule = re.Rule(config["condition"])

        other_keys: ty.Set[str] = set(config.keys()) - Rule._MANDATORY_KEYS
        self._information: ty.Dict[str, ty.Any] = {k: config[k] for k in other_keys}

    def apply_in_place(self, information: InformationContainer) -> None:
        """Apply the rule and modify in place the provided information."""
        if self._condition.matches(information):
            for key, value in self._information.items():
                if key == "tags" and key in information:
                    # Append tags instead of replacing the value
                    if information[key]:
                        # If the tags are not empty, add a delimiter.
                        information[key] += ","
                    information[key] += Rule._replace_value(value, information)
                else:
                    information[key] = Rule._replace_value(value, information)


class Rules:
    def __init__(self, rules: Path) -> None:
        """Initialise the rules.

        :param rules: path to the INI file containing the rules to apply.
        """
        self._path = rules.absolute()
        if not self._path.is_file():
            raise FileNotFoundError(f"Cannot find rules at '{rules}'.")

        config = configparser.ConfigParser(delimiters=":")
        config.read(self._path)

        self._rules: ty.List[Rule] = [
            Rule(rname, config[rname]) for rname in config if rname != "DEFAULT"
        ]
        self._rules.sort(key=lambda r: r._priority)

    def apply_rules(self, information: InformationContainer) -> InformationContainer:
        for rule in self._rules:
            rule.apply_in_place(information)

        return information

    @property
    def path(self) -> Path:
        return self._path


def extract_information_credit_agricole(
    rules: Rules, already_extracted_information: InformationContainer
) -> InformationContainer:
    """Extract information from the data returned by the Credit Agricole API.

    :param rules: the Rules instance containing all the rules to apply.
    :param already_extracted_information: a JSON-like object that contains
        already extracted data that might be used to compute the value of the
        newly added information.
    :returns: a JSON-like object with possible keys in {priority, rule,
        transaction_type, description, source, destination, budget, category,
        bill, tags, notes}

    """
    return rules.apply_rules(already_extracted_information)
