import decimal
import getpass
import logging
import logging.handlers
import os
import traceback

import click
from mnemonic import Mnemonic
from jsonrpcclient.exceptions import ReceivedErrorResponse
from path import Path
from two1.lib.blockchain.chain_provider import ChainProvider
from two1.lib.blockchain.twentyone_provider import TwentyOneProvider
from two1.lib.wallet.account_types import account_types
from two1.lib.wallet.base_wallet import convert_to_btc
from two1.lib.wallet.base_wallet import convert_to_satoshis
from two1.lib.wallet.base_wallet import satoshi_to_btc
from two1.lib.wallet import exceptions
from two1.lib.wallet.two1_wallet import Two1Wallet
from two1.lib.wallet.two1_wallet import Wallet
from two1.lib.wallet.daemonizer import get_daemonizer


WALLET_VERSION = "0.1.0"
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])
REQUIRED_DATA_PROVIDER_PARAMS = {'chain': ['chain_api_key_id', 'chain_api_key_secret'],
                                 'twentyone': []}

logger = logging.getLogger('wallet')


def handle_exceptions(f, custom_msg=""):
    """ Decorator for handling exceptions

    Args:
        f (function): The function to decorate. This assumes that f
            is a click wrapper function which will be passed a context
            object as its first argument.

    Returns:
        function: A wrapper function that handles exceptions in f.
    """
    def wrapper(*args, **kwargs):
        try:
            rv = f(*args, **kwargs)
        except Exception as e:
            if hasattr(e, 'message'):
                if e.message == "Timed out waiting for lock":
                    msg = e.message + ". Please try again."
            else:
                tb = e.__traceback__
                if custom_msg:
                    msg = "%s: %s" % (custom_msg, e)
                else:
                    msg = str(e)
            logger.error(msg)
            logger.debug("".join(traceback.format_tb(tb)))
            if not logger.hasHandlers():
                click.echo(msg)

            args[0].exit(code=1)

        return rv

    return wrapper


def log_usage(f):
    """ Decorator for logging function usage

    Args:
        f (function): The function to be logged

    Returns:
        function: A wrapper function that logs usage information
    """
    def wrapper(*args, **kwargs):
        logger.info("%s(args=%r, kwargs=%r)" % (f.__name__, args[1:], kwargs))
        return f(*args, **kwargs)

    return wrapper


def get_passphrase():
    """ Prompts the user for a passphrase.

    Returns:
        str: The user-entered passphrase.
    """
    return getpass.getpass("Passphrase to unlock wallet: ")


@click.pass_context
def validate_data_provider(ctx, param, value):
    """ Validates the data provider sent in via the CLI.

    Args:
        ctx (Click context): Click context object.
        param (str): Parameter that is being validated.
        value (str): Parameter value.
    """
    data_provider_params = {}
    if ctx.obj is None:
        ctx.obj = {}

    if value not in REQUIRED_DATA_PROVIDER_PARAMS:
        ctx.fail("Unknown data provider %s" % value)

    required = REQUIRED_DATA_PROVIDER_PARAMS[value]

    fail = False
    for r in required:
        if r not in ctx.params:
            s = r.replace('_', '-')
            click.echo("--%s is required to use %s." % (s, value))
            fail = True
        else:
            data_provider_params[r] = ctx.params[r]

    if fail:
        ctx.fail("One or more required arguments are missing.")

    dp = None
    if value == 'chain':
        key = ctx.params['chain_api_key_id']
        secret = ctx.params['chain_api_key_secret']

        # validate key and secret for chain data provider
        if len(key) != 32 or len(secret) != 32 or \
           not key.isalnum() or not secret.isalnum():
            ctx.fail("Invalid chain_api_key_id or chain_api_key_secret")

        dp = ChainProvider(api_key_id=key, api_key_secret=secret)
    elif value == 'twentyone':
        dp = TwentyOneProvider()

    ctx.obj['data_provider'] = dp
    ctx.obj['data_provider_params'] = data_provider_params


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--wallet-path', '-wp',
              default=Two1Wallet.DEFAULT_WALLET_PATH,
              metavar='PATH',
              show_default=True,
              help='Path to wallet file')
@click.option('--passphrase', '-p',
              is_flag=True,
              help='Prompt for a passphrase.')
@click.option('--blockchain-data-provider', '-b',
              default='twentyone',
              type=click.Choice(['twentyone', 'chain']),
              show_default=True,
              callback=validate_data_provider,
              help='Blockchain data provider service to use')
@click.option('--chain-api-key-id', '-ck',
              metavar='STRING',
              envvar='CHAIN_API_KEY_ID',
              is_eager=True,
              help='Chain API Key (only if -b chain)')
@click.option('--chain-api-key-secret', '-cs',
              metavar='STRING',
              envvar='CHAIN_API_KEY_SECRET',
              is_eager=True,
              help='Chain API Secret (only if -b chain)')
@click.option('--debug', '-d',
              is_flag=True,
              help='Turns on debugging messages.')
@click.version_option(WALLET_VERSION)
@click.pass_context
def main(ctx, wallet_path, passphrase,
         blockchain_data_provider, chain_api_key_id, chain_api_key_secret,
         debug):
    """ Command-line Interface for the Two1 Wallet
    """
    wp = Path(wallet_path)

    # Initialize some logging handlers
    ch = logging.StreamHandler()
    ch_formatter = logging.Formatter(
        '%(levelname)s: %(message)s')
    ch.setFormatter(ch_formatter)

    if not os.path.exists(wp.dirname()):
        os.makedirs(wp.dirname())
    fh = logging.handlers.TimedRotatingFileHandler(wp.dirname().joinpath("wallet_cli.log"),
                                                   when='midnight',
                                                   backupCount=5)
    fh_formatter = logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s')
    fh.setFormatter(fh_formatter)

    logger.addHandler(ch)
    logger.addHandler(fh)

    fh.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setLevel(logging.DEBUG if debug else logging.WARNING)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    logger.info("Wallet client started.")

    if ctx.obj is None:
        ctx.obj = {}

    ctx.obj['wallet_path'] = wallet_path
    ctx.obj['passphrase'] = passphrase

    if ctx.invoked_subcommand not in ['create', 'restore',
                                      'startdaemon', 'stopdaemon',
                                      'uninstalldaemon']:
        # Check that the wallet path exists
        if not Two1Wallet.check_wallet_file(ctx.obj['wallet_path']):
            click.echo("ERROR: Wallet file does not exist or is corrupt.")
            ctx.exit(code=7)

        p = get_passphrase() if passphrase else ''

        try:
            logger.info("Loading wallet %s ..." % (wp))
            ctx.obj['wallet'] = Wallet(wallet_path=wallet_path,
                                       data_provider=ctx.obj['data_provider'],
                                       passphrase=p)
            logger.info("... loading complete.")
        except exceptions.PassphraseError as e:
            click.echo(str(e))
            ctx.exit(code=1)
        except (TypeError, ValueError) as e:
            logger.error("Internal wallet error. Please report this as a bug.")
            logger.debug("".join(traceback.format_tb(e.__traceback__)))
            ctx.exit(code=2)

        def _on_close():
            try:
                ctx.obj['wallet'].sync_wallet_file()
            except:
                pass

        ctx.call_on_close(_on_close)


@click.command()
@click.pass_context
def startdaemon(ctx):
    """ Starts the daemon
    """
    # Check to sere if we're in a venv and don't do anything if we are
    if os.environ.get("VIRTUAL_ENV"):
        click.echo("Not starting daemon while inside a virtualenv. It can be manually started by doing 'walletd' and backgrounding the process.")
        return

    # Check if the wallet path exists
    if not Two1Wallet.check_wallet_file(ctx.obj['wallet_path']):
        click.echo("ERROR: Wallet does not exist! Not starting daemon.")
        ctx.exit(code=7)

    try:
        d = get_daemonizer()
    except OSError as e:
        logger.debug(str(e))
        click.echo("Error: %s" % e)
        return

    if d.started():
        click.echo("walletd already running.")
        return

    if not d.installed():
        if isinstance(ctx.obj['data_provider'], TwentyOneProvider):
            dpo = dict(provider='twentyone')
        elif isinstance(ctx.obj['data_provider'], ChainProvider):
            dp_params = ctx.obj['data_provider_params']
            dpo = dict(provider='chain',
                       api_key_id=dp_params['chain_api_key_id'],
                       api_key_secret=dp_params['chain_api_key_secret'])

        try:
            d.install(dpo)
        except exceptions.DaemonizerError as e:
            logger.debug(str(e))
            click.echo("Error: %s" % e)
            return

    msg = ""
    try:
        if d.start():
            msg = "walletd successfully started."
        else:
            msg = "walletd not started."
    except exceptions.DaemonizerError as e:
        msg = "Error: %s" % e

    logger.debug(msg)
    click.echo(msg)


@click.command()
@click.pass_context
def stopdaemon(ctx):
    """ Stops the daemon
    """
    # Check to sere if we're in a venv and don't do anything if we are
    if os.environ.get("VIRTUAL_ENV"):
        click.echo("Not stopping any daemons from within a virtualenv.")
        return

    try:
        d = get_daemonizer()
    except OSError as e:
        logger.debug(str(e))
        click.echo("Error: %s" % e)
        return

    msg = ""
    try:
        if d.stop():
            msg = "walletd successfully stopped."
        else:
            msg = "walletd not stopped."
    except exceptions.DaemonizerError as e:
        msg = "Error: %s" % e

    logger.debug(msg)
    click.echo(msg)


@click.command()
@click.pass_context
def uninstalldaemon(ctx):
    """ Uninstalls the daemon from the init system
    """
    try:
        d = get_daemonizer()
    except OSError as e:
        logger.debug(str(e))
        click.echo("Error: %s" % e)
        return

    try:
        d.stop()
        if d.installed():
            rv = d.uninstall()
            if rv:
                msg = "walletd successfully uninstalled from init system."
        else:
            msg = "Unable to uninstall walletd!"
    except exceptions.DaemonizerError as e:
        msg = "Error: %s" % e

    logger.debug(msg)
    click.echo(msg)


@click.command(name="create")
@click.option('--account-type', '-a',
              default=Two1Wallet.DEFAULT_ACCOUNT_TYPE,
              type=click.Choice(list(account_types.keys())),
              show_default=True,
              help='Type of account to create')
@click.option('--testnet', '-tn',
              is_flag=True,
              help="Create a testnet wallet.")
@click.pass_context
@log_usage
def create(ctx, account_type, testnet):
    """ Creates a new wallet
    """
    # txn_data_provider and related params come from the
    # global context.
    passphrase = ""
    if ctx.obj['passphrase']:
        # Let's prompt for a passphrase
        conf = "a"
        i = 0
        while passphrase != conf and i < 3:
            passphrase = getpass.getpass("Enter desired passphrase: ")
            conf = getpass.getpass("Confirm passphrase: ")
            i += 1

        if passphrase != conf:
            ctx.fail("Passphrases don't match. Quitting.")

    options = {"account_type": account_type,
               "passphrase": passphrase,
               "data_provider": ctx.obj['data_provider'],
               "testnet": testnet,
               "wallet_path": ctx.obj['wallet_path']}

    logger.info("Creating wallet with options: %r" % options)
    created = Two1Wallet.configure(options)

    if created:
        # Make sure it opens
        logger.info("Wallet created.")
        try:
            wallet = Two1Wallet(params_or_file=ctx.obj['wallet_path'],
                                data_provider=ctx.obj['data_provider'],
                                passphrase=passphrase)

            click.echo("Wallet successfully created!")

            adder = " (and your passphrase) " if passphrase else " "
            click.echo("Your wallet can be recovered using the following set of words (in that order).")
            click.echo("Please store them%ssafely." % adder)
            click.echo("\n%s\n" % wallet._orig_params['master_seed'])
        except Exception as e:
            logger.debug("Error opening created wallet: %s" % e)
            click.echo("Wallet was not created properly.")
            ctx.exit(code=3)
    else:
        ctx.fail("Wallet was not created.")


@click.command(name="restore")
@click.pass_context
def restore(ctx):
    """ Restore a wallet from a mnemonic
    """
    # Stop daemon if it's running.
    d = None
    try:
        d = get_daemonizer()
    except OSError as e:
        pass

    if d:
        try:
            d.stop()
        except exceptions.DaemonizerError as e:
            click.echo("ERROR: Couldn't stop daemon: %s" % e)
            ctx.exit(code=4)

    # Check to see if the current wallet path exists
    if os.path.exists(ctx.obj['wallet_path']):
        if click.confirm("Wallet file already exists and may have a balance. Do you want to delete it?"):
            os.remove(ctx.obj['wallet_path'])
        else:
            click.echo("Not continuing.")
            ctx.exit(code=4)

    # Ask for mnemonic
    mnemonic = click.prompt("Please enter the wallet's 12 word mnemonic")

    # Sanity check the mnemonic
    m = Mnemonic(language='english')
    if not m.check(mnemonic):
        click.echo("ERROR: Invalid mnemonic.")
        ctx.exit(code=5)

    if click.confirm("Did the wallet have a passphrase?"):
        passphrase = get_passphrase()
    else:
        passphrase = ''

    # Try creating the wallet
    click.echo("\nRestoring...")
    wallet = Two1Wallet.import_from_mnemonic(
        data_provider=ctx.obj['data_provider'],
        mnemonic=mnemonic,
        passphrase=passphrase)

    wallet.to_file(ctx.obj['wallet_path'])
    if Two1Wallet.check_wallet_file(ctx.obj['wallet_path']):
        click.echo("Wallet successfully restored.")
    else:
        click.echo("Wallet not restored.")
        ctx.exit(code=6)


@click.command(name="payoutaddress")
@click.option('--account',
              metavar="STRING",
              default=None,
              help="Account")
@click.pass_context
@handle_exceptions
@log_usage
def payout_address(ctx, account):
    """ Prints the current payout address
    """
    w = ctx.obj['wallet']
    click.echo(w.get_payout_address(account))


@click.command(name="confirmedbalance")
@click.option('--account',
              metavar="STRING",
              default=None,
              help="Account")
@click.pass_context
@handle_exceptions
@log_usage
def confirmed_balance(ctx, account):
    """ Prints the current *confirmed* balance
    """
    w = ctx.obj['wallet']
    cb = w.confirmed_balance(account)
    click.echo("Confirmed balance: %0.8f BTC" %
               convert_to_btc(cb))


@click.command(name="balance")
@click.option('--account',
              metavar="STRING",
              default=None,
              help="Account")
@click.pass_context
@handle_exceptions
@log_usage
def balance(ctx, account):
    """ Prints the current total balance.
    """
    w = ctx.obj['wallet']
    ucb = w.unconfirmed_balance(account)
    click.echo("Total balance (including unconfirmed txns): %0.8f BTC" %
               convert_to_btc(ucb))


@click.command(name='listbalances')
@click.option('--byaddress',
              is_flag=True,
              default=False,
              help="List non-zero balances for each address")
@click.pass_context
@handle_exceptions
@log_usage
def list_balances(ctx, byaddress):
    """ Prints the current balances of each account.
    """
    w = ctx.obj['wallet']
    for a in w.account_names:
        ucb = w.unconfirmed_balance(a)
        cb = w.confirmed_balance(a)
        click.echo("Account: %s\nConfirmed: %0.8f BTC, Total: %0.8f BTC" %
                   (a,
                    convert_to_btc(cb),
                    convert_to_btc(ucb)))

        if byaddress:
            by_addr = w.balances_by_address(a)
            if by_addr:
                click.echo("")
            for addr, balances in by_addr.items():
                if balances['confirmed'] > 0 or \
                   balances['total'] > 0:
                    click.echo("%35s: %0.8f (confirmed), %0.8f (total)" %
                               (addr,
                                convert_to_btc(balances['confirmed']),
                                convert_to_btc(balances['total'])))
        click.echo("")

    click.echo("Account Totals\nConfirmed: %0.8f BTC, Total: %0.8f BTC" %
               (convert_to_btc(w.confirmed_balance()),
                convert_to_btc(w.unconfirmed_balance())))


@click.command(name="sendto")
@click.argument('address',
                type=click.STRING)
@click.argument('amount',
                type=click.STRING)
@click.option('--use-unconfirmed', '-uu',
              is_flag=True,
              default=False,
              show_default=True,
              help="Use unconfirmed inputs if necessary")
@click.option('--fees', '-f',
              type=click.INT,
              default=None,
              show_default=True,
              help="Manually specify the fees (in Satoshis)")
@click.option('--account',
              metavar="STRING",
              multiple=True,
              help="List of accounts to use")
@click.pass_context
@handle_exceptions
@log_usage
def send_to(ctx, address, amount, use_unconfirmed, fees, account):
    """ Send bitcoin to a single address
    """
    w = ctx.obj['wallet']

    # Do we want to confirm if it's larger than some amount?
    try:
        satoshis = int(decimal.Decimal(amount) * satoshi_to_btc)
    except decimal.InvalidOperation as e:
        ctx.fail("'%s' is not a valid amount. Amounts must be in BTC." %
                 (amount))

    logger.info("Sending %d satoshis to %s from accounts = %r" %
                (satoshis, address, list(account)))

    txids = w.send_to(address=address,
                      amount=satoshis,
                      use_unconfirmed=use_unconfirmed,
                      fees=fees,
                      accounts=list(account))
    if txids:
        click.echo("Successfully sent %s BTC to %s. txids:" %
                   (amount, address))
        for t in txids:
            click.echo(t['txid'])


@click.command(name="spreadutxos")
@click.argument('num_addresses',
                type=click.IntRange(min=2, max=100))
@click.argument('threshold',
                type=click.STRING)
@click.option('--account',
              type=click.STRING,
              multiple=True,
              help="List of accounts to use")
@click.pass_context
@handle_exceptions
@log_usage
def spread_utxos(ctx, num_addresses, threshold, account):
    """ Spreads out all UTXOs with value > threshold into
        multiple change addresses.
    """
    w = ctx.obj['wallet']
    try:
        satoshis = int(decimal.Decimal(threshold) * satoshi_to_btc)
    except decimal.InvalidOperation:
        ctx.fail("'%s' is an invalid value for threshold. It must be in BTC." %
                 (threshold))

    txids = w.spread_utxos(threshold=satoshis,
                           num_addresses=num_addresses,
                           accounts=list(account))
    if txids:
        click.echo("Successfully spread UTXOs in the following txids:")
        for t in txids:
            click.echo(t['txid'])


@click.command(name="createaccount")
@click.argument('name',
                metavar="STRING")
@click.pass_context
@handle_exceptions
@log_usage
def create_account(ctx, name):
    """ Creates a named account within the wallet
    """
    w = ctx.obj['wallet']
    rv = w.create_account(name)

    if rv:
        click.echo("Successfully created account '%s'." % name)
    else:
        click.echo("Account creation failed.")


@click.command(name="listaccounts")
@click.pass_context
@handle_exceptions
@log_usage
def list_accounts(ctx):
    """ Lists all accounts in the wallet
    """
    w = ctx.obj['wallet']
    for name, n in sorted(w.account_map.items(), key=lambda x: x[1]):
        click.echo("Account %d: %s" % (n, name))


@click.command(name='listaddresses')
@click.option('--account',
              metavar="STRING",
              multiple=True,
              help="List of accounts to use")
@click.pass_context
@handle_exceptions
@log_usage
def list_addresses(ctx, account):
    """ List all addresses in the specified accounts
    """
    w = ctx.obj['wallet']

    addresses = w.addresses(accounts=list(account))
    for acct, addr_list in addresses.items():
        len_acct_name = len(acct)
        click.echo("Account: %s" % (acct))
        click.echo("---------%s" % ("-" * len_acct_name))

        for addr in addr_list:
            click.echo(addr)

        click.echo("")


@click.command(name="sweep")
@click.argument('address',
                metavar="STRING")
@click.option('--account',
              metavar="STRING",
              multiple=True,
              help="List of accounts to sweep")
@click.pass_context
@handle_exceptions
@log_usage
def sweep(ctx, address, account):
    """ Lists all accounts in the wallet
    """
    w = ctx.obj['wallet']

    txids = w.sweep(address=address,
                    accounts=list(account))

    if txids:
        click.echo("Swept balance in the following transactions:")

    for txid in txids:
        click.echo(txid)


@click.command(name="signmessage")
@click.argument('message',
                metavar="STRING")
@click.argument('address',
                metavar="STRING")
@click.pass_context
@handle_exceptions
@log_usage
def sign_bitcoin_message(ctx, message, address):
    """ Signs an arbitrary message
    """
    w = ctx.obj['wallet']
    sig = w.sign_bitcoin_message(message=message, address=address)
    click.echo("Signature: %s" % sig)


@click.command(name='verifymessage')
@click.argument('message',
                metavar="STRING")
@click.argument('signature',
                metavar="STRING")
@click.argument('address',
                metavar="STRING")
@click.pass_context
@handle_exceptions
@log_usage
def verify_bitcoin_message(ctx, message, signature, address):
    """ Verifies that an arbitrary message was signed by
        the private key corresponding to address
    """
    w = ctx.obj['wallet']
    verified = w.verify_bitcoin_message(message=message,
                                        signature=signature,
                                        address=address)
    if verified:
        click.echo("Verified")
    else:
        click.echo("Not verified")


main.add_command(startdaemon)
main.add_command(stopdaemon)
main.add_command(uninstalldaemon)
main.add_command(create)
main.add_command(restore)
main.add_command(payout_address)
main.add_command(confirmed_balance)
main.add_command(balance)
main.add_command(send_to)
main.add_command(spread_utxos)
main.add_command(create_account)
main.add_command(list_accounts)
main.add_command(list_addresses)
main.add_command(list_balances)
main.add_command(sweep)
main.add_command(sign_bitcoin_message)
main.add_command(verify_bitcoin_message)

if __name__ == "__main__":
    main()