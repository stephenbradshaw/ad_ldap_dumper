# Introduction
Security focused tool for dumping information from Active Directory via LDAP

This tool seeks to dump a similar set of information from Active Directory Domain Controllers as is retrieved by the LDAP component of Bloodhound collectors such as SharpHound. 

Originally written because I wanted something that worked from *nix systems, which worked reliably under more restrictive circumstances than Bloodhound collectors. e.g. if you can talk to the relevant LDAP port on a Domain Controller, and authenticate, this will grab all the useful data, regardless of the state of DNS resolution.

This tool also allows me to very strictly control how and what LDAP queries are executed for environments that have LDAP query based detections in place.

In its current state, this tool writes its output to a single large indented JSON file. Im working on adding the option for output in a BloodHound compatible format, but this is only partly implemented at this point in time.

# Requirements

This tool depends on ldap3 and impacket Python modules to work. For kerberos you also need gssapi.

# Authentication options

You can authenticate using either NTLM (password or pass the hash), simple bind, Kerberos or anonymous.

Provide the username (`-u USERNAME, --username USERNAME`) in the `DOMAIN\username` format for NTLM or as `username@domain.com` for simple bind. Provide the `LMHASH:NTHASH` hash in place of a password if you wish to use this. `:NTHASH` works too if you dont have a LM hash. you can either specify a password with `-password` or you will get prompted for one if you have attempted an authentication method that requires one.

Use `-k` for Kerberos authentication. On *nix a ccache ticket cache must exist and be referenced by the `KRB5CCNAME` environment variable. Similar to the way Kerberos authentication works for Impacket. I havent tested this on Windows. You will need to provide the domain controller to connect to (-d option) as a domain name for this to work. This can work without DNS if you use your host file, but it will be finicky when it comes to case. Try and match the servers SPN.  You might also need to specify a realm (e.g. short domain name) and a dc-ip with those options. Best results usually come from using upper case for the realm.

The `-no-password` option can be used when attempting to logon as a user with an emtpy password set. You need to specify the username in NTLM format for this to work - the ldap3 module requires that some password be specified for all non anonymous binds, so we set a blank NT hash in this case. It seems to work.

Connect without specifying any authentication details for anonymous access to get some basic server information (and perhaps more if the server is misconfigured).

The `ssl` option is available to use SSL for the LDAP connection for servers that require this.


# Information collected

The tool collects information on the following categories of objects. The LDAP query used by default for each category is provided:
* containers - (objectClass=container)
* computers - (objectClass=computer)
* domains - (objectClass=domain)
* forests - (objectClass=crossRefContainer)
* gpos - (objectClass=groupPolicyContainer)
* groups - (objectClass=group)
* ous - (objectClass=organizationalUnit)
* trusted_domains - (objectClass=trustedDomain)
* users - (&(objectClass=user)(objectCategory=person))
* info - server.info as available to anonymous binds

There is an option (`-custom-query <query>`) to run an alternate custom LDAP query as opposed to multiple queries that collect the previous info, or to run only a subset of the built in query categories from the list above (`-methods <comma-seperated-list>`).

The LDAP schema is also queried by default to help with some of the internal lookups. You can choose to avoid the schema lookup or only perform this as required (`-only-schema` or `-no-schema`). Expect the quality of the output to be negatively affected if the schema collection is omitted.

Parsed versions of Discretionary Access Control Lists (DACLs) with resolved SIDs are gathered for each object, as well as each other field available to the user performing the query. A few other similar binary fields are also parsed.

# Output

The output will be stored in an automatically named json file, unless an output filename is provided with `-o`. There are options to increase logging level (`-loglevel`) and exclude raw output for fields that the tool does parsing on (`-exclude-raw`).

There is an option there to output in a Bloodhound compatible format, but its **CURRENTLY IMCOMPLETE** due to the amount of work required and the fact that I mainly use the JSON. Pull requests welcome.

The JSON represents an object with the following high level keys by default (although this can change when run with non default options):
* schema
* containers
* computers
* domains
* forests
* gpos
* groups
* ous
* trusted_domains
* users
* info
* meta


The key names that match that of a category from the previous section contain lists of each collected object of that type. e.g. users in the users key. The schema section contains a dump of the LDAP schema and the meta section contains various information about the operation of the tool.

Even when run against small environments, this is **A LOT** of information. You will likely need to have a good approach to make sense out of this - I use iPython, and will look to update this readme in future with some details about how to make effective use of this in various ways.


# Evasions

The tool includes the option to introduce delays (`-sleep <time_seconds>`), with optional jitter (`-jitter <jitter_max_seconds>`), between each query it performs in order to avoid detection by tools that correlate queries over time from particular sources. 

You can also provide a JSON file specifying custom queries for each category of information (`-query-config <filename>`), if you have specific queries to use instead of the default ones for evasion. File should be in the format of a simple lookup mapping the category name to a new query - the default query will be used for any unmapped values.

Heres a very simple example file:

```
{"users": "(objectClass=user)"}
```


