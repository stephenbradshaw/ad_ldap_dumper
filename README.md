# Introduction
Security focused tool for dumping information from Active Directory via LDAP

This tool seeks to dump a similar set of information from Active Directory Domain Controllers as is retrieved by the LDAP component of Bloodhound collectors such as SharpHound. 

Originally written because I wanted something that worked from *nix systems, which worked reliably under more restrictive circumstances than Bloodhound collectors. e.g. if you can talk to the relevant LDAP port on a Domain Controller, this will grab all the useful data you have the permissions to see, regardless of the state of DNS resolution.

This tool also allows me to very strictly control how and what LDAP queries are executed for environments that have LDAP query based detections/alerting in place.

In its current state, this tool writes its output to a single large indented JSON file. There is also `BETA` level support for converting the output to Bloodhound format, discussed below.

# Requirements

This tool depends on ldap3 and impacket Python modules to work. For kerberos you also need gssapi.

# Authentication options

You can authenticate using either NTLM (password or pass the hash), simple bind, Kerberos, ADCS certificates or anonymous.

Provide the username (`-u USERNAME, --username USERNAME`) in the `DOMAIN\username` format for NTLM or as `username@domain.com` for simple bind. Provide the `LMHASH:NTHASH` hash in place of a password if you wish to use this. `:NTHASH` works too if you dont have a LM hash. You can either specify a password with `-password` or you will get prompted for one if you have attempted an authentication method that requires one.

Use `-k` for Kerberos authentication. On *nix a ccache ticket cache must exist and be referenced by the `KRB5CCNAME` environment variable. Similar to the way Kerberos authentication works for Impacket. I havent tested this on Windows. You will need to provide the domain controller to connect to (`-d` option) as a domain name for this to work. This can work without DNS if you use your host file, but it will be finicky when it comes to case. Try and match the servers SPN.  You might also need to specify a realm (e.g. short domain name) and a `-dc-ip` with those options. Best results usually come from using upper case for the realm.

There is a `-pc`/`--pkcs12_client_cert` options to allow the use of a PKCS12 certificate for ADCS certificate based authentication, or `-cc`/`--pem_client_cert` and `-ck`/`--pem_client_key` options to allow the same using PEM certifcate AND key files (individual cert and key files must be provided if using PEM instead of PKCS12). Use of a PKCS12 certificate will perform an automatic creation of PEM based key and cert files to temp files on disk (required for use by the ldap3 library) which _SHOULD_ be automatically deleted once no longer used (the file names will be output by the logger), but if this worries you, perform the conversion yourself. If used on the plaintext LDAP port this option will trigger a STARTTLS operation to wrap the communication socket automatically on bind, as this authentication approach requires TLS.

The `-no-password` option can be used when attempting to logon as a user with an emtpy password set. You need to specify the username in NTLM format for this to work - the ldap3 module requires that some password be specified for all non anonymous binds, so the tool auto-sets a blank NT hash in this case. It seems to work.

Connect without specifying any authentication details for anonymous access to get some basic server information (and perhaps more if the server is misconfigured).

The `ssl` option is available to use SSL for the LDAP connection for servers that require this, which will connect to the secure LDAP port (636 by default) , and there is also a `start_tls` option for upgrading to a TLS connection on the plain text port (389 by default). The `ssl_protocol` option allows specification of a particular protocol version if desired.


# Information collected

The tool collects information on the following categories of objects. The LDAP query used by default for each category is provided:
* **certauthorities** - `(objectClass=certificationAuthority)`
* **certenrollservices** - `(objectClass=pKIEnrollmentService)`
* **certtemplates** - `(objectClass=pKICertificateTemplate)`
* **containers** - `(objectClass=container)`
* **computers** - `(objectClass=computer)`
* **domains** - `(objectClass=domain)`
* **forests** - `(objectClass=crossRefContainer)`
* **gpos** - `(objectClass=groupPolicyContainer)`
* **groups** - `(objectClass=group)`
* **ous** - `(objectClass=organizationalUnit)`
* **trusted_domains** - `(objectClass=trustedDomain)`
* **users** - `(&(objectClass=user)(|(objectCategory=person)(objectCategory=msDS-GroupManagedServiceAccount)(objectCategory=msDS-ManagedServiceAccount)))`
* **info** - no query, this collects `server.info` associated with the LDAP server connection as available to anonymous binds

If any of the certificate categories are collected, the following query will also be run in the configuration naming context to obtain certificate object parent containers:
* containers - `(|(objectClass=container)(objectClass=configuration))`

The majority of these queries are performed recursively from the root object, however the certificate and forest information is collected from beneath the `configurationNamingContext`.

By default, all the attributes that the user you connect with can see will be collected for each object (the attribute option provided to the query is the ALL_ATTRIBUTES pattern of `*`). Attribute names in the output are case sensitive, and largely match the `lDAPDisplayName` value from the `attributeSchema` object in the schema. 

The exceptions to this naming approach are parsed flag entries, which will have `Flags` appended to the name (e.g. `userAccountControlFlags`) and raw copies of parsed binary types which have `_raw` appended (e.g. `nTSecurityDescriptor_raw`).

There is interpretation or parsing on security descriptor type attributes (`nTSecurityDescriptor`, `msDS-AllowedToActOnBehalfOfOtherIdentity` and `msDS-GroupMSAMembership`), date attributes and certain other interesting flag style attributes such as `userAccountControl`, but in general attributes are returned as is.

The `nTSecurityDescriptor` attribute for each object has the Dacls, owner, group and a few of the control fields parsed, and Sids resolved to a friendly name where possible. The Sacl component are currently not being retrieved. The raw version of the attribute is also still returned, as hexlified binary data.

There is an option (`-custom-query <query>`) to run a single alternate custom LDAP query as opposed to the multiple queries mentioned above. You can also choose to run only a subset of the built in query categories from the list above using the `-methods <comma-seperated-list>` option.

The LDAP schema is also queried by default to help with some of the internal lookups when parsing ACL entries and to filter out missing attributes from queries. You can choose to avoid the schema lookup or only perform this as required (`-only-schema` or `-no-schema`). Expect the quality of the output to be negatively affected if the schema collection is omitted. 

The query used for schema collection is `(|(objectClass=classSchema)(objectClass=attributeSchema))` run underneath the `schemaNamingContext`.

A limited set of attributes are collected for this query, collecting enough information to meet the previously mentioned goals and to enable reconstruction of what attributes apply to what objects.


# Output

The output will be stored in an automatically named json file, unless an output filename is provided with `-o`. There are options to increase logging level (`-loglevel`) and exclude raw output for fields that the tool does parsing on (`-exclude-raw`).

There is a (`BETA` quality) option there to output in a Bloodhound compatible format, discussed below.

The JSON represents an object with the following high level keys by default (although this can change when run with non default options):
* **schema**
* **certauthorities**
* **certenrollservices**
* **certtemplates**
* **containers**
* **computers**
* **domains**
* **forests**
* **gpos**
* **groups**
* **ous**
* **trusted_domains**
* **users**
* **info**
* **meta**


The key names that match that of a category from the previous section contain lists of each collected object of that type. e.g. users in the users key. The schema section contains a dump of a subset of the LDAP schema and the meta section contains various information about the operation of the tool.

Even when run against small environments, this is **A LOT** of information. You will likely need to have a good approach to make sense out of this - I use iPython, and an overview of how to explore the data was covered in a post on my blog [here](https://thegreycorner.com/2023/08/16/iPython-for-cyber-security.html#exploring-data-by-example-active-directory). BloodHound output is also available in `BETA` form, discussed below.


# Evasions

The tool includes the option to introduce delays (`-sleep <time_seconds>`), with optional jitter (`-jitter <jitter_max_seconds>`), between each query it performs in order to avoid detection by tools that correlate queries over time from particular sources. 

You can also provide a JSON file specifying custom queries and/or attributes for each category of information (`-query-config <filename>`), if you have specific queries to use instead of the default ones for evasion or other reasons. File should be in the format of a simple lookup mapping the category name to a new query - the default query will be used for any unmapped values.

Heres a very simple example file overriding the query only:

```
{
    "users": {
        "query": "(objectClass=user)"
    }
}
```

Here is an example overriding the query and the attributes. The minimum attributes of `objectSid,distinguishedName,name` will be added to any in the configured list to prevent errors in the tool.

```
{
    "users": {
        "query": "(objectClass=user)",
        "attributes": [
            "ntSecurityDescriptor",
            "objectSid",
            "distinguishedName",
            "name"
        ]
    }
}
```


# Controlling attributes returned

By default all attributes are collected for the categories of information listed above. This is the `*` attributes query condition. This ensures you get all the relevant information, but also drammatically increases information collected and storage requirements and a lot of the information likely wont be useful. There are a few options that can be used to change which attributes are collected.

As well as the `-query-config <filename>` option mentioned in the previous section, you can also specify `-attributes <attributes_list>` to specify the attributes for each object that will be returned for every query run. The provided value should be a comma seperated list of attributes to query.

The following attributes are needed for the tool to operate and will be added to the list of provided attributes if not already included.

```
objectSid,distinguishedName,name
```

Interpreted attributes for `domain` and `domainShort` will also be added for objects that have an `objectSid` regardless when domain information is collected.

The `-attributes` option is probably best used for custom queries defined using the `-custom-query`, or when using a small number of collection methods using `-methods`, as finding a single set of attributes that work usefully for multiple object types can be difficult. Use of `-query-config` when you have multiple types of objects to collect is recommended.

There is also a `BETA` option to collect only the attributes used to create Bloodhound output files `-bh-attributes`. When used, a particular defined and minimum set of attributes will be collected per object type to fulfil the data requirements for Bloodhound.  Please report any issues.

Precedence of application for options relating to attribute collection is `-query-config`, followed by `-bh-attributes` followed by `-attributes`, meaning that `-query-config` settings have priority, and so on, if multiple attribute options are used at once.

If schema collection is enabled (which it is by default) then any requested attributes not present in the schema will be removed from queries, because they cannot be retrieved and may cause query errors. The removed entries will be listed at `DEBUG` logging level. If you want to see the attributes that are present in the directory you can run a schema collection (e.g. `-only-schema`) and check the `lDAPDisplayName` field for `attributeSchema` objects.


# Bloodhound output

The tool now has `BETA` level support for Bloodhound output. 

The best approach to use for the moment while any kinks in the output are resolved is to run the tool as normal, outputting to json, and then converting the contents to Bloodhound output as a seperate step.

For an example output file of `20240410185809_192.168.1.100_AD_Dump.json` created using a normal execution of the tool, you can do the conversion by running the tool similar to the following:

    ./ad_ldap_dumper.py -bh-output -loglevel DEBUG -i 20240410185809_192.168.1.100_AD_Dump.json

The individual Bloodhound output files will be written individually to the present working directory (these files will not be added to a zip archive like SharpHound does).

Please report any issues experienced using this option.

# Global Catalog Servers

You should be querying a Global Catalog LDAP server in order to maximise the quantity/quality of information collected. Theres a few ways to identify Global Catalog servers:

Do an NSLookup against the domains DNS servers for `gc._msdcs.<domain_name>`. For example for domain `example.com`.

    dig gc._msdcs.example.com


Do a SRV lookup for `_gc._tcp.<domain_name>`. For domain `example.com`.

    dig SRV _gc._tcp.example.com


The tool will now also warn you if you are NOT talking to a Global Catalog by checking the `info.other.isGlobalCatalogReady` property during connection. You can do this using anonymous binds (e.g. without authentication). If loglevel is set to at least `INFO` the tool will also specifically tell you that the server is a Global Catalog or not shortly after a successful connection is made to LDAP.

Something like the following is a lightweight approach (e.g. minimally invasive on the target server) that will allow you to work out if a given server `192.168.1.100` is a Global Catalog server or not.

    ./ad_ldap_dumper.py -d 192.168.1.100 -methods info -loglevel INFO

