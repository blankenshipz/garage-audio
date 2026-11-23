"""python -m garage_audio.ops_mcp [--transport stdio|http] [--config PATH]"""
import argparse
import logging
import sys

from .config import load_bearer_token, load_config
from .context import Context
from .errors import OpsError


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='python -m garage_audio.ops_mcp',
                                     description='Operator MCP server for the garage audio stack.')
    parser.add_argument('--transport', choices=('stdio', 'http'), default='stdio',
                        help='stdio (default) or streamable HTTP at /mcp (bearer token required)')
    parser.add_argument('--config', metavar='PATH',
                        help='JSON configuration file (default: $GARAGE_OPS_MCP_CONFIG, else built-in defaults)')
    parser.add_argument('--check-config', action='store_true',
                        help='validate configuration (and the HTTP token when --transport http), print the '
                             'effective non-secret configuration and exit')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # stdout carries the stdio protocol: all logging goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    try:
        config = load_config(args.config)
        token = load_bearer_token(config) if args.transport == 'http' else None
        if args.transport == 'http' and not token:
            raise OpsError('config_invalid', 'HTTP transport refuses to start without a bearer token: set '
                                             'server.token_file (chmod 600) or GARAGE_OPS_MCP_TOKEN')
        if args.check_config:
            import json
            print(json.dumps(config.public(), indent=2))
            return 0
        from . import transport
        ctx = Context(config)
        if args.transport == 'http':
            transport.run_http(ctx, token)
        else:
            transport.run_stdio(ctx)
    except OpsError as error:
        print(f'garage-audio-ops-mcp: {error.message}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
