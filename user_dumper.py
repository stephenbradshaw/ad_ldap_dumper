#!/usr/bin/env python

from ad_ldap_dumper import *


def command_line():
    parser = MyParser()
    input_arg_group = parser.add_argument_group('Operation')
    mgroup = input_arg_group.add_mutually_exclusive_group(required=True)
    mgroup.add_argument('-d', '--domain-controller', type=str, help='Domain controller address to connect to')
    input_arg_group.add_argument('-ssl', action='store_true', default=True, help='Force use of SSL for LDAP connection')
    input_arg_group.add_argument('-port', type=int, default=None, help='Port to connect to. Determined automatically if not specified')
    input_arg_group.add_argument('-query-config', type=str, default=None, help='Provide JSON config file that defines custom LDAP queries and attribute lists for each query category, overriding other settings')
    input_arg_group.add_argument('-attributes', type=str, default=None, help='Provide comma seperated list of object attributes to return for all queries')

    output_arg_group = parser.add_argument_group('Output')
    output_arg_group.add_argument('-loglevel', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default='INFO', help='Set logging level')
    output_arg_group.add_argument('-output', type=str,  help='Output filename. An automatically generated name will be used if not provided.')

    auth_arg_group = parser.add_argument_group('Authentication')
    agroup = auth_arg_group.add_mutually_exclusive_group()
    agroup.add_argument('-u', '--username', type=str, default = '', help='Username, use DOMAIN\\username format for NTLM authentication, user@domain for SIMPLE auth')
    auth_arg_group.add_argument('-password', type=str,  default = '', help='Password, hashes also accepted for NTLM. Will be prompted for if not provided and no-password not set')


    args = parser.parse_args()
    logger = create_logger(args.loglevel, 'UserDumper')
    password = args.password
    if not args.password:
        print('Please enter the password for {}:'.format(args.username))
        password = getpass.getpass()

    if args.attributes:
        if args.attributes in ['+', '*']:
            attributes = attributes
        else:
            attributes = [a.strip() for a in args.attributes.split(',')]
            attributes += [a for a in MINIMUM_ATTRIBUTES if a.lower() not in [b.lower() for b in attributes]]
    else:
        attributes = ldap3.ALL_ATTRIBUTES

    
    dumper = AdDumper(args.domain_controller, username=args.username, password=password, ssl=args.ssl, port=args.port, attributes=attributes, logger=logger, query_config=args.query_config) 
    outputfile = args.output if args.output else '{}_{}_User_Dump.json'.format(dumper.generate_timestamp(), args.domain_controller)

    dumper.connect()
    data = dumper.query(methods=['users'])

    if args.attributes and args.attributes not in ['+', '*']:
        out_attributes = [a.strip() for a in args.attributes.split(',')]
    else:
        all_attributes = []
        for entry in data.get('users', []):
            all_attributes += list(entry.keys())
        out_attributes = list(set(all_attributes))

    process_field = lambda x: x[0] if isinstance(x, list) and len(x) == 1 else '' if x == [] else x
    out_filtered = [{b: process_field(a[b]) for b in out_attributes if b in a} for a in data.get('users', [])]

    open(outputfile, 'w').write(json.dumps(out_filtered, indent=4))



if __name__ == "__main__":
    # execute only if run as a script, helpful if script needs to be debugged
    
    if not check_ipython():
        command_line()

