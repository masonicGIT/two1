"""
The 21 command line interface.

This command can be invoked as 21, two1, or twentyone. The former is
shorter and easier for use at the CLI; the latter, being alphanumeric,
is preferred within Python or any context where the code needs to be
imported. We have configured setup.py and this code such that the
documentation dynamically updates based on this name.
"""
import sys
import platform
import locale
import click
from path import path
from two1.commands.config import Config
from two1.commands.config import TWO1_CONFIG_FILE
from two1.commands.config import TWO1_VERSION
from two1.lib.blockchain.exceptions import DataProviderUnavailableError
from two1.lib.blockchain.exceptions import DataProviderError
from two1.lib.server.login import check_setup_twentyone_account
from two1.lib.util.decorators import docstring_parameter
from two1.lib.util.exceptions import TwoOneError, UnloggedException
from two1.lib.util.uxstring import UxString
# from two1.commands.update import update_two1_package
from two1.commands.buy import buy
from two1.commands.doctor import doctor
from two1.commands.mine import mine
from two1.commands.log import log
from two1.commands.login import login
from two1.commands.help import help
from two1.commands.status import status
from two1.commands.update import update
from two1.commands.flush import flush
from two1.commands.send import send
from two1.commands.search import search
from two1.commands.rate import rate
from two1.commands.sell import sell
from two1.commands.publish import publish
from two1.commands.join import join


CLI_NAME = str(path(sys.argv[0]).name)
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--config-file',
              envvar='TWO1_CONFIG_FILE',
              default=TWO1_CONFIG_FILE,
              metavar='PATH',
              help='Path to config (default: %s)' % TWO1_CONFIG_FILE)
@click.option('--config',
              nargs=2,
              multiple=True,
              metavar='KEY VALUE',
              help='Overrides a config key/value pair.')
@click.version_option(TWO1_VERSION, message='%(prog)s v%(version)s')
@click.pass_context
@docstring_parameter(CLI_NAME)
def main(ctx, config_file, config):
    """Mine bitcoin and use it to buy and sell digital goods.

\b
Usage
-----
Mine bitcoin, list your balance, and buy a search query without ads.
$ {0} mine
$ {0} status
$ {0} buy search "Satoshi Nakamoto"

\b
For further details on how you can use your mined bitcoin to buy digital
goods both at the command line and programmatically, visit 21.co/learn
"""
    create_wallet_and_account = ctx.invoked_subcommand not in \
                                ('help', 'update', 'publish', 'sell', 'rate', 'search',
                                 'login')
    try:
        cfg = Config(config_file, config, create_wallet=create_wallet_and_account)
    except DataProviderUnavailableError:
        raise TwoOneError(UxString.Error.connection_cli)
    except DataProviderError:
        raise TwoOneError(UxString.Error.server_err)

    if create_wallet_and_account:
        try:
            check_setup_twentyone_account(cfg)
        except UnloggedException:
            sys.exit(1)

    ctx.obj = dict(config=cfg)


main.add_command(buy)
main.add_command(doctor)
main.add_command(mine)
main.add_command(status)
main.add_command(update)
main.add_command(flush)
main.add_command(log)
main.add_command(help)
main.add_command(send)
main.add_command(search)
main.add_command(rate)
main.add_command(sell)
main.add_command(publish)
main.add_command(login)
main.add_command(join)

if __name__ == "__main__":
    if platform.system() == 'Windows':
        locale.setlocale(locale.LC_ALL, 'us')

    main()
