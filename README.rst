**This README and the documentation in general is still a work in progress.**

Firefly-III "Crédit Agricole" importer
======================================

This repository contains an importer that is able to get all the transactions from the French bank "Crédit Agricole" and import them into a Firefly-III instance.

Important warnings
------------------

In order to import your transactions, your Crédit Agricole login and password will be needed. I can guarantee that the code **in this repository** will never make any malicious use of these or save them, but I **CANNOT** guarantee that the dependencies will do the same. I personnally checked the code of `dmachard/creditagricole-particuliers <https://github.com/dmachard/creditagricole-particuliers>`_ version 0.7.0 and fixed this version as a dependency, but it is up to you to check the code and be sure that your logins are safe.


Installation
------------

.. highlight:: bash

   git clone git@github.com:nelimee/firefly-iii-credit-agricole-importer.git
   # Activate the virtual environment of your choice
   python -m pip install -e firefly-iii-credit-agricole-importer
   

How to use
----------

Once the package has been correctly installed, you should have access to an executable named ``firefly_update_ca``.

   $ firefly_update_ca --help
   usage: Update Firefly instance with the given account [-h] [-r RULES] firefly_instance credit_agricole_region

   positional arguments:
     firefly_instance      URL of the Firefly III instance to update.
     credit_agricole_region
                           Region in which your Crédit Agricole account is located.
   
   optional arguments:
     -h, --help            show this help message and exit
     -r RULES, --rules RULES
                           Path to a file containing rules to classify transactions.

You can store the token to connect to your Firefly III instance in the environment variable "FIREFLY_TOKEN", or you will be asked to enter it at each execution of ``firefly_update_ca``.

Here is one typical execution obtained on my personnal computer:

   $ firefly_update_ca https://firefly.url pyrenees-gascogne
   Enter your Crédit Agricole account number: 12345678910
   Enter your Crédit Agricole password: 123456
   [ 0.71s] Connecting to the different web services                                                   
   [ 7.01s] Recovering data from Crédit Agricole                                                       
   [ 0.00s] Finding transfers                                                                          
       Creating account if not present 'Compte de Dépôt'                                               
       Creating account if not present 'Livret A'                                                      
   [35.24s] Creating non-existant accounts on Firefly III                                              
           Account was last updated the '2022-01-25 12:00:00+01:00'                                    
       [24.23s] Updating account 'Compte de Dépôt'                                                     
           Account was last updated the '2021-12-31 12:00:00+01:00'                                    
       [ 0.39s] Updating account 'Livret A'                                                            
   [25.27s] Updating Firefly-III database
