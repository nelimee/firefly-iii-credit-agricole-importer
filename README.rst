**This README and the documentation in general is still a work in progress.**

Firefly-III "Crédit Agricole" importer
======================================

This repository contains an importer that is able to get all the transactions from the French bank "Crédit Agricole" and import them into a Firefly-III instance.

Important warnings
------------------

In order to import your transactions, your Crédit Agricole login and password will be needed. I can guarantee that the code **in this repository** will never make any malicious use of these or save them, but I **CANNOT** guarantee that the dependencies will do the same. I personnally checked the code of `dmachard/creditagricole-particuliers <https://github.com/dmachard/creditagricole-particuliers>`_ version 0.7.0 and fixed this version as a dependency, but it is up to you to check the code and be sure that your logins are safe.


