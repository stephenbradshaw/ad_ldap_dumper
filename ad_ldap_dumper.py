#!/usr/bin/env python
import sys 
import argparse
import ldap3
import ssl
import json
import time
import logging
import os
import tempfile
import random
import getpass
import struct
import typing
from functools import reduce
from binascii import hexlify, unhexlify
from logging import Logger
from ldap3 import Server, Connection, ALL, Tls, SASL, KERBEROS, EXTERNAL, AUTO_BIND_TLS_BEFORE_BIND
from ldap3.utils.ciDict import CaseInsensitiveDict
from impacket.ldap.ldaptypes import ACE, ACCESS_ALLOWED_OBJECT_ACE, ACCESS_MASK, LDAP_SID, SR_SECURITY_DESCRIPTOR
from datetime import datetime, timedelta
from impacket.uuid import bin_to_string
from OpenSSL.crypto import load_certificate, FILETYPE_ASN1
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization



# TODO: look at these properties, make sure they are collected and consider evasion, and search for more: https://github.com/elastic/detection-rules/blob/374f21fbc46e0bc75fbc606f24bd8381b438d329/rules/windows/credential_access_ldap_attributes.toml#L19
# FEATURE: restrict attributes returned based on an analysis of the schema? There might be a relevant option in the Connection for this..
# FEATURE: Add option to split output into seperate files based on top level key names?
# FEATURE: Optional retrieval and parsing of SACL for admin connections?

# Template for Kerberos config file
# only needed when DNS not working
KRB_CONF_TEMPLATE = '''
[libdefaults]
    default_realm = [REALM]
    
[realms]
    [REALM] = {
        kdc = [KDC]
    }
'''

#dns_canonicalize_hostname = false
#canonicalize = true

# required attributes used by the tool
MINIMUM_ATTRIBUTES = [
    'objectSid',
    'distinguishedName',
    'name'
]


# https://docs.microsoft.com/en-us/openspecs/windows_protocols/ms-adts/1522b774-6464-41a3-87a5-1e5633c3fbbb
# https://docs.microsoft.com/en-au/windows/win32/adschema/classes-all?redirectedfrom=MSDN
OBJECT_TYPES = {
    'ee914b82-0a98-11d1-adbb-00c04fd8d5cd': 'Abandon-Replication',
    '440820ad-65b4-11d1-a3da-0000f875ae0d': 'Add-GUID',
    '1abd7cf8-0a99-11d1-adbb-00c04fd8d5cd': 'Allocate-Rids',
    '68b1d179-0d15-4d4f-ab71-46152e79a7bc': 'Allowed-To-Authenticate',
    'edacfd8f-ffb3-11d1-b41d-00a0c968f939': 'Apply-Group-Policy',
    '0e10c968-78fb-11d2-90d4-00c04f79dc55': 'Certificate-Enrollment',
    'a05b8cc2-17bc-4802-a710-e7c15ab866a2': 'Certificate-AutoEnrollment',
    '014bf69c-7b3b-11d1-85f6-08002be74fab': 'Change-Domain-Master',
    'cc17b1fb-33d9-11d2-97d4-00c04fd8d5cd': 'Change-Infrastructure-Master',
    'bae50096-4752-11d1-9052-00c04fc2d4cf': 'Change-PDC',
    'd58d5f36-0a98-11d1-adbb-00c04fd8d5cd': 'Change-Rid-Master',
    'e12b56b6-0a95-11d1-adbb-00c04fd8d5cd': 'Change-Schema-Master',
    'e2a36dc9-ae17-47c3-b58b-be34c55ba633': 'Create-Inbound-Forest-Trust',
    'fec364e0-0a98-11d1-adbb-00c04fd8d5cd': 'Do-Garbage-Collection',
    'ab721a52-1e2f-11d0-9819-00aa0040529b': 'Domain-Administer-Server',
    '69ae6200-7f46-11d2-b9ad-00c04f79f805': 'DS-Check-Stale-Phantoms',
    '2f16c4a5-b98e-432c-952a-cb388ba33f2e': 'DS-Execute-Intentions-Script',
    '9923a32a-3607-11d2-b9be-0000f87a36b2': 'DS-Install-Replica',
    '4ecc03fe-ffc0-4947-b630-eb672a8a9dbc': 'DS-Query-Self-Quota',
    '1131f6aa-9c07-11d1-f79f-00c04fc2dcd2': 'DS-Replication-Get-Changes',
    '1131f6ad-9c07-11d1-f79f-00c04fc2dcd2': 'DS-Replication-Get-Changes-All',
    '89e95b76-444d-4c62-991a-0facbeda640c': 'DS-Replication-Get-Changes-In-Filtered-Set',
    '1131f6ac-9c07-11d1-f79f-00c04fc2dcd2': 'DS-Replication-Manage-Topology',
    'f98340fb-7c5b-4cdb-a00b-2ebdfa115a96': 'DS-Replication-Monitor-Topology',
    '1131f6ab-9c07-11d1-f79f-00c04fc2dcd2': 'DS-Replication-Synchronize',
    '05c74c5e-4deb-43b4-bd9f-86664c2a7fd5': 'Enable-Per-User-Reversibly-Encrypted-Password',
    'b7b1b3de-ab09-4242-9e30-9980e5d322f7': 'Generate-RSoP-Logging',
    'b7b1b3dd-ab09-4242-9e30-9980e5d322f7': 'Generate-RSoP-Planning',
    '7c0e2a7c-a419-48e4-a995-10180aad54dd': 'Manage-Optional-Features',
    'ba33815a-4f93-4c76-87f3-57574bff8109': 'Migrate-SID-History',
    'b4e60130-df3f-11d1-9c86-006008764d0e': 'msmq-Open-Connector',
    '06bd3201-df3e-11d1-9c86-006008764d0e': 'msmq-Peek',
    '4b6e08c3-df3c-11d1-9c86-006008764d0e': 'msmq-Peek-computer-Journal',
    '4b6e08c1-df3c-11d1-9c86-006008764d0e': 'msmq-Peek-Dead-Letter',
    '06bd3200-df3e-11d1-9c86-006008764d0e': 'msmq-Receive',
    '4b6e08c2-df3c-11d1-9c86-006008764d0e': 'msmq-Receive-computer-Journal',
    '4b6e08c0-df3c-11d1-9c86-006008764d0e': 'msmq-Receive-Dead-Letter',
    '06bd3203-df3e-11d1-9c86-006008764d0e': 'msmq-Receive-journal',
    '06bd3202-df3e-11d1-9c86-006008764d0e': 'msmq-Send',
    'a1990816-4298-11d1-ade2-00c04fd8d5cd': 'Open-Address-Book',
    '1131f6ae-9c07-11d1-f79f-00c04fc2dcd2': 'Read-Only-Replication-Secret-Synchronization',
    '45ec5156-db7e-47bb-b53f-dbeb2d03c40f': 'Reanimate-Tombstones',
    '0bc1554e-0a99-11d1-adbb-00c04fd8d5cd': 'Recalculate-Hierarchy',
    '62dd28a8-7f46-11d2-b9ad-00c04f79f805': 'Recalculate-Security-Inheritance',
    'ab721a56-1e2f-11d0-9819-00aa0040529b': 'Receive-As',
    '9432c620-033c-4db7-8b58-14ef6d0bf477': 'Refresh-Group-Cache',
    '1a60ea8d-58a6-4b20-bcdc-fb71eb8a9ff8': 'Reload-SSL-Certificate',
    '7726b9d5-a4b4-4288-a6b2-dce952e80a7f': 'Run-Protect_Admin_Groups-Task',
    '91d67418-0135-4acc-8d79-c08e857cfbec': 'SAM-Enumerate-Entire-Domain',
    'ab721a54-1e2f-11d0-9819-00aa0040529b': 'Send-As',
    'ab721a55-1e2f-11d0-9819-00aa0040529b': 'Send-To',
    'ccc2dc7d-a6ad-4a7a-8846-c04e3cc53501': 'Unexpire-Password',
    '280f369c-67c7-438e-ae98-1d46f3c6f541': 'Update-Password-Not-Required-Bit',
    'be2bb760-7f46-11d2-b9ad-00c04f79f805': 'Update-Schema-Cache',
    'ab721a53-1e2f-11d0-9819-00aa0040529b': 'User-Change-Password',
    '00299570-246d-11d0-a768-00aa006e0529': 'User-Force-Change-Password',
    '3e0f7e18-2c7a-4c10-ba82-4d926db99a3e': 'DS-Clone-Domain-Controller',
    '084c93a2-620d-4879-a836-f0ae47de0e89': 'DS-Read-Partition-Secrets',
    '94825a8d-b171-4116-8146-1e34d8f54401': 'DS-Write-Partition-Secrets',
    '4125c71f-7fac-4ff0-bcb7-f09a41325286': 'DS-Set-Owner',
    '88a9933e-e5c8-4f2a-9dd7-2527416b8092': 'DS-Bypass-Quota',
    '9b026da6-0d3c-465c-8bee-5199d7165cba': 'DS-Validated-Write-Computer',
    'e362ed86-b728-0842-b27d-2dea7a9df218': 'ms-DS-ManagedPassword',
    '037088f8-0ae1-11d2-b422-00a0c968f939': 'rASInformation',
    '3e0abfd0-126a-11d0-a060-00aa006c33ed': 'sAMAccountName',
    '3f78c3e5-f79a-46bd-a0b8-9d18116ddc79': 'msDS-AllowedToActOnBehalfOfOtherIdentity',
    '46a9b11d-60ae-405a-b7e8-ff8a58d456d2': 'tokenGroupsGlobalAndUniversal',
    '47cf3000-0019-4754-8c71-da7b9a2d5349': '47cf3000-0019-4754-8c71-da7b9a2d5349', # could not find
    '4828cc14-1437-45bc-9b07-ad6f015e5f28': 'inetOrgPerson',
    '4c164200-20c0-11d0-a768-00aa006e0529': 'userAccountRestrictions',
    '5805bc62-bdc9-4428-a5e2-856a0f4c185e': 'terminalServerLicenseServer',
    '59ba2f42-79a2-11d0-9020-00c04fc2d3cf': 'generalInformation',
    '5b47d60f-6090-40b2-9f37-2a4de88f3063': 'msDS-KeyCredentialLink',
    '5f202010-79a5-11d0-9020-00c04fc2d4cf': 'logonInformation',
    '6db69a1c-9422-11d1-aebd-0000f80367c1': 'terminalServer',
    '72e39547-7b18-11d1-adef-00c04fd8d5cd': 'validatedDNSHostName',
    '736e4812-af31-11d2-b7df-00805f48caeb': 'trustedDomain',
    '77b5b886-944a-11d1-aebd-0000f80367c1': 'personalInformation',
    '91e647de-d96f-4b70-9557-d63ff4f3ccd8': 'privateInformation',
    'b7c69e6d-2cc7-11d2-854e-00a0c983f608': 'tokenGroups',
    'b8119fd0-04f6-4762-ab7a-4986c76b3f9a': 'domainOtherParameters',
    'bc0ac240-79a9-11d0-9020-00c04fc2d4cf': 'groupMembership',
    'bf967950-0de6-11d0-a285-00aa003049e2': 'description',
    'bf967953-0de6-11d0-a285-00aa003049e2': 'displayName',
    'bf967a7f-0de6-11d0-a285-00aa003049e2': 'userCertificate',
    'bf967a86-0de6-11d0-a285-00aa003049e2': 'computer',
    'bf967a9c-0de6-11d0-a285-00aa003049e2': 'organizationalUnit',
    'bf967aa8-0de6-11d0-a285-00aa003049e2': 'printer',
    'bf967aba-0de6-11d0-a285-00aa003049e2': 'user',
    'c47d1819-529b-4c8a-8516-4f273a07e43c': 'c47d1819-529b-4c8a-8516-4f273a07e43c', # could not find
    'c7407360-20bf-11d0-a768-00aa006e0529': 'domainPassword',
    'e45795b2-9455-11d1-aebd-0000f80367c1': 'emailInformation',
    'e45795b3-9455-11d1-aebd-0000f80367c1': 'webInformation',
    'e48d0154-bcf8-11d1-8702-00c04fb96050': 'publicInformation',
    'ea1b7b93-5e48-46d5-bc6c-4df4fda78a35': 'msTPM-TpmInformationForComputer',
    'f3a64788-5306-11d1-a9c5-0000f80367c1': 'servicePrincipalName',
    'bf967aa5-0de6-11d0-a285-00aa003049e2': 'organizationalUnit',
    'bf967a9c-0de6-11d0-a285-00aa003049e2': 'group',
    '5cb41ed0-0e4c-11d0-a286-00aa003049e2': 'contact',
    '19195a5a-6da0-11d0-afd3-00c04fd930c9': 'domain',
    'f30e3bc2-9ff0-11d1-b603-0000f80367c1': 'groupPolicyContainer',
    '4c164200-20c0-11d0-a768-00aa006e0529': 'User-Account-Restrictions',
    'ea1dddc4-60ff-416e-8cc0-17cee534bce7': 'ms-PKI-Certificate-Name-Flag',
    'd15ef7d8-f226-46db-ae79-b34e560bd12c': 'ms-PKI-Enrollment-Flag',
    'e5209ca2-3bba-11d2-90cc-00c04fd91ab1': 'PKI-Certificate-Template',
    '00000000-0000-0000-0000-000000000000': 'AllProperties'

}

# https://github.com/BloodHoundAD/SharpHoundCommon/blob/80fc5c0deaedf8d39d62c6f85d6fd58fd90a840f/src/CommonLib/Enums/CommonOids.cs#L8
# https://github.com/BloodHoundAD/SharpHoundCommon/blob/80fc5c0deaedf8d39d62c6f85d6fd58fd90a840f/src/CommonLib/Helpers.cs#L324
AUTHENTICATION_OIDS = {
    '1.3.6.1.5.5.7.3.2', # ClientAuthentication,
    '1.3.6.1.5.2.3.4', # PKINITClientAuthentication,
    '1.3.6.1.4.1.311.20.2.2', # SmartcardLogon,
    '2.5.29.37.0' # AnyPurpose
}


# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-adts/564dc969-6db3-49b3-891a-f2f8d0a68a7f
FUNCTIONAL_LEVELS = {
    0: "2000 Mixed/Native",
    1: "2003 Interim",
    2: "2003",
    3: "2008",
    4: "2008 R2",
    5: "2012",
    6: "2012 R2",
    7: "2016"
}

# used for automated lookups for field parsing based on LDAP entry field name
LOOKUPS = {
    #https://docs.microsoft.com/en-us/openspecs/windows_protocols/ms-adts/5026a939-44ba-47b2-99cf-386a9e674b04
    'trustDirection' : {0 : 'DISABLED', 1: 'INBOUND', 2: 'OUTBOUND', 3: 'BIDIRECTIONAL'},
    
    #https://docs.microsoft.com/en-us/openspecs/windows_protocols/ms-adts/36565693-b5e4-4f37-b0a8-c1b12138e18e
    'trustType' : {1 : 'DOWNLEVEL', 2: 'UPLEVEL', 3: 'MIT', 4: 'DCE', 5: 'AAD'}
}


# used for automated flag parsing for field parsing based on LDAP entry field name
FLAGS = {

    #https://docs.microsoft.com/en-us/troubleshoot/windows-server/identity/useraccountcontrol-manipulate-account-properties
    'userAccountControl' : 
    {
        'SCRIPT'            : 0x0001,
        'ACCOUNTDISABLE'    : 0x0002,
        'HOMEDIR_REQUIRED'  : 0x0008,
        'LOCKOUT'           : 0x0010,
        'PASSWD_NOTREQD'    : 0x0020,
        'PASSWD_CANT_CHANGE': 0x0040,
        'ENCRYPTED_TEXT_PWD_ALLOWED' : 	0x0080,
        'TEMP_DUPLICATE_ACCOUNT' : 	0x0100,
        'NORMAL_ACCOUNT' : 	0x0200,
        'INTERDOMAIN_TRUST_ACCOUNT' : 0x0800,
        'WORKSTATION_TRUST_ACCOUNT' : 0x1000,
        'SERVER_TRUST_ACCOUNT' : 0x2000,
        'DONT_EXPIRE_PASSWORD' : 0x10000,
        'MNS_LOGON_ACCOUNT' : 0x20000,
        'SMARTCARD_REQUIRED' : 0x40000,
        'TRUSTED_FOR_DELEGATION' : 0x80000,
        'NOT_DELEGATED'	: 0x100000,
        'USE_DES_KEY_ONLY' : 0x200000,
        'DONT_REQ_PREAUTH': 0x400000,
        'PASSWORD_EXPIRED' : 0x800000,
        'TRUSTED_TO_AUTH_FOR_DELEGATION' :0x1000000,
        'PARTIAL_SECRETS_ACCOUNT': 0x04000000,
    },

    #https://docs.microsoft.com/en-us/openspecs/windows_protocols/ms-adts/e9a2d23c-c31e-4a6f-88a0-6646fdb51a3c
    'trustAttributes' :
    {
        'NON_TRANSITIVE':0x00000001,
        'UPLEVEL_ONLY':0x00000002,
        'QUARANTINED_DOMAIN':0x00000004,
        'FOREST_TRANSITIVE':0x00000008,
        'CROSS_ORGANIZATION':0x00000010,
        'WITHIN_FOREST':0x00000020,
        'TREAT_AS_EXTERNAL':0x00000040,
        'USES_RC4_ENCRYPTION':0x00000080,
        'CROSS_ORGANIZATION_NO_TGT_DELEGATION':0x00000200,
        'CROSS_ORGANIZATION_ENABLE_TGT_DELEGATION': 0x00000800,
        'PIM_TRUST':0x00000400
    },

    # https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-crtd/1192823c-d839-4bc3-9b6b-fa8c53507ae1
    'msPKI-Certificate-Name-Flag': 
    {
        'CT_FLAG_ENROLLEE_SUPPLIES_SUBJECT': 0x00000001,
        'CT_FLAG_ENROLLEE_SUPPLIES_SUBJECT_ALT_NAME': 0x00010000,
        'CT_FLAG_SUBJECT_ALT_REQUIRE_DOMAIN_DNS': 0x00400000,
        'CT_FLAG_SUBJECT_ALT_REQUIRE_SPN': 0x00800000,
        'CT_FLAG_SUBJECT_ALT_REQUIRE_DIRECTORY_GUID': 0x01000000,
        'CT_FLAG_SUBJECT_ALT_REQUIRE_UPN': 0x02000000,
        'CT_FLAG_SUBJECT_ALT_REQUIRE_EMAIL': 0x04000000, 
        'CT_FLAG_SUBJECT_ALT_REQUIRE_DNS': 0x08000000, 
        'CT_FLAG_SUBJECT_REQUIRE_DNS_AS_CN': 0x10000000, 
        'CT_FLAG_SUBJECT_REQUIRE_EMAIL': 0x20000000, 
        'CT_FLAG_SUBJECT_REQUIRE_COMMON_NAME': 0x40000000, 
        'CT_FLAG_SUBJECT_REQUIRE_DIRECTORY_PATH': 0x80000000,
        'CT_FLAG_OLD_CERT_SUPPLIES_SUBJECT_AND_ALT_NAME': 0x00000008
    },

    # https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-crtd/ec71fd43-61c2-407b-83c9-b52272dec8a1
    'msPKI-Enrollment-Flag': {
        'CT_FLAG_INCLUDE_SYMMETRIC_ALGORITHMS': 0x00000001, 
        'CT_FLAG_PEND_ALL_REQUESTS': 0x00000002, 
        'CT_FLAG_PUBLISH_TO_KRA_CONTAINER': 0x00000004, 
        'CT_FLAG_PUBLISH_TO_DS': 0x00000008,
        'CT_FLAG_AUTO_ENROLLMENT_CHECK_USER_DS_CERTIFICATE': 0x00000010,
        'CT_FLAG_AUTO_ENROLLMENT': 0x00000020,
        'CT_FLAG_PREVIOUS_APPROVAL_VALIDATE_REENROLLMENT': 0x00000040,
        'CT_FLAG_USER_INTERACTION_REQUIRED': 0x00000100,
        'CT_FLAG_REMOVE_INVALID_CERTIFICATE_FROM_PERSONAL_STORE': 0x00000400,
        'CT_FLAG_ALLOW_ENROLL_ON_BEHALF_OF': 0x00000800,
        'CT_FLAG_ADD_OCSP_NOCHECK': 0x00001000,
        'CT_FLAG_ENABLE_KEY_REUSE_ON_NT_TOKEN_KEYSET_STORAGE_FULL': 0x00002000,
        'CT_FLAG_NOREVOCATIONINFOINISSUEDCERTS': 0x00004000,
        'CT_FLAG_INCLUDE_BASIC_CONSTRAINTS_FOR_EE_CERTS': 0x00008000,
        'CT_FLAG_ALLOW_PREVIOUS_APPROVAL_KEYBASEDRENEWAL_VALIDATE_REENROLLMENT': 0x00010000,
        'CT_FLAG_ISSUANCE_POLICIES_FROM_REQUEST': 0x00020000,
        'CT_FLAG_SKIP_AUTO_RENEWAL': 0x00040000,
        'CT_FLAG_NO_SECURITY_EXTENSION': 0x00080000
    }, 

    # https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-crtd/f6122d87-b999-4b92-bff8-f465e8949667
    # FEATURE: Extra processing can be done on the marked entries as per above
    'msPKI-Private-Key-Flag': {
        'CT_FLAG_REQUIRE_PRIVATE_KEY_ARCHIVAL': 0x00000001,
        'CT_FLAG_EXPORTABLE_KEY': 0x00000010,
        'CT_FLAG_STRONG_KEY_PROTECTION_REQUIRED': 0x00000020,
        'CT_FLAG_REQUIRE_ALTERNATE_SIGNATURE_ALGORITHM': 0x00000040,
        'CT_FLAG_REQUIRE_SAME_KEY_RENEWAL': 0x00000080,
        'CT_FLAG_USE_LEGACY_PROVIDER': 0x00000100,
        'CT_FLAG_ATTEST_NONE': 0x00000000, # * 
        'CT_FLAG_ATTEST_REQUIRED': 0x00002000, # *
        'CT_FLAG_ATTEST_PREFERRED': 0x00001000, # *
        'CT_FLAG_ATTESTATION_WITHOUT_POLICY': 0x00004000, # *
        'CT_FLAG_EK_TRUST_ON_USE': 0x00000200, # *
        'CT_FLAG_EK_VALIDATE_CERT': 0x00000400, # *
        'CT_FLAG_EK_VALIDATE_KEY': 0x00000800, # *
        'CT_FLAG_HELLO_LOGON_KEY': 0x00200000 # *
    },

}


MANUAL_FLAGS = {

    # https://github.com/BloodHoundAD/SharpHoundCommon/blob/1ccdb773d3af19718f410d9795ca9977019b5a85/src/CommonLib/Enums/CollectionMethods.cs
    'collectionMethods': 
    {
        'None': 0,
        'Group' : 1,
        'LocalAdmin': 1 << 1,
        'GPOLocalGroup': 1 << 2,
        'Session' : 1 << 3,
        'LoggedOn' : 1 << 4,
        'Trusts' : 1 << 5,
        'ACL' : 1 << 6,
        'Container' : 1 << 7,
        'RDP' : 1 << 8,
        'ObjectProps' : 1 << 9,
        'SessionLoop' : 1 << 10,
        'LoggedOnLoop' : 1 << 11,
        'DCOM' : 1 << 12,
        'SPNTargets' : 1 << 13,
        'PSRemote' : 1 << 14,
        'UserRights' : 1 << 15,
        'CARegistry' : 1 << 16,
        'DCRegistry' : 1 << 17,
        'CertServices' : 1 << 18,
        'LocalGroups' :  (1 << 12) | (1 << 8) | (1 << 1) | (1 << 14), # 20738 - DCOM | RDP | LocalAdmin | PSRemote,
        'ComputerOnly' : 20738 | (1 << 3) | (1 << 15) | (1 << 16) | (1 << 17), # 250122 - LocalGroups | Session | UserRights | CARegistry | DCRegistry,
        'DCOnly' : (1 << 6) | (1 << 7) | 1 | (1 << 9) | (1 << 5) | (1 << 2) | (1 << 18), # 262885 - ACL | Container | Group | ObjectProps | Trusts | GPOLocalGroup | CertServices,
        'Default' : 1 | (1 << 3) | (1 << 5) | (1 << 6) | (1 << 9) |  20738 | (1 << 13) | (1 << 7) | (1 << 18), # 291819 - Group | Session | Trusts | ACL | ObjectProps | LocalGroups | SPNTargets | Container | CertServices,
        'All' : 291819 | (1 << 4) | (1 << 2) | (1 << 15) | (1 << 16) | (1 << 17) #521215 - Default | LoggedOn | GPOLocalGroup | UserRights | CARegistry | DCRegistry
    },

    # https://github.com/BloodHoundAD/SharpHoundCommon/blob/80fc5c0deaedf8d39d62c6f85d6fd58fd90a840f/src/CommonLib/Enums/PKICertificateAuthorityFlags.cs
    'flags': {
        'NO_TEMPLATE_SUPPORT' : 0x00000001,
        'SUPPORTS_NT_AUTHENTICATION' : 0x00000002,
        'CA_SUPPORTS_MANUAL_AUTHENTICATION' : 0x00000004,
        'CA_SERVERTYPE_ADVANCED' : 0x00000008
    }
}


# TODO: lDAPAdminLimits set on query policy objects

# Limit the schema collection to the following
SCHEMA_ATTRIBUTES = [
    'adminDescription',
    'defaultSecurityDescriptor',
    'description',
    'name',
    'lDAPDisplayName',
    'mayContain',
    'mustContain',
    'objectClass',
    'schemaIDGUID',
    'systemMayContain',
    'systemMustContain'
]

# BH attributes

# attributes shared by all categories
SHARED_ATTRIBUTES = [
    'description',
    'distinguishedName',
    'isDeleted',
    'nTSecurityDescriptor',
    'name',
    'objectCategory',
    'objectClass',
    'whenCreated'
]

CERTAUTHORITIES_ATTRIBUTES =  sorted(SHARED_ATTRIBUTES + [
    'cACertificate',
    'crossCertificatePair',
    'msPKI-Certificate-Policy',
    'objectGUID'
])

CERTENROLLSERVICES_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'cACertificate',
    'certificateTemplates',
    'crossCertificatePair',
    'displayName',
    'dNSHostName',
    'flags',
    'objectGUID'
])

CERTTEMPLATES_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'displayName',
    'flags',
    'objectGUID',
    'msDS-OIDToGroupLink',
    'msPKI-Cert-Template-OID',
    'msPKI-Certificate-Application-Policy',
    'msPKI-Certificate-Name-Flag',
    'msPKI-Enrollment-Flag',
    'msPKI-RA-Application-Policies',
    'msPKI-RA-Policies',
    'msPKI-RA-Signature',
    'msPKI-Template-Schema-Version',
    'pKIExpirationPeriod',
    'pKIExtendedKeyUsage',
    'pKIOverlapPeriod'
])

COMPUTERS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'dNSHostName',
    'homeDirectory',
    'lastlogon',
    'lastlogontimestamp',
    'mail',
    'msDS-AllowedToActOnBehalfOfOtherIdentity',
    'msDS-AllowedToDelegateTo',
    'msDS-HostServiceAccount',
    'msDS-GroupMSAMembership',
    'ms-Mcs-AdmPwd',
    'ms-Mcs-AdmPwdExpirationTime',
    'msLAPS-EncryptedPassword',
    'msLAPS-EncryptedPasswordHistory',
    'msLAPS-EncryptedDSRMPassword',
    'msLAPS-EncryptedDSRMPasswordHistory',
    'msLAPS-CurrentPasswordVersion',
    'msLAPS-Password',
    'msLAPS-PasswordExpirationTime',
    'objectSid',
    'operatingSystem',
    'operatingSystemServicePack',
    'primaryGroupID',
    'pwdlastset',
    'sAMAccountName',
    'scriptpath',
    'sIDHistory',
    'servicePrincipalName',
    'userAccountControl'
])

CONTAINERS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'objectGUID'
])

DOMAINS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'gPLink',
    'objectSid',
    'msDS-Behavior-Version'
])

FORESTS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'msDS-Behavior-Version',
    'objectGUID'
])

GPOS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'displayName',
    'flags',
    'gPCFileSysPath',
    'objectGUID'
])

GROUPS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'member',
    'objectSid',
    'sAMAccountName',
    'sIDHistory'
])

OUS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'gPLink',
    'gPOptions',
    'objectGUID',
    'whenCreated'
])

TRUSTED_DOMAINS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'objectGUID',
    'securityIdentifier',
    'trustAttributes',
    'trustDirection',
    'trustPartner',
    'trustType'
])

USERS_ATTRIBUTES = sorted(SHARED_ATTRIBUTES + [
    'homeDirectory',
    'lastlogon',
    'lastlogontimestamp',
    'mail',
    'msSFU30Password',
    'msDS-AllowedToActOnBehalfOfOtherIdentity',
    'msDS-AllowedToDelegateTo',
    'msDS-GroupMSAMembership',
    'msDS-HostServiceAccount',
    'objectSid',
    'primaryGroupID',
    'pwdlastset',
    'sAMAccountName',
    'scriptPath',
    'sIDHistory',
    'servicePrincipalName',
    'unixUserPassword',
    'unicodePwd',
    'userPassword',
    'userAccountControl'
])

# BloodHound common properties that are generated directly from ldap properties

BH_COMMON_PROPERTIES = [
    'description',
    'distinguishedname',
    'domain',
    'domainsid',
    'name',
    'whencreated'
]



class AdDumper:

    def __init__(self, host=None, target_ip=None, username=None, password=None, ssl=False, sslprotocol=None, port=None, delay=0, jitter=0, paged_size=500, logger=Logger('AdDumper'), raw=False, kerberos=False, 
                 no_password=False, query_config=None, import_mode=False, attributes=ldap3.ALL_ATTRIBUTES, bh_attributes=False, start_tls=False, client_cert_file=None, client_key_file=None):
        self.logger = logger
        self.host = host
        self.kerberos = kerberos
        self.target_ip = target_ip if target_ip else host
        self.username = username 
        if kerberos:
            self.logger.debug('Kerberos option selected, will attempt to authenticate using configured Kerberos ccache')
            try:
                import gssapi
                cred = gssapi.Credentials(usage='initiate')
                self.username = cred.name.__bytes__().decode()
                self.logger.debug('Username from Kerberos: {}'.format(self.username))
            except Exception as e:
                self.logger.debug(f'Failed to determine username from Kerberos credential store using gssapi. Ensure gssapi is installed for Kerberos use. Exception:\n{e}')
                self.username = 'Kerberos {}'.format(os.environ["KRB5CCNAME"])
        elif (client_cert_file and client_key_file):
            self.logger.debug(f'Attempting to use client certificate file "{client_cert_file}" and client key file "{client_key_file}" for authentication')
        elif not username:
            self.authentication = None
            if not import_mode:
                self.logger.debug('No username provided, will attempt to perform anonymous bind. Will likely result in limited output.')
        elif '\\' in username:
            self.logger.debug('Username provided in NTLM format, will attempt NTLM authentication')
            self.authentication = 'NTLM'
        else:
            self.logger.debug('Using SIMPLE authentication')
            self.authentication = 'SIMPLE'
        self.password = password 
        self.no_password = no_password
        if self.no_password and self.username:
            if self.authentication == 'NTLM': # password to empty NTLM string, ldap3 wont allow you to specify no password but will allow emtpy password hash
                self.password = 'AAD3B435B51404EEAAD3B435B51404EE:31D6CFE0D16AE931B73C59D7E0C089C0' 
                self.logger.debug('No password setting specified, attempting NTLM authentication using empty password')
            else: # empty password wont work for SIMPLE binds
                raise Exception('Passwordless authentication not supported using SIMPLE bind, specify your login name as DOMAIN\\username to use NTLM authentication')
        self.ssl = ssl 
        self.raw = raw
        self.port = port if port else 636 if self.ssl else 389
        self.delay = delay
        self.jitter = jitter
        self.config = query_config
        if sslprotocol:
            spv = self.get_supported_tls()
            if sslprotocol in spv:
                self.sslprotocol = spv[sslprotocol]
            else:
                raise Exception('Bad SSL Protocol value provided, choose one from: {}'.format(', '.join(list(spv))))
        else:
            self.sslprotocol = None
        
        self.start_tls = start_tls
        self.client_cert_file = client_cert_file
        self.client_key_file = client_key_file
        
        self.bh_parent_map = {}
        self.bh_gpo_map = {}
        self.bh_cert_temp_map = {}
        self.bh_member_map = {}
        self.bh_computer_map = {}
        self.bh_core_domain = ''
        self.post_process_data = True
        self.multi_field = ['dSCorePropagationData', 'objectClass']
        self.datetime_format = '%Y-%m-%d %H:%M:%S.%f %Z %z'
        self.timestamp = False
        self.paged_size = paged_size
        # "Security descriptor flags" control 1.2.840.113556.1.4.801
        # LDAP_SERVER_SD_FLAGS_OID - 0x07 flag value, queries for all values in nTSecurityDescriptor apart from SACL
        self.controls = [('1.2.840.113556.1.4.801', True, "\x30\x03\x02\x01\x07")]  # SACL is 0x8, owner 0x1, group 0x2, DACL 0x4
        
        self.domainLT = {}
        self.domainLTNB = {}
        self.convert_binary = True

        # impacket LDAP access mask structures have values for set (not read) operations for these masks, so we override
        # https://learn.microsoft.com/en-us/dotnet/api/system.directoryservices.activedirectoryrights?view=netframework-4.7.2
        self.am_overrides = {
            'GENERIC_READ' : 0x00020094,
            'GENERIC_WRITE': 0x00020028,
            'GENERIC_EXECUTE':0x00020004,
            'GENERIC_ALL': 0x000F01FF
        }

        self.ace_flags = self.get_ace_flag_constants()
        self.access_masks = self.get_access_mask_constants()
        self.ace_data_flags = self.get_ace_data_flag_constants()
        self.object_types = dict(OBJECT_TYPES)
        self.schema = {}
        self.output_timestamp = None
        self.start_time = None 

        self.methods = []
        self.config_containers_collected = False
        self.attributes = attributes
        self.bh_attributes = bh_attributes

        # start with well known SIDS https://learn.microsoft.com/en-us/windows/win32/secauthz/well-known-sids
        self.sidLT = {
            'S-1-0': ['Null Authority', 'User'],
            'S-1-0-0': ['Nobody', 'User'],
            'S-1-1': ['World Authority', 'User'],
            'S-1-1-0': ['Everyone', 'Group'],
            'S-1-2': ['Local Authority', 'User'],
            'S-1-2-0': ['Local', 'Group'],
            'S-1-2-1': ['Console Logon', 'Group'],
            'S-1-3': ['Creator Authority', 'User'],
            'S-1-3-0': ['Creator Owner', 'User'],
            'S-1-3-1': ['Creator Group', 'Group'],
            'S-1-3-2': ['Creator Owner Server', 'Computer'],
            'S-1-3-3': ['Creator Group Server', 'Computer'],
            'S-1-3-4': ['Owner Rights', 'Group'],
            'S-1-4': ['Non-unique Authority', 'User'],
            'S-1-5': ['NT Authority', 'User'],
            'S-1-5-1': ['Dialup', 'Group'],
            'S-1-5-2': ['Network', 'Group'],
            'S-1-5-3': ['Batch', 'Group'],
            'S-1-5-4': ['Interactive', 'Group'],
            'S-1-5-6': ['Service', 'Group'],
            'S-1-5-7': ['Anonymous', 'Group'],
            'S-1-5-8': ['Proxy', 'Group'],
            'S-1-5-9': ['Enterprise Domain Controllers', 'Group'],
            'S-1-5-10': ['Principal Self', 'User'],
            'S-1-5-11': ['Authenticated Users', 'Group'],
            'S-1-5-12': ['Restricted Code', 'Group'],
            'S-1-5-13': ['Terminal Server Users', 'Group'],
            'S-1-5-14': ['Remote Interactive Logon', 'Group'],
            'S-1-5-15': ['This Organization', 'Group'],
            'S-1-5-17': ['IUSR', 'User'],
            'S-1-5-18': ['Local System', 'User'],
            'S-1-5-19': ['NT Authority', 'User'],
            'S-1-5-20': ['Network Service', 'User'],
            'S-1-5-80-0': ['All Services ', 'Group'],
            'S-1-5-32-544': ['Administrators', 'Group'],
            'S-1-5-32-545': ['Users', 'Group'],
            'S-1-5-32-546': ['Guests', 'Group'],
            'S-1-5-32-547': ['Power Users', 'Group'],
            'S-1-5-32-548': ['Account Operators', 'Group'],
            'S-1-5-32-549': ['Server Operators', 'Group'],
            'S-1-5-32-550': ['Print Operators', 'Group'],
            'S-1-5-32-551': ['Backup Operators', 'Group'],
            'S-1-5-32-552': ['Replicators', 'Group'],
            'S-1-5-32-554': ['Pre-Windows 2000 Compatible Access', 'Group'],
            'S-1-5-32-555': ['Remote Desktop Users', 'Group'],
            'S-1-5-32-556': ['Network ConfiguratiManagedServiceAccountn Operators', 'Group'],
            'S-1-5-32-557': ['Incoming Forest Trust Builders', 'Group'],
            'S-1-5-32-558': ['Performance Monitor Users', 'Group'],
            'S-1-5-32-559': ['Performance Log Users', 'Group'],
            'S-1-5-32-560': ['Windows Authorization Access Group', 'Group'],
            'S-1-5-32-561': ['Terminal Server License Servers', 'Group'],
            'S-1-5-32-562': ['Distributed COM Users', 'Group'],
            'S-1-5-32-568': ['IIS_IUSRS', 'Group'],
            'S-1-5-32-569': ['Cryptographic Operators', 'Group'],
            'S-1-5-32-573': ['Event Log Readers', 'Group'],
            'S-1-5-32-574': ['Certificate Service DCOM Access', 'Group'],
            'S-1-5-32-575': ['RDS Remote Access Servers', 'Group'],
            'S-1-5-32-576': ['RDS Endpoint Servers', 'Group'],
            'S-1-5-32-577': ['RDS Management Servers', 'Group'],
            'S-1-5-32-578': ['Hyper-V Administrators', 'Group'],
            'S-1-5-32-579': ['Access Control Assistance Operators', 'Group'],
            'S-1-5-32-580': ['Remote Management Users', 'Group'],
            'S-1-5-32-581': ['Default Account', 'Group'],
            'S-1-5-32-582': ['Storage Replica Administrators', 'Group'],
            'S-1-5-32-583': ['Device Owners', 'Group']
        }


    
    def get_ace_flag_constants(self):
        return {a:ACE.__dict__[a] for a in ACE.__dict__ if a == a.upper()} 


    def get_access_mask_constants(self):
        access_mask = {a:ACCESS_MASK.__dict__[a] for a in ACCESS_MASK.__dict__ if a == a.upper() }
        access_mask.update({a:ACCESS_ALLOWED_OBJECT_ACE.__dict__[a] for a in ACCESS_ALLOWED_OBJECT_ACE.__dict__ if a.startswith('ADS_')})   
        access_mask.update(self.am_overrides)
        return access_mask


    def get_ace_data_flag_constants(self):
        return {a:ACCESS_ALLOWED_OBJECT_ACE.__dict__[a] for a in ACCESS_ALLOWED_OBJECT_ACE.__dict__ if 'PRESENT' in a}
        

    def get_supported_tls(self):
        try:
            return {a.replace('PROTOCOL_', ''): getattr(ssl, a).value for a in dir(ssl) if a.startswith('PROTOCOL_') and 'v' in a}
        except:
            return {'SSLv23': 2, 'TLSv1': 3, 'TLSv1_1': 4, 'TLSv1_2': 5}

    def connect(self):
        if not self.target_ip:
            raise Exception('No host provided')
        
        if self.client_key_file and self.client_cert_file:
            tls_object = Tls(validate=0, version=self.sslprotocol, local_private_key_file=self.client_key_file, local_certificate_file=self.client_cert_file)
        else:
            tls_object = Tls(validate=0, version=self.sslprotocol)

        if self.ssl:
            self.server = Server(self.target_ip, get_info=ALL, port=self.port, use_ssl=True, tls=tls_object)
        else:
            if self.start_tls or (self.client_key_file and self.client_cert_file):
                self.server = Server(self.target_ip, get_info=ALL, port=self.port, tls=tls_object)
            else:
                self.server = Server(self.target_ip, get_info=ALL, port=self.port)
        
        # host needs to be a domain name for kerberos
        # we ensure this is the case even if we connect to an IP via the sasl_credentials with the host specified as var 1 in Connection
        if self.kerberos:
            self.logger.debug(f'Attempting to perform Kerberos connection to LDAP server {self.server} with bind host name {self.host}')
            self.connection = Connection(self.server, sasl_credentials=(self.host,), authentication=SASL, sasl_mechanism=KERBEROS) 
        elif self.client_key_file and self.client_cert_file and self.ssl:
            self.logger.debug(f'Attempting to authenticate to LDAP server {self.server} using provided certificate with SSL bind')
            self.connection = Connection(self.server) 
        elif (self.client_key_file and self.client_cert_file):
            self.logger.debug(f'Attempting to perform connection to LDAP server {self.server} with STARTTLS')
            self.connection = Connection(self.server, authentication=SASL, sasl_mechanism=EXTERNAL, auto_bind=AUTO_BIND_TLS_BEFORE_BIND)
        else:
            self.logger.debug(f'Attempting to perform connection to LDAP server {self.server}')
            self.connection = Connection(self.server, user=self.username, password=self.password, authentication=self.authentication)

        if self.start_tls and not (self.client_key_file and self.client_cert_file):
            self.logger.debug(f'Attempting to START_TLS on connection...')
            try:
                self.connection.start_tls()
            except Exception as e:
                self.logger.debug(f'Exception during START_TLS operation: {str(e)}')
                sys.exit(1)

        # need to open and not rebind when relying on TLS connection for authentication
        if (self.client_key_file and self.client_cert_file) and self.ssl:
            self.connection.open() 
        # the connection auto binds when using certificate auth on the non SSL LDAP port
        elif not (self.client_key_file and self.client_cert_file):
            try:
                bindresult = self.connection.bind()
            except Exception as e:
                print('An error occurred when binding to the LDAP service:\n{}\n'.format(e))
                print('For Kerberos errors try manually specifying the realm, ensuring that forged ccache tickets use upper case for the domain and removing conflicting hosts file entries.')
                sys.exit(1)

            if not bindresult:
                raise Exception('An error occurred when attempting to bind to the LDAP server: {}'.format(', '.join(['{} : {}' .format(a, self.connection.result[a]) for a in  self.connection.result])))
        
        # Check to see if server is a Global Catalog server
        if not 'TRUE' in self.server.info.other.get('isGlobalCatalogReady'):
            self.logger.warning('WARNING: Server is not a global catalog, results may be incomplete...')
        else:
            self.logger.info('Target server is a Global Catalog server')
        self.root = self.server.info.other['defaultNamingContext'][0]
        self.logger.info('Authenticated as user: {}'.format(self.whoami()))
    

    def generate_timestamp(self):
        if not self.output_timestamp:
            self.output_timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        return self.output_timestamp

    #https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-dtyp/7d4dac05-9cef-4563-a058-f108abecce1d?redirectedfrom=MSDN
    #https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-adts/990fb975-ab31-4bc1-8b75-5da132cd4584
    def parseSecurityDescriptor(self, nTSecurityDescriptor):
        out = {}
        sd = SR_SECURITY_DESCRIPTOR()
        sd.fromString(nTSecurityDescriptor)
        out['IsACLProtected'] = int(bin(sd['Control'])[2:][3]) == 1 # 3 PD DACL Protected from inherit operations
        # Get-ADUser -Filter * -Properties nTSecurityDescriptor | ?{ $_.nTSecurityDescriptor.AreAccessRulesProtected -eq "True" }
        # sd['Sacl'] is masked in the LDAP query because of permissions, so wont be available here
        if sd['Control']:
            out['Control'] = sd['Control']
        if sd['OwnerSid']:
            out['OwnerSid'] = sd['OwnerSid'].formatCanonical()
            if out['OwnerSid'] in self.sidLT:
                out['OwnerName'] = self.sidLT[out['OwnerSid']][0]
        if sd['GroupSid']:
            out['GroupSid'] = sd['GroupSid'].formatCanonical()
            if out['GroupSid'] in self.sidLT:
                out['GroupName'] = self.sidLT[out['GroupSid']][0]
        if sd['Dacl']:
            out['Dacls'] = []
            for ace in sd['Dacl']['Data']:
                dacl = {'Type' : ace['TypeName']}
                dacl['Sid'] = ace['Ace']['Sid'].formatCanonical()
                if dacl['Sid'] in self.sidLT:
                    d = [self.sidLT[dacl['Sid']][0]]
                    domainsid = self.get_domain_sid(dacl['Sid'])
                    if domainsid in self.domainLTNB:
                        d.append(self.domainLTNB[domainsid])
                    elif dacl['Sid'].startswith('S-1-5-32-'):
                        d.append('Builtin')
                    dacl['ResolvedSidName'] = '\\'.join(d[::-1])
                    dacl['Foreign'] = False
                #elif dacl['Sid'].count('-') > 6: # this is wrong...
                #    dacl['Foreign'] = True

                dacl['Flags'] = []
                for flag in self.ace_flags:
                    if ace.hasFlag(self.ace_flags[flag]):
                        dacl['Flags'].append(flag)
                if dacl['Type'] == 'ACCESS_ALLOWED_OBJECT_ACE':
                    dacl['Ace_Data_Flags'] = []
                    for dataflag in self.ace_data_flags:
                        if ace['Ace'].hasFlag(self.ace_data_flags[dataflag]):
                            dacl['Ace_Data_Flags'].append(dataflag)

                dacl['Mask'] = ace['Ace']['Mask']['Mask']

                dacl['Privs'] = []
                for priv in self.access_masks:
                    if ace['Ace']['Mask'].hasPriv(self.access_masks[priv]):
                        dacl['Privs'].append(priv)
                if 'ObjectType' in ace['Ace'].fields and len(ace['Ace']['ObjectType']) > 0:
                    type_guid = bin_to_string(ace['Ace']['ObjectType']).lower()
                    if type_guid in self.object_types:
                        dacl['ControlObjectType'] = self.object_types[type_guid]
                    else:
                        dacl['ControlObjectType'] = type_guid
                if 'InheritedObjectType' in ace['Ace'].fields and len(ace['Ace']['InheritedObjectType']) > 0:
                    type_guid = bin_to_string(ace['Ace']['InheritedObjectType']).lower()
                    if type_guid in self.object_types:
                        dacl['InheritableObjectType'] = self.object_types[type_guid]
                    else:
                        dacl['InheritableObjectType'] = type_guid
                out['Dacls'].append(dacl)

        return out

    def _parse_convert_val(self, value):
        if isinstance(value, datetime):
            if self.timestamp:
                return value.timestamp()
            else:
                return value.strftime(self.datetime_format)
        elif isinstance(value, timedelta):
            return str(value)
        elif isinstance(value, list):
            return [self._parse_convert_val(a) for a in value]
        else:
            return value



    def parse_records(self, gen):
        out = []
        counter=0
        for record in gen:
            if 'type' in record and record['type'] == 'searchResEntry' and 'attributes' in record:
                orecord = record['attributes']
                for key in orecord:
                    orecord[key] = self._parse_convert_val(orecord[key])

                for entry in FLAGS:
                    if entry in orecord:
                        # msPKI-Private-Key-Flag
                        orecord['{}Flags'.format(entry)] = [a for a in FLAGS[entry] if self.hasFlag(FLAGS[entry][a], orecord[entry])]

                for entry in LOOKUPS:
                    if entry in orecord:
                        orecord['{}Resolved'.format(entry)] = LOOKUPS[entry][orecord[entry]]

                for entry in ['pKIExpirationPeriod', 'pKIOverlapPeriod']:
                    if entry in orecord:
                        if self.raw:
                            orecord['{}_raw'.format(entry)] = orecord[entry]
                        orecord[entry] = self._convert_pki_period(orecord[entry])


                out.append(orecord)

                # delay between each page of records if sleep is configured
                if self.delay:
                    counter+=1
                    if counter==self.paged_size:
                        mydelay = self.delay
                        if self.jitter:
                            myjit = random.randint(1, self.jitter)
                            mydelay = self.delay + myjit
                            self.logger.debug('Adding {} seconds of jitter to delay'.format(myjit))
                        self.logger.info('Sleeping for {} seconds during paging operation as per configured setting'.format(mydelay))
                        time.sleep(mydelay)
                        counter=0

        return out

    def get_class(self, entry):
        return entry['objectCategory'].split(',')[0].split('=')[-1].replace('Person', 'User').replace('-DNS', '')

    def update_sidlt(self, data):
        self.sidLT.update({a['objectSid']: [a['sAMAccountName'], self.get_class(a)] for a in data if 'objectSid' in a and 'sAMAccountName' in a and 'objectCategory' in a})

    def get_domain_sid(self, sid):
        return '-'.join(sid.split('-')[:-1])


    def jsonify(self, data, delistify=False):
        if isinstance(data, list) or isinstance(data, tuple):
            if delistify:
                if len(data) == 1:
                    return self.jsonify(data[0])
            return [self.jsonify(a) for a in data]
        elif isinstance(data, dict):
            for key in data:
                data[key] = self.jsonify(data[key])
        elif isinstance(data, bytes):
            try:
                data = data.decode('utf-8')
                return data 
            except:
                pass
            return hexlify(data).decode('utf-8')
        elif isinstance(data, datetime):
            return str(data)
        elif isinstance(data, str):
            if data.isdigit():
                return int(data)
            if data.lower() == 'true':
                return bool('1')
            elif data.lower() == 'false':
                return bool('')
        elif isinstance(data, CaseInsensitiveDict):
            return self.jsonify(dict(data))
        return data


    def custom_query(self, query: str, attributes: str=ldap3.ALL_ATTRIBUTES, parse_records: bool=True, controls: bool=None) -> list:
        self.logger.info('Running custom query against LDAP')
        self.logger.debug('Query: {}'.format(query))
        if isinstance(controls, type(None)):
            controls=self.controls
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=controls, attributes=attributes, paged_size=self.paged_size, generator=parse_records)
        if parse_records:
            data = self.parse_records(gen)
            return data
        return gen


    def _query_certcontainers(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        data = []
        if not self.config_containers_collected:
            if 'containers' in self.methods:
                self.logger.info('Querying configuration container objects from LDAP')
                query = '(|(objectClass=container)(objectClass=configuration))'
                method_name = 'containers'
                query, attributes = self._configure_query(method_name, query, attributes)
                gen = self.connection.extend.standard.paged_search(self.server.info.other['configurationNamingContext'][0], query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
                data = self.parse_records(gen)
                self.config_containers_collected = True
        return data



    def query_certauthorities(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying certauthority objects from LDAP')
        query = '(objectClass=certificationAuthority)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        # forcing base to CN=Configuration is the only way Ive been able to get PKI related items to work, not sure if theres a betetr way
        gen = self.connection.extend.standard.paged_search(self.server.info.other['configurationNamingContext'][0], query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        return data


    def query_certenrollservices(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying certenrollservice objects from LDAP')
        query = '(objectClass=pKIEnrollmentService)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.server.info.other['configurationNamingContext'][0], query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        # post process flag field value - "flag" field is too generic to do this in shared routine so do it here
        for record in data:
            if 'flags' in record:
                record['flags_raw'] = record['flags']
                record['flags'] = [a for a in MANUAL_FLAGS['flags'] if self.hasFlag(MANUAL_FLAGS['flags'][a], record['flags'])]
        return data


    def query_certtemplates(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying certtemplate objects from LDAP')
        query = '(objectClass=pKICertificateTemplate)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.server.info.other['configurationNamingContext'][0], query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        return data

    def query_containers(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying container objects from LDAP')
        query = '(objectClass=container)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        return data

    def query_computers(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying computer objects from LDAP')
        query = '(objectCategory=computer)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        self.update_sidlt(data)
        return data

    def query_domains(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        # FEATURE add a derived domain functional param from msDS-Behavior-Version ?
        self.logger.info('Querying domain objects from LDAP')
        query = '(objectClass=domain)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        self.domainLT = {a['objectSid']: '.'.join([b.split('=')[1].upper() for b in a['distinguishedName'].split(',')]) for a in data}
        self.domainLTNB = {a['objectSid']: a['name'].upper() for a in data}
        return data

    def query_forests(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        # FEATURE add a derived forest functional param from msDS-Behavior-Version ?
        # configurationNamingContext should be under cn=partitions,cn=configuration,dc=domain,dc=local
        self.logger.info('Querying forest objects from LDAP')
        query = '(objectClass=crossRefContainer)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.server.info.other['configurationNamingContext'][0], query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        return data

    def query_gpos(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying GPO objects from LDAP')
        query = '(objectClass=groupPolicyContainer)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True) # domainPolicy
        return self.parse_records(gen)


    # query for security groups only (|(sAMAccountType=268435456)(sAMAccountType=536870912)) 
    def query_groups(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying group objects from LDAP')
        query = '(objectClass=group)' # if not self.alt_query else '(objectCategory=group)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)     
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        self.update_sidlt(data)
        return data

    def query_ous(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying OU objects from LDAP')
        query = '(objectClass=organizationalUnit)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        return self.parse_records(gen)

    def query_trusted_domains(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying trusted domain objects from LDAP')
        query = '(objectClass=trustedDomain)' # if not self.alt_query else '(objectCategory=trustedDomain)'
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        for index in range(0, len(data)):            
            if 'trustAttributesFlags' in data[index]:
                fp = lambda x : x in data[index]['trustAttributesFlags']
                #data[index]['sidFiltering'] = True if not fp('WITHIN_FOREST') else fp('QUARANTINED_DOMAIN')
                data[index]['sidFiltering'] = bool(fp('QUARANTINED_DOMAIN'))
                data[index]['transitive'] = True if not (fp('TREAT_AS_EXTERNAL') or fp('CROSS_ORGANIZATION')) else False

        return data

    def query_users(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> list:
        self.logger.info('Querying user objects from LDAP')
        query = '(&(objectClass=user)(|(objectCategory=person)(objectCategory=msDS-GroupManagedServiceAccount)(objectCategory=msDS-ManagedServiceAccount)))' 
        method_name = sys._getframe(0).f_code.co_name.split('_', 1)[1]
        query, attributes = self._configure_query(method_name, query, attributes)
        gen = self.connection.extend.standard.paged_search(self.root, query, controls=self.controls, attributes=attributes, paged_size=self.paged_size, generator=True)
        data = self.parse_records(gen)
        self.update_sidlt(data)
        return data
        
    def query_info(self, attributes: str=ldap3.ALL_ATTRIBUTES) -> dict:
        '''This one runs on anonymous binds'''
        self.logger.info('Querying server information from LDAP')
        info = self.server.info.__dict__
        del(info['raw'])
        info['other'] = dict(info['other'])
        return info

    def whoami(self) -> str:
        try:
            who = (lambda x: x if x else 'Anonymous')(self.connection.extend.standard.who_am_i())
            return who.replace('u:', '', 1) if who.startswith('u:') else who
        except Exception as e:
            return f'Exception determining connected user: {str(e)}'


    def _configure_query(self, method_name, query, attributes):
        if self.bh_attributes:
            attributes = globals().get('{}_ATTRIBUTES'.format(method_name.upper()))
        if self.config and method_name in self.config:
            if 'query' in self.config[method_name]:
                query = self.config[method_name]['query']
                self.logger.debug('Query override for method "{}" from config file: {}'.format(method_name, query))
            if 'attributes' in self.config[method_name]:
                attributes = self.config[method_name]['attributes']
                self.logger.debug('Attributes override for method "{}" from config file: {}'.format(method_name, ','.join(attributes)))
                attributes += [a for a in MINIMUM_ATTRIBUTES if a.lower() not in [b.lower() for b in attributes]]
        if self.schema and not isinstance(attributes, str):
            present_attributes = [b for b in attributes if b.lower() in [a['lDAPDisplayName'].lower() for a in self.schema]]
            if len(attributes) != (present_attributes):
                removed_attributes = [a for a in attributes if a.lower() not in [b.lower() for b in present_attributes]]
                self.logger.debug('Removing the following attributes from {} query that were not present in schema: {}'.format(method_name, ', '.join(removed_attributes)))
            attributes = present_attributes
        return query, attributes


    #classSchema is object type of defined objects, fields mayContain mustContain systemMayContain systemMustContain have the associated attributes
    # subClassOf in classSchema defines class inheritance, which is from class type top
    def retrieve_schema(self):
        self.logger.info('Querying schema from LDAP')
        gen = self.connection.extend.standard.paged_search(self.server.info.other['schemaNamingContext'][0], '(|(objectClass=classSchema)(objectClass=attributeSchema))', attributes=SCHEMA_ATTRIBUTES, paged_size=self.paged_size, generator=True)
        parsed = [a['attributes'] for a in gen if 'attributes' in a]
        for entry in parsed:
            if 'schemaIDGUID' in entry:
                entry['schemaIDGUID'] = bin_to_string(entry['schemaIDGUID']).lower()
            entry = self.jsonify(entry)
        additional = {a['schemaIDGUID']: a['name'] for a in parsed if 'schemaIDGUID' in a and a['schemaIDGUID']}
        if additional:
            self.object_types.update(additional)
        self.schema = parsed

    def hasFlag(self, flag, value):
        return True if flag & value == flag else False

    def get_valid_methods(self):
        return [a.split('_', 1)[1] for a in self.__dir__() if a.startswith('query_')]


    def query(self, methods=None, only_schema=False, no_schema=False):
        self.start_time = self.generate_timestamp()
        out = {}
        if not no_schema:
            self.retrieve_schema()
            out['schema'] = self.schema
        
        if not only_schema:
            valid_methods = self.get_valid_methods()

            if not methods:
                methods = valid_methods
            else:
                for method in methods:
                    if method not in valid_methods:
                        raise Exception('Invalid query method of {} supplied.\nValid methods are: '.format(method, ', '.join(valid_methods)))

            self.methods = methods
            for method in methods:
                if self.delay:
                    mydelay = self.delay
                    if self.jitter:
                        myjit = random.randint(1, self.jitter)
                        mydelay = self.delay + myjit
                        self.logger.debug('Adding {} seconds of jitter to delay'.format(myjit))
                    self.logger.info('Sleeping for {} seconds between queries as per configured setting'.format(mydelay))
                    time.sleep(mydelay)
                method_call = getattr(self, 'query_{}'.format(method))
                method_return = typing.get_type_hints(method_call).get('return')
                if method_return == list:
                    if not method in out:
                        out[method] = []
                    out[method] += method_call(attributes=self.attributes)
                elif method_return == dict:
                    if not method in out:
                        out[method] = {}
                    out[method].update(method_call(attributes=self.attributes))
                else:
                    out[method] = method_call(attributes=self.attributes)
                if method.startswith('cert') and len(out[method]) > 0:
                    if not 'containers' in out:
                        out['containers'] = []
                    out['containers'] += self._query_certcontainers()

        out['meta'] = {'start_time': self.start_time, 'end_time' : self.generate_timestamp(), 'username': self.username, 'whoami': self.whoami(), 'server': self.host, 'methods' : list([a for a in out.keys() if a != 'schema']), 'sid_lookup' : self.sidLT}
        self.logger.info('Data collection complete, processing...')

        if self.post_process_data:
            return self.jsonify(self.post_process(out))
        else:
            return self.jsonify(out)


    def run_custom_query(self, query, attributes=ldap3.ALL_ATTRIBUTES, parse_records=True, controls=None):
        self.start_time = self.generate_timestamp()
        data = self.custom_query(query, attributes, parse_records, controls)
        meta = {'custom_query': query, 'start_time': self.start_time,'end_time' : self.generate_timestamp(), 'username': self.username, 'whoami': self.whoami(), 'server': self.host}
        out = self.post_process({'meta': meta, 'custom_query_results' : data}, auto_query_domains=False)
        return self.jsonify(out)


    def post_process(self, data, auto_query_domains=True):
        # run this to populate domain lookup table if not already run
        if not 'domains' in data and auto_query_domains:
            self.logger.info('Domain data not collected and "auto_query_domains" enabled - collecting domain info...')
            self.query_domains()

        for key in [a for a in data.keys() if a not in ['info', 'schema', 'meta']]:
            for index in range(0, len(data[key])):
                for sd in ['nTSecurityDescriptor', 'msDS-GroupMSAMembership', 'msDS-AllowedToActOnBehalfOfOtherIdentity']:
                    if sd in data[key][index]:
                        if data[key][index][sd] and isinstance(data[key][index][sd], bytes):
                            if self.raw:
                                data[key][index]['{}_raw'.format(sd)] = data[key][index][sd]
                            parsed = {}
                            try: 
                                parsed = self.parseSecurityDescriptor(data[key][index][sd])
                            except Exception as e:
                                self.logger.debug('Error in parsing security descriptor data in field {}: {}'.format(sd, str(e)))
                            data[key][index][sd] = parsed
                        else:
                            # delete empty entries added by explicitly requesting attribute
                            del data[key][index][sd]

                if 'domains' not in key:
                    if 'objectSid' in data[key][index] and data[key][index]['objectSid']:
                        domainsid = self.get_domain_sid(data[key][index]['objectSid'])
                        if domainsid in self.domainLT:
                            data[key][index]['domain'] = self.domainLT[domainsid]
                        if domainsid in self.domainLTNB:
                            data[key][index]['domainShort'] = self.domainLTNB[domainsid]
                for field in ['securityIdentifier', 'sIDHistory']:
                    if field in data[key][index]:
                        try:
                            if isinstance(data[key][index][field], bytes):
                                data[key][index][field] = LDAP_SID(data[key][index][field]).formatCanonical()
                            elif isinstance(data[key][index][field], list): 
                                items = []
                                for sid in data[key][index][field]:
                                    items += [LDAP_SID(sid).formatCanonical()]                                
                                data[key][index][field] = items
                        except Exception as e:
                            self.logger.debug('Post processing of field {} in key {} failed with error {}' .format(field, key, e))
                            pass
        return data
    

    # convert PKI period format, based on bh code from below
    # https://github.com/BloodHoundAD/SharpHoundCommon/blob/80fc5c0deaedf8d39d62c6f85d6fd58fd90a840f/src/CommonLib/Processors/LDAPPropertyProcessor.cs#L665
    def _convert_pki_period(self, value):
        up = struct.unpack('<q', value)[0] * -.0000001
        if (up % 31536000 == 0 and up / 31536000 >=1): # years 
            if up == 31536000:
                return '1 year'
            return '{} years'.format(int(up / 31536000))
        if (up % 2592000 == 0 and up / 2592000 >=1): # months 
            if up == 2592000:
                return '1 month'
            return '{} months'.format(int(up / 2592000))
        if (up % 604800 == 0 and up / 604800 >=1): # weeks
            if up == 604800:
                return '1 week'
            return '{} weeks'.format(int(up / 604800))
        if (up % 86400 == 0 and up / 86400 >=1): # day
            if up == 86400:
                return '1 day'
            return '{} days'.format(timedelta(seconds=up).days)
        if (up % 3600 == 0 and up / 3600 >=1): # hours
            if up == 3600:
                return '1 hour'
            return '{} hours'.format(int(up / 3600))
        
        return ''




    def _fp(self, obj, name, default=None):
        '''Internal case insensitive property fetcher'''
        matching_keys = [a for a in obj.keys() if a.lower() == name.lower()]
        if matching_keys:
            if name.lower().startswith('when') or name.lower().startswith('last') or name.lower() in ['pwdlastset']:
                if obj[matching_keys[0]]:
                    return self._dtt(obj[matching_keys[0]])
            else:
                if obj[matching_keys[0]]:
                    return obj[matching_keys[0]]
            return default
        else:
            return default


    def _dtt(self, time):
        '''Internal function to convert time strings to timestamps'''
        return int(datetime.timestamp(datetime.strptime(time, self.datetime_format)))


    def _hv(self, sid):
        '''Determines high value sids, based on bloodhound.py code'''
        hv = ['S-1-5-32-544', 'S-1-5-32-548', 'S-1-5-32-549', 'S-1-5-32-550', 'S-1-5-32-551']
        if sid in hv:
            return True
        if [a for a in ['-512', '-516', '-519', '-520'] if sid.endswith(a)]:
            return True 
        return False

    def _ft(self, sid):
        '''Fetch type of sid'''
        return self.sidLT[sid][1] if sid in self.sidLT else 'Unknown'

    def _tbs(self, sid):
        return sid if sid.startswith('S-1-5-21-') else '{}-{}'.format(self.bh_core_domain, sid)

    def convert_bloodhound_acl(self, entry: dict) -> list:
        '''Parse security descriptor from entry into Bloodhound format'''

        # mostly adapted from python bloodhound here:
        #https://github.com/fox-it/BloodHound.py/blob/d65eb614831cd30f26028ccb072f5e77ca287e0b/bloodhound/enumeration/acls.py

        # acl info here
        #https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-adts/990fb975-ab31-4bc1-8b75-5da132cd4584
        #https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-ace_header 

        out = []
        build_hb_acl = lambda w,x,y,z: {'PrincipalSID': self._tbs(w), 'PrincipalType': x, 'RightName': y, 'IsInherited': z}
        inherited = lambda x: 'INHERITED_ACE' in x['Flags']
        objectClass = self.get_class(entry)

        creator_system_sids = ['S-1-3-0', 'S-1-5-18', 'S-1-5-10'] # creater owner and local system
        allowed_dacls = ['ACCESS_ALLOWED_OBJECT_ACE', 'ACCESS_ALLOWED_ACE'] # ace types we care about


        # parse msDS-GroupMSAMembership for ReadGMSAPassword permissions
        if 'msDS-GroupMSAMembership' in entry:
            for gmsadacl in entry['msDS-GroupMSAMembership']['Dacls']:
                out.append(build_hb_acl(gmsadacl['Sid'], self._ft(gmsadacl['Sid']), 'ReadGMSAPassword', inherited(gmsadacl)))
        

        sd = entry['nTSecurityDescriptor']
        dacls = sd['Dacls']
        owner = sd['OwnerSid']
        

        ignore_conditions = [
            # no creator owner/local system
            lambda x: x['Sid'] in creator_system_sids,
            # only particular ace types
            lambda x: x['Type'] not in allowed_dacls,
            # no inherit only without inherited
            # this was based on BH.py, but not sure if the mere presence of INHERIT_ONLY should
            # be enough to consider it out of scope, the MS docco says the flag being present
            # means the ACE does not control access to the object
            lambda x: 'INHERIT_ONLY_ACE' in x['Flags'] and 'INHERITED_ACE' not in x['Flags']
        ]

        if owner not in creator_system_sids:
            out.append(build_hb_acl(owner, self._ft(owner), 'Owns', False))
        
        for dacl in dacls:
            GenericWrite = False 
            WriteDacl = False 
            WriteOwner = False 
            AllExtendedRights = False

            # ignore conditions, skip dacl if any are true
            if [a for a in ignore_conditions if a(dacl)]:
                continue

            if dacl['Type'] == 'ACCESS_ALLOWED_OBJECT_ACE':
                # inherited ace with the InheritableObjectType not matching this object type
                if 'INHERITED_ACE' in dacl['Flags'] and 'ACE_INHERITED_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', []) and dacl.get('InheritableObjectType', '') != objectClass:
                    continue

                if 'GENERIC_ALL' in dacl['Privs']:
                    # check for laps
                    if (objectClass.lower() == 'computer' and 'ACE_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', []) and 'ms-Mcs-AdmPwdExpirationTime' in entry 
                    and dacl['ControlObjectType'].lower() in ['ms-mcs-admpwd', 'allproperties']):
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'ReadLAPSPassword', inherited(dacl)))
                    else:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'GenericAll', inherited(dacl)))
                        continue # implies all other permissions

                # GenericWrite
                if 'GENERIC_WRITE' in dacl['Privs']:
                     GenericWrite = True

                if ('ADS_RIGHT_DS_WRITE_PROP' in dacl['Privs'] and objectClass.lower() in ['user', 'group', 'computer', 'gpo'] 
                and 'ACE_OBJECT_TYPE_PRESENT' not in dacl.get('Ace_Data_Flags', [])):
                    GenericWrite = True
                
                # WriteDacl
                if 'WRITE_DACL' in dacl['Privs']:
                    WriteDacl = True
                
                # WriteOwner
                if 'WRITE_OWNER' in dacl['Privs']:
                    WriteOwner = True
                
                # AllExtendedRights
                if ('ADS_RIGHT_DS_CONTROL_ACCESS' in dacl['Privs'] and objectClass.lower() in ['user', 'domain', 'computer', 'pki-certificate-template']
                and (('ACE_OBJECT_TYPE_PRESENT' not in dacl.get('Ace_Data_Flags', [])) or (dacl['ControlObjectType'] == 'AllProperties')) ):
                    AllExtendedRights = True


                if GenericWrite:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'GenericWrite', inherited(dacl)))
                if WriteDacl:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WriteDacl', inherited(dacl)))
                if WriteOwner:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WriteOwner', inherited(dacl)))
                if AllExtendedRights:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'AllExtendedRights', inherited(dacl)))                

                # https://github.com/BloodHoundAD/SharpHoundCommon/blob/1ccdb773d3af19718f410d9795ca9977019b5a85/src/CommonLib/Processors/ACLProcessor.cs 
                    

                if ('ADS_RIGHT_DS_WRITE_PROP' in dacl['Privs'] or GenericWrite) and 'ACE_OBJECT_TYPE_PRESENT' in dacl['Ace_Data_Flags']:
                    if objectClass.lower() == 'group' and dacl['ControlObjectType'] in ['Member', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'AddMember', inherited(dacl)))
                    elif objectClass.lower() == 'computer' and dacl['ControlObjectType'] in ['ms-DS-Allowed-To-Act-On-Behalf-Of-Other-Identity', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'AddAllowedToAct', inherited(dacl)))
                    elif objectClass.lower() == 'computer' and dacl['ControlObjectType'] in ['User-Account-Restrictions', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WriteAccountRestrictions', inherited(dacl)))
                    elif objectClass.lower() in ['computer', 'user', 'ms-ds-group-managed-service-account'] and dacl['ControlObjectType'] in ['ms-DS-Key-Credential-Link', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'AddKeyCredentialLink', inherited(dacl)))
                    elif objectClass.lower() == 'user' and dacl['ControlObjectType'] in ['Service-Principal-Name', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WriteSPN', inherited(dacl)))
                    elif objectClass.lower() == 'pki-certificate-template' and dacl['ControlObjectType'] in ['ms-PKI-Enrollment-Flag', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WritePKIEnrollmentFlag', inherited(dacl)))
                    elif objectClass.lower() == 'pki-certificate-template' and dacl['ControlObjectType'] in ['ms-PKI-Certificate-Name-Flag', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WritePKINameFlag', inherited(dacl)))


                if ('ADS_RIGHT_DS_SELF' in dacl['Privs'] and objectClass.lower() == 'group' and 'ACE_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', [])
                and dacl['ControlObjectType'] in ['Member', 'AllProperties']):
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'AddSelf', inherited(dacl)))
                    
                if ('ADS_RIGHT_DS_READ_PROP' in dacl['Privs'] and objectClass.lower() == 'computer' and 
                'ACE_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', []) and 'ms-Mcs-AdmPwdExpirationTime' in entry and 
                dacl['ControlObjectType'].lower() in ['ms-mcs-admpwd', 'allproperties']):
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'ReadLAPSPassword', inherited(dacl)))

                if 'ADS_RIGHT_DS_CONTROL_ACCESS' in dacl['Privs']:
                    if objectClass.lower() == 'user' and 'ACE_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', []) and dacl['ControlObjectType'] in ['User-Force-Change-Password', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'ForceChangePassword', inherited(dacl)))

                    if objectClass.lower() == 'domain' and 'ACE_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', []):
                        if dacl['ControlObjectType'] in ['DS-Replication-Get-Changes', 'AllProperties']:
                            out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'GetChanges', inherited(dacl)))
                        if dacl['ControlObjectType'] in ['DS-Replication-Get-Changes-All', 'AllProperties']:
                            out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'GetChangesAll', inherited(dacl)))
                        if dacl['ControlObjectType'] in ['DS-Replication-Get-Changes-In-Filtered-Set', 'AllProperties']:
                            out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'GetChangesInFilteredSet', inherited(dacl)))
                    
                    if objectClass.lower() == 'pki-enrollment-service' and 'ACE_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', []) and dacl['ControlObjectType'] in ['Certificate-Enrollment', 'Certificate-AutoEnrollment', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'Enroll', inherited(dacl)))

                    if objectClass.lower() == 'pki-certificate-template' and 'ACE_OBJECT_TYPE_PRESENT' in dacl.get('Ace_Data_Flags', []) and dacl['ControlObjectType'] in ['Certificate-Enrollment', 'Certificate-AutoEnrollment', 'AllProperties']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'Enroll', inherited(dacl)))
                    

                    


            elif dacl['Type'] == 'ACCESS_ALLOWED_ACE':
                if 'GENERIC_ALL' in dacl['Privs']:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'GenericAll', inherited(dacl)))
                    continue # this implies all other rights
                
                if 'ADS_RIGHT_DS_WRITE_PROP' in dacl['Privs'] and objectClass.lower() in ['user', 'group', 'computer', 'gpo', 'ms-ds-group-managed-service-account']:
                    GenericWrite = True
                
                if 'WRITE_OWNER' in dacl['Privs']:
                    WriteOwner = True
                
                if 'ADS_RIGHT_DS_CONTROL_ACCESS' in dacl['Privs'] and objectClass.lower() in ['user', 'domain', 'ms-ds-group-managed-service-account', 'computer']:
                    AllExtendedRights = True

                #if ('ADS_RIGHT_DS_CONTROL_ACCESS' in dacl['Privs'] and objectClass.lower() == 'computer'): 
                #and not dacl['Sid'].endswith('-512')):
                #dacl['Sid'] != 'S-1-5-32-544' and not dacl['Sid'].endswith('-512')):
                    #AllExtendedRights = True

                if 'WRITE_DACL' in dacl['Privs']:
                    WriteDacl = True

                if GenericWrite:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'GenericWrite', inherited(dacl)))
                if WriteDacl:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WriteDacl', inherited(dacl)))
                if WriteOwner:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'WriteOwner', inherited(dacl)))
                if AllExtendedRights:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'AllExtendedRights', inherited(dacl))) 
                if objectClass.lower() == 'pki-enrollment-service' and 'GENERIC_WRITE' in dacl['Privs']:
                    out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'ManageCA', inherited(dacl))) 
                    if 'ADS_RIGHT_DS_DELETE_CHILD' in dacl['Privs']:
                        out.append(build_hb_acl(dacl['Sid'], self._ft(dacl['Sid']), 'ManageCertificates', inherited(dacl))) 

        return out
    
    def _get_entry_id(self, entry):        
        sid = self._fp(entry, 'objectSid')
        return sid if sid else self._fp(entry, 'objectGUID').upper().translate({ord('{'):None,ord('}'):None})


    def _get_container(self, entry):
        if self.bh_parent_map:  
            pc = ','.join(self._fp(entry, 'distinguishedName').split(',')[1:])
            if pc in self.bh_parent_map:
                return self.bh_parent_map[pc]
            elif (pc.startswith('CN=Builtin,DC=')):
                return {'ObjectIdentifier': 'S-1-5-32', 'ObjectType': 'Base'} 
            #elif (pc.startswith('CN=Configuration,DC=')):
            #    return {'ObjectIdentifier': 'S-1-5-32', 'ObjectType': 'Configuration'} 
            elif (pc.startswith('DC=')):
                return None
            else:
                self.logger.debug('No parent container object identifier found in collected data for {}'.format(self._fp(entry, 'distinguishedName')))
                return None
        else:
            return None

    def _get_gplink(self, entry):
        try:
            if 'gPLink' in entry and entry['gPLink']:
                gplinks = [a.split(';') for a in self._fp(entry, 'gPLink').upper().replace('[LDAP://', '').split(']')[:-1]]
                missing_gpos = [a[0] for a in gplinks if a[0] not in self.bh_gpo_map]
                if missing_gpos:
                    self.logger.debug('The following non existent GPOs were found linked to OU "{}": {}'.format(self._fp(entry, 'distinguishedName'), ', '.join(missing_gpos)))
                present_gpos = [a for a in gplinks if a[0] in self.bh_gpo_map]
                return [{'GUID': self.bh_gpo_map[a[0]], 'IsEnforced': bool(int(a[1])) if a[1].isdigit() else False} for a in present_gpos]
            else:
                return []
        except:
            self.logger.debug('Error parsing GPLink in {}'.format(self._fp(entry, 'distinguishedName')))
            return [] #[{'GUID': '', 'IsEnforced': False}]



    def bloodhound_map_common(self, entry):
        domainName = '.'.join([a.split('=')[1] for a in self._fp(entry,'distinguishedName', '').upper().split(',') if a.startswith('DC=')])
        if not 'nTSecurityDescriptor' in entry:
            self.logger.debug('Record for "{}" is missing the security descriptor field, ACLs and dependant information will not be available'.format(entry['distinguishedName']))
        common_properties = {
            #'name': '{}@{}'.format(self._fp(entry,'sAMAccountName', '').upper(), self._fp(entry,'domain', '').upper()),
            'name': '{}@{}'.format(str(self._fp(entry, 'name')).upper(), domainName.upper()),
            'domain': domainName.upper(),
            'distinguishedname' : self._fp(entry, 'distinguishedName').upper(), 
            'displayname' : self._fp(entry,'displayName', '').upper(),
            'domainsid' : self.get_domain_sid(entry['objectSid']) if 'objectSid' in entry else '',
            'description' : self._fp(entry, 'description', [None])[0],
            #'highvalue': self._hv(entry['objectSid']) if 'objectSid' in entry else False, # not sure if this is correct, but this is no longer included in v6 so will leave it as is
            'isaclprotected': entry.get('nTSecurityDescriptor', {'IsACLProtected': None})['IsACLProtected']
        }
        return {
            'Properties': {**{a: self._fp(entry, a) for a in BH_COMMON_PROPERTIES}, **common_properties},
            'IsACLProtected': entry.get('nTSecurityDescriptor', {'IsACLProtected': None})['IsACLProtected'], # Protected DACL flag
            'IsDeleted': self._fp(entry, 'isDeleted', False),
            'ObjectIdentifier': self._get_entry_id(entry),
            'ContainedBy':  self._get_container(entry),
            'Aces': self.convert_bloodhound_acl(entry) if 'nTSecurityDescriptor' in entry else []
        }


    def _parse_cert(self, data):
        return load_certificate(FILETYPE_ASN1, data)
    
    def _parse_cert_info(self, cert):
        bcdata = [str(cert.get_extension(a)) for a in range(0, cert.get_extension_count()) if cert.get_extension(a).get_short_name() == b'basicConstraints']
        bcdata = {a.split(':')[0]: a.split(':')[1] for a in bcdata[0].split(', ')} if len(bcdata) > 0 else {}
        bcpathlen = int(bcdata.get('pathlen', 0))
        out = {
            'certthumbprint': cert.digest('sha1').decode('utf8').replace(':', ''),
            'certname': cert.digest('sha1').decode('utf8').replace(':', ''),
            # the following does not seem to detect whether basicContraints ext is present, but that it has more than just CA field???
            'hasbasicconstraints' : bool(len([a for a in bcdata if a != 'CA']) > 0), 
            'basicconstraintpathlength' : bcpathlen, 
            'caname' : [a[1] for a in cert.get_subject().get_components() if a[0] == b'CN'][0]
        }
        return out


    def bloodhound_map_enterpriseca(self, entry):
        domainName = '.'.join([a.split('=')[1] for a in self._fp(entry,'distinguishedName', '').upper().split(',') if a.startswith('DC=')])
        certs = [self._parse_cert(unhexlify(a)) for a in self._fp(entry,'cACertificate', [])]
        cert1 = self._parse_cert_info(certs[0])
        domainName = '.'.join([a.replace('DC=', '').upper() for a in self._fp(entry, 'distinguishedName', '').split(',') if a.startswith('DC=')])
        out = self.bloodhound_map_common(entry)
        unique_properties = {
            'name' : '{}@{}'.format(self._fp(entry, 'name').upper(), domainName.upper()),
            'domain': domainName.upper(),
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
            'flags': ', '.join(self._fp(entry, 'flags', [])), 
            'caname' : self._fp(entry, 'name'),
            'dnshostname': self._fp(entry, 'dNSHostName'),
            'certthumbprint' : cert1['certthumbprint'],
            'certname': cert1['certname'],
            'certchain': [a.digest('sha1').decode('utf8').replace(':', '') for a in certs],
            'hasbasicconstraints': cert1['hasbasicconstraints'],
            'basicconstraintpathlength': cert1['basicconstraintpathlength']
        }
        out['Properties'].update(unique_properties)
        out['HostingComputer'] = None # CONFIRM - string, looks to be the SID of the computer with the dnshostname, not in LDAP
        out['CARegistryData'] = None # CONFIRM - not in LDAP https://github.com/BloodHoundAD/SharpHoundCommon/blob/1ccdb773d3af19718f410d9795ca9977019b5a85/src/CommonLib/OutputTypes/CARegistryData.cs
        out['EnabledCertTemplates'] = [self.bh_cert_temp_map[a] for a in self._fp(entry, 'certificateTemplates', [])]
        del out['Properties']['displayname']
        return out



    def bloodhound_map_aiaca(self, entry):
        domainName = '.'.join([a.split('=')[1] for a in self._fp(entry,'distinguishedName', '').upper().split(',') if a.startswith('DC=')])
        certs = [self._parse_cert(unhexlify(a)) for a in self._fp(entry,'cACertificate', [])]
        cert1 = self._parse_cert_info(certs[0])
        out = self.bloodhound_map_common(entry)
        unique_properties = {
            'name' : '{}@{}'.format(self._fp(entry, 'name').upper(), domainName.upper()),
            'domain': domainName.upper(),
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
            'certchain': [a.digest('sha1').decode('utf8').replace(':', '') for a in certs],
            'certthumbprint' : cert1['certthumbprint'],
            'certname': cert1['certname'],
            'hascrosscertificatepair' : True if 'crossCertificatePair' in entry else False,
            'crosscertificatepair': [self._parse_cert(unhexlify(a)).digest('sha1').decode('utf8').replace(':', '') for a in self._fp(entry,'crossCertificatePair', [])], # CONFIRM I think this is right 
            'hasbasicconstraints': cert1['hasbasicconstraints'],
            'basicconstraintpathlength': cert1['basicconstraintpathlength']
        }
        out['Properties'].update(unique_properties)
        del out['Properties']['displayname']
        return out


    def bloodhound_map_ntauthstore(self, entry):
        domainName = '.'.join([a.split('=')[1] for a in self._fp(entry,'distinguishedName', '').upper().split(',') if a.startswith('DC=')])
        certs = [self._parse_cert(unhexlify(a)) for a in self._fp(entry,'cACertificate', [])]
        out = self.bloodhound_map_common(entry)
        unique_properties = {
            'name' : '{}@{}'.format(self._fp(entry, 'name').upper(), domainName.upper()),
            'domain': domainName.upper(),
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
            'certthumbprints': [a.digest('sha1').decode('utf8').replace(':', '') for a in certs]
        }
        out['Properties'].update(unique_properties)
        out['DomainSID'] = {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else ''
        del out['Properties']['displayname']
        return out


    def bloodhound_map_rootca(self, entry):
        domainName = '.'.join([a.split('=')[1] for a in self._fp(entry,'distinguishedName', '').upper().split(',') if a.startswith('DC=')])
        certs = [self._parse_cert(unhexlify(a)) for a in self._fp(entry,'cACertificate', [])]
        cert1 = self._parse_cert_info(certs[0])
        out = self.bloodhound_map_common(entry)
        unique_properties = {
            'name' : '{}@{}'.format(self._fp(entry, 'name').upper(), domainName.upper()),
            'domain': domainName.upper(),
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
            'certchain': [a.digest('sha1').decode('utf8').replace(':', '') for a in certs],
            'certthumbprint' : cert1['certthumbprint'],
            'certname': cert1['certname'],
            'hasbasicconstraints': cert1['hasbasicconstraints'],
            'basicconstraintpathlength': cert1['basicconstraintpathlength']
        }
        out['Properties'].update(unique_properties)
        out['DomainSID'] = {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
        del out['Properties']['displayname']
        return out

    # TODO: confirm completenesss -  https://m365internals.com/2022/11/07/investigating-certificate-template-enrollment-attacks-adcs/
    # https://support.bloodhoundenterprise.io/hc/en-us/articles/22454652589083-CertTemplate
    # https://github.com/BloodHoundAD/SharpHoundCommon/blob/1ccdb773d3af19718f410d9795ca9977019b5a85/src/CommonLib/Processors/LDAPPropertyProcessor.cs#L484
    # https://support.bloodhoundenterprise.io/hc/en-us/articles/22454652589083-CertTemplate
    def bloodhound_map_certtemplate(self, entry):
        domainName = '.'.join([a.split('=')[1] for a in self._fp(entry,'distinguishedName', '').upper().split(',') if a.startswith('DC=')])
        out = self.bloodhound_map_common(entry)
        ap = self._fp(entry, 'msPKI-Certificate-Application-Policy', [])
        schema = self._fp(entry, 'msPKI-Template-Schema-Version', 0)
        eku = self._fp(entry, 'pKIExtendedKeyUsage', [])
        effectiveekus = ap if not (schema == 1 and eku) else eku
        unique_properties = {
            'name' : '{}@{}'.format(self._fp(entry, 'name').upper(), domainName.upper()),
            'domain': domainName.upper(),
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
            'displayname': self._fp(entry, 'displayName', ''),
            'validityperiod' : self._fp(entry, 'pKIExpirationPeriod', ''), 
            'renewalperiod': self._fp(entry, 'pKIOverlapPeriod', ''), 
            'schemaversion' : schema, 
            'enrollmentflag': ', '.join([a.replace('CT_FLAG_', '') for a in self._fp(entry, 'msPKI-Enrollment-FlagFlags', [])]), 
            'oid' : self._fp(entry, 'msPKI-Cert-Template-OID'),
            'requiresmanagerapproval' : 'CT_FLAG_PEND_ALL_REQUESTS' in self._fp(entry, 'msPKI-Enrollment-FlagFlags', []), 
            'nosecurityextension' : 'CT_FLAG_NO_SECURITY_EXTENSION' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'certificatenameflag': ', '.join([a.replace('CT_FLAG_', '') for a in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', [])]), 
            'enrolleesuppliessubject': 'CT_FLAG_ENROLLEE_SUPPLIES_SUBJECT' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'subjectaltrequireupn': 'CT_FLAG_SUBJECT_ALT_REQUIRE_UPN' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'subjectaltrequiredns': 'CT_FLAG_SUBJECT_ALT_REQUIRE_DNS' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'subjectaltrequiredomaindns': 'CT_FLAG_SUBJECT_ALT_REQUIRE_DOMAIN_DNS' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'subjectaltrequireemail': 'CT_FLAG_SUBJECT_ALT_REQUIRE_EMAIL' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'subjectaltrequirespn': 'CT_FLAG_SUBJECT_ALT_REQUIRE_SPN' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'subjectrequireemail': 'CT_FLAG_SUBJECT_REQUIRE_EMAIL' in self._fp(entry, 'msPKI-Certificate-Name-FlagFlags', []),
            'ekus': eku, 
            'certificateapplicationpolicy': ap, 
            'authorizedsignatures': self._fp(entry, 'msPKI-RA-Signature', 0), 
            'applicationpolicies': self._fp(entry, 'msPKI-RA-Application-Policies', []), 
            'issuancepolicies': self._fp(entry, 'msPKI-RA-Policies', []), 
            'effectiveekus':  effectiveekus, 
            'authenticationenabled': bool(len(effectiveekus) == 0) or bool([a for a in effectiveekus if a in AUTHENTICATION_OIDS])         
        }
        out['Properties'].update(unique_properties)
        return out


    def bloodhound_map_container(self, entry):
        domainName = '.'.join([a.split('=')[1] for a in self._fp(entry,'distinguishedName', '').upper().split(',') if a.startswith('DC=')])
        out = {**self.bloodhound_map_common(entry)}
        out['DomainSID'] = {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
        out['ChildObjects'] = []
        unique_properties = {
            'name' : '{}@{}'.format(str(self._fp(entry, 'name')).upper(), domainName.upper()),
            'domain': domainName.upper(),
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else ''
        }
        out['Properties'].update(unique_properties)
        del out['Properties']['displayname']
        del out['Properties']['whencreated']
        del out['Properties']['description']
        return out

    def _bh_parse_allowed_to_act(self, entry):
        out = []
        if 'msDS-AllowedToActOnBehalfOfOtherIdentity' in entry:
            for dacl in entry['msDS-AllowedToActOnBehalfOfOtherIdentity']['Dacls']:
                matchsid = [a for a in self.bh_member_map.values() if a['ObjectIdentifier'] == dacl['Sid']]
                if matchsid:
                    out.append(matchsid[0])
        return out



    def bloodhound_map_computer(self, entry):
        out = {**self.bloodhound_map_common(entry)}
        out['PrimaryGroupSID'] = '{}-{}'.format(self.get_domain_sid(self._fp(entry, 'objectSid')), self._fp(entry, 'primaryGroupID'))
        out['HasSIDHistory'] = [{'ObjectIdentifier': a, 'ObjectType': 'Computer'} for a in self._fp(entry, 'sIDHistory', [])]
        out['AllowedToDelegate'] = self._bh_parse_delegation(entry) 
        out['DcomUsers'] = [] # cannt be collected from LDAP
        out['LocalAdmins'] = [] # cannt be collected from LDAP
        out['PSRemoteUsers'] = [] # cannt be collected from LDAP
        out['PrivilegedSessions'] = [] # cannt be collected from LDAP
        out['RegistrySessions'] = [] # cannt be collected from LDAP
        out['RemoteDesktopUsers'] = [] # cannt be collected from LDAP
        out['Sessions'] = [] # cannt be collected from LDAP
        out['Status'] = None # can you connect to computer, probably not suitable for this tool but if not null format is { "Connectable": false, "Error": "PwdLastSetOutOfRange" }
        out['AllowedToAct'] = self._bh_parse_allowed_to_act(entry)
        out['IsDC'] = 'SERVER_TRUST_ACCOUNT' in self._fp(entry, 'userAccountControlFlags', [])
        out['DumpSMSAPassword'] = [] # Standalone Managed Service Account, requires LSA secret dumping on local machine
        out['LocalGroups'] = [] # requires LSA
        out['UserRights'] = [] # requires LSA
        out['DomainSID'] = self.get_domain_sid(self._fp(entry, 'objectSid'))
        out['DCRegistryData'] = { "CertificateMappingMethods": None, "StrongCertificateBindingEnforcement": None  } # requires LSA
        for key in ['Sessions', 'PrivilegedSessions', 'RegistrySessions']:
            out[key] = {'Results': [],'Collected': False, 'FailureReason': None} # requires LSA

        #out['Properties'].update({a: self._fp(entry, a) for a in BH_COMPUTER_PROPERTIES})
        unique_properties = {
            'email': self._fp(entry, 'mail'),
            'isdc': 'SERVER_TRUST_ACCOUNT' in self._fp(entry, 'userAccountControlFlags', []),
            'lastlogontimestamp': self._fp(entry, 'lastLogontimeStamp', -1),
            'lastlogon' : (lambda x: x if x and int(x) > 0 else 0)(self._fp(entry, 'lastLogon')),
            'operatingsystem': self._fp(entry, 'operatingSystem'),
            'pwdlastset': (lambda x: x if x and int(x) > 0 else 0)(self._fp(entry, 'pwdLastSet')),
            'name' : '{}.{}'.format(self._fp(entry, 'name'), self._fp(entry,'domain', '').upper()),
            'haslaps': True if 'ms-Mcs-AdmPwdExpirationTime' in entry else False,
            'serviceprincipalnames': self._fp(entry, 'servicePrincipalName', []),
            'unconstraineddelegation': True if 'TRUSTED_FOR_DELEGATION' in self._fp(entry, 'userAccountControlFlags') else False,
            'trustedtoauth': True if 'TRUSTED_TO_AUTH_FOR_DELEGATION' in self._fp(entry, 'userAccountControlFlags') else False,
            'samaccountname': self._fp(entry, 'sAMAccountName'),
            'sidhistory' : self._fp(entry,'sidHistory', []),
            'enabled': False if 'ACCOUNTDISABLE' in self._fp(entry, 'userAccountControlFlags') else True,
        }
        out['Properties'].update(unique_properties)
        del out['Properties']['displayname']
        return out


    #https://github.com/BloodHoundAD/SharpHoundCommon/blob/main/src/CommonLib/OutputTypes/Domain.cs
    def bloodhound_map_domain(self, entry):
        domainName = '.'.join([a.replace('DC=', '').upper() for a in self._fp(entry, 'distinguishedName', '').split(',') if a.startswith('DC=')])
        out = {**self.bloodhound_map_common(entry)}
        out['ChildObjects'] = [] 
        out['GPOChanges'] = {'LocalAdmins': [], 'RemoteDesktopUsers': [], 'DcomUsers': [], 'PSRemoteUsers': [], 'AffectedComputers': []} # requires GPO disk parsing (?)
        out['Links'] = self._get_gplink(entry) # [{'IsEnforced': False, 'GUID': ''}] 
        out['Trusts'] = [] 
        #out['Properties'].update({a: self._fp(entry, a) for a in BH_DOMAIN_PROPERTIES})
        unique_properties = {
            'domainsid': self._fp(entry, 'objectSid'),
            'domain': domainName,
            'name' : domainName,
            'functionallevel': (FUNCTIONAL_LEVELS[self._fp(entry,'msDS-Behavior-Version')] 
                if self._fp(entry,'msDS-Behavior-Version') in FUNCTIONAL_LEVELS else self._fp(entry,'msDS-Behavior-Version')),
            'whencreated' : '',
            'collected': True
        }
        out['Properties'].update(unique_properties)
        del out['Properties']['displayname']
        return out
    
    def _bh_map_group_members(self, entry):
        out = []
        for member in self._fp(entry, 'member', []):
            if member in self.bh_member_map:
                out.append(self.bh_member_map[member])
            elif 'ForeignSecurityPrincipals' in member:
                out.append({'ObjectIdentifier': self._tbs(member.split(',')[0].split('=')[-1]), 'ObjectType': 'Group'})
            else:
                self.logger.debug('Group member {} could not be mapped to an object type'.format(member))
                out.append({'ObjectIdentifier': member, 'ObjectType': 'Unknown'})
        return out



    def bloodhound_map_group(self, entry):
        domainName = '.'.join([a.replace('DC=', '').upper() for a in self._fp(entry, 'distinguishedName', '').split(',') if a.startswith('DC=')])
        out = self.bloodhound_map_common(entry)
        #out['Properties'].update({a: self._fp(entry, a) for a in BH_GROUP_PROPERTIES})
        out['Members'] = self._bh_map_group_members(entry) 
        out['ObjectIdentifier'] = self._tbs(self._fp(entry, 'objectSid'))
        unique_properties = {
            'admincount': bool(self._fp(entry, 'adminCount')),
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
            'samaccountname': self._fp(entry, 'SAMAccountName')
        }
        out['Properties'].update(unique_properties)
        del out['Properties']['displayname']
        return out

    def bloodhound_map_gpo(self, entry):
        domainName = '.'.join([a.replace('DC=', '').upper() for a in self._fp(entry, 'distinguishedName', '').split(',') if a.startswith('DC=')])
        out = self.bloodhound_map_common(entry)
        #out['Properties'].update({a: self._fp(entry, a) for a in BH_GPO_PROPERTIES})
        unique_properties = {
            'name' : '{}@{}'.format(self._fp(entry, 'displayName').upper(), domainName),
            'gpcpath' : self._fp(entry, 'gPCFileSysPath').upper(),
            'domainsid' : {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else ''
        }
        out['Properties'].update(unique_properties)
        return out

    def bloodhound_map_ou(self, entry):
        domainName = '.'.join([a.replace('DC=', '').upper() for a in self._fp(entry, 'distinguishedName', '').split(',') if a.startswith('DC=')])
        out = {**self.bloodhound_map_common(entry)}
        out['ChildObjects'] = []
        out['GPOChanges'] = {'LocalAdmins': [], 'RemoteDesktopUsers': [], 'DcomUsers': [], 'PSRemoteUsers': [], 'AffectedComputers': []}
        out['Links'] = self._get_gplink(entry) # [{'IsEnforced': False, 'GUID': ''}] 
        unique_properties = {
            'domainsid': {self.domainLT[a]:a for a in self.domainLT}[domainName] if domainName in self.domainLT.values() else '',
            'blocksinheritance': int(self._fp(entry, 'gpoptions', 0)) == 1
        }
        out['Properties'].update(unique_properties)
        del out['Properties']['displayname']
        return out
    

    def _bh_parse_spn(self, spnentry):
        p = spnentry.split('/')[1].split(':')
        name = p[0].lower()
        port = int(p[1]) if len(p) > 1 and p[1].isdigit() else 1433
        sids = [self.bh_computer_map[a] for a in self.bh_computer_map.keys() if name in a.split(',')]
        if len(sids) > 0:
            return {"ComputerSID": sids[0], "Port": port}
        else:
            self.logger.debug('Could not resolve SPN {} to a computer SID'.format(spnentry))
            return {}

    
    def _bh_parse_spn_targets(self, spnentry):
        if 'mssqlsvc' in spnentry.lower():
            pspn = self._bh_parse_spn(spnentry)
            if pspn:
                pspn['Service'] = 'SQLAdmin'
            return pspn
        else:
            return {}
        

    def _bh_parse_delegation(self, entry):
        if 'TRUSTED_TO_AUTH_FOR_DELEGATION' in self._fp(entry, 'userAccountControlFlags', []):
            out = []
            for spnentry in self._fp(entry, 'msDS-AllowedToDelegateTo', []):
                spn = self._bh_parse_spn(spnentry)
                if spn and 'ComputerSID' in spn:
                    if not spn['ComputerSID'] in [a['ObjectIdentifier'] for a in out if 'ObjectIdentifier' in a]:
                        out.append({'ObjectIdentifier': spn['ComputerSID'], 'ObjectType': 'Computer'})
            return out
        else:
            return []



    def bloodhound_map_user(self, entry):
        '''Maps user entries from dump into a BloodHound compatible format'''
        out = {**self.bloodhound_map_common(entry)}
        out['SPNTargets'] = [b for b in [self._bh_parse_spn_targets(a) for a in self._fp(entry, 'servicePrincipalName', [])] if b]
        out['HasSIDHistory'] = [{'ObjectIdentifier': a, 'ObjectType': 'User'} for a in self._fp(entry, 'sIDHistory', [])] # CONFIRM: think this is correct
        out['AllowedToDelegate'] = self._bh_parse_delegation(entry) 
        out['PrimaryGroupSID'] = '{}-{}'.format(self.get_domain_sid(self._fp(entry, 'objectSid', '')), self._fp(entry, 'primaryGroupID'))
        #out['Properties'].update({a: self._fp(entry, a) for a in BH_USER_PROPERTIES})
        unique_properties = {
            'displayname': self._fp(entry, 'displayName'),
            'email': self._fp(entry, 'mail', ''),
            'homedirectory': self._fp(entry, 'homedirectory', ''),
            'lastlogontimestamp': self._fp(entry, 'lastLogontimeStamp', -1),
            'lastlogon': (lambda x: x if x and int(x) > 0 else 0)(self._fp(entry, 'lastLogon')),
            'pwdlastset': (lambda x: x if x and int(x) > 0 else 0)(self._fp(entry, 'pwdLastSet')),
            'admincount': bool(self._fp(entry, 'adminCount')),
            'sensitive': True if 'NOT_DELEGATED' in self._fp(entry, 'userAccountControlFlags', []) else False,
            'dontreqpreauth': True if 'DONT_REQ_PREAUTH' in self._fp(entry, 'userAccountControlFlags', []) else False,
            'passwordnotreqd': True if 'PASSWD_NOTREQD' in self._fp(entry, 'userAccountControlFlags', []) else False,
            'unconstraineddelegation': True if 'TRUSTED_FOR_DELEGATION' in self._fp(entry, 'userAccountControlFlags', []) else False,
            'pwdneverexpires': True if 'DONT_EXPIRE_PASSWORD' in self._fp(entry, 'userAccountControlFlags', []) else False,
            'enabled': False if 'ACCOUNTDISABLE' in self._fp(entry, 'userAccountControlFlags', []) else True,
            'trustedtoauth': True if 'TRUSTED_TO_AUTH_FOR_DELEGATION' in self._fp(entry, 'userAccountControlFlags', []) else False,
            'serviceprincipalnames': self._fp(entry, 'servicePrincipalName', []),
            'hasspn': bool(self._fp(entry, 'servicePrincipalName', [])),
            'unixpassword': self._fp(entry,'unixUserPassword'),
            'unicodepassword': self._fp(entry,'unicodePwd'),
            'userpassword': self._fp(entry,'userPassword'),
            'sfupassword': self._fp(entry,'msSFU30Password'),
            'logonscript': self._fp(entry,'scriptPath'),
            'samaccountname': self._fp(entry,'sAMAccountName'),
            'sidhistory' : self._fp(entry,'sidHistory', []),
            'title': self._fp(entry,'title')
        }
        if self.get_class(entry).lower() == 'ms-ds-group-managed-service-account':
            unique_properties['gmsa'] = True
        out['Properties'].update(unique_properties)
        
        return out
    
    def bloodhound_map_trusted_domains(self, entry):
        return {
            'TargetDomainName': self._fp(entry, 'trustPartner').upper(), 
            'TargetDomainSid': self._fp(entry, 'securityIdentifier'), 
            'IsTransitive' : self._fp(entry, 'transitive'),
            'TrustDirection': LOOKUPS['trustDirection'][self._fp(entry, 'trustDirection')].title(),
            'TrustType': self._bh_trust_type(entry),
            'SidFilteringEnabled' : self._fp(entry, 'sidFiltering')
        }
    
    def _bh_trust_type(self, entry):
        flags = entry.get('trustAttributesFlags', [])
        if 'WITHIN_FOREST' in flags:
            return 'ParentChild'
        elif 'FOREST_TRANSITIVE' in flags:
            return 'Forest'
        elif not [a for a in flags if a in ['WITHIN_FOREST', 'FOREST_TRANSITIVE']]:
            return 'External'
        else:
            return 'Unknown'


    def _get_containter_def(self, entry):
        oc_to_name = lambda x : 'Container' if 'Container' in x else 'Domain' if 'Domain' in x else 'Configuration' if 'Configuration' in x else 'OU'
        return {'ObjectIdentifier': self._get_entry_id(entry), 'ObjectType': oc_to_name(self._fp(entry, 'objectCategory')) }


    def bloodhound_convert(self, dump, filename_base=''):
        '''Takes in complete json dump and writes output to individual bloodhound files'''
        self.logger.info('Processing data into Bloodhound format')
        timestamp = self.generate_timestamp()
        methods_included = ['ACL', 'ObjectProps', 'Trusts', 'UserRights'] 
        for key in ['containers', 'groups']:
            if key in dump:
                methods_included.append(key.capitalize().rstrip('s'))
        if [a for a in dump if a.startswith('cert')]:
            methods_included.append('CertServices')
        methods = reduce(lambda x, y: x | y,[MANUAL_FLAGS['collectionMethods'][a] for a in methods_included])
        if 'domains' in dump:
            self.bh_core_domain = '.'.join([a.replace('DC=', '').upper() for a in self._fp(dump['domains'][0], 'distinguishedName', '').split(',') if a.startswith('DC=')])
        else:
            self.logger.info('No domain info in dump file, this conversion is probably going to fail...')

        if 'computers' in dump:
            self.bh_computer_map = {','.join([self._fp(a, 'dNSHostName', '').lower(), self._fp(a, 'name', '').lower() ]) : self._fp(a, 'objectSid') for a in dump['computers']}

        for key in ['users', 'groups', 'computers']:
            map_cat = lambda x: 'User' if x.split(',')[0].split('=')[-1] == 'Person' else x.split(',')[0].split('=')[-1]
            mapentry = {self._fp(a, 'distinguishedName'): {'ObjectIdentifier': self._fp(a, 'objectSid'), 'ObjectType': map_cat(self._fp(a, 'objectCategory'))} for a  in dump[key]}
            self.bh_member_map = {**self.bh_member_map, **mapentry}
        
        for key in ['domains', 'containers', 'ous']:
            mapentry = {self._fp(a, 'distinguishedName'): self._get_containter_def(a) for a in dump[key]}
            self.bh_parent_map = {**self.bh_parent_map, **mapentry}

        if 'gpos' in dump:
            for entry in dump['gpos']:
                self.bh_gpo_map[self._fp(entry, 'distinguishedName').upper()] = self._fp(entry, 'objectGUID').upper().translate({ord('{'):None,ord('}'):None})

        if 'certtemplates' in dump:
            for entry in dump['certtemplates']:
                self.bh_cert_temp_map[self._fp(entry, 'name')] = {'ObjectIdentifier': self._fp(entry, 'objectGUID').upper().translate({ord('{'):None,ord('}'):None}), 'ObjectType': 'CertTemplate'}


        parse_categories = ['certauthorities', 'certenrollservices', 'certtemplates', 'containers', 'computers', 'domains', 'gpos', 'groups', 'ous', 'users']

        ca_categories = {
            'aiacas': 'CN=AIA,CN=PUBLIC KEY SERVICES,CN=SERVICES,CN=CONFIGURATION', 
            'ntauthstores': 'CN=PUBLIC KEY SERVICES,CN=SERVICES,CN=CONFIGURATION', 
            'rootcas': 'CN=CERTIFICATION AUTHORITIES,CN=PUBLIC KEY SERVICES,CN=SERVICES,CN=CONFIGURATION'
        }

        for key in parse_categories: 
            if key in dump:
                if key =='certauthorities':
                    for fieldname in ca_categories:
                        # pre filter based on parent container
                        data = [a for a in dump[key] if a['distinguishedName'].split(',', 1)[1].upper().startswith(ca_categories[fieldname])]
                        self._bh_parser_func(dump, data, fieldname, methods, filename_base, timestamp)
                else:
                    fieldname = key if key != 'certenrollservices' else 'enterprisecas'
                    self._bh_parser_func(dump, dump[key], fieldname, methods, filename_base, timestamp)


    def _bh_parser_func(self, dump, data, fieldname, methods, filename_base, timestamp):
        self.logger.info('Generating Bloodhound {} file'.format(fieldname))
        processed = {}
        processed['data'] = [getattr(self, 'bloodhound_map_{}'.format(fieldname.rstrip('s')))(a) for a in data]
        if fieldname == 'domains' and 'trusted_domains' in dump:
            processed['data'][0]['Trusts'] = [self.bloodhound_map_trusted_domains(a) for a in dump['trusted_domains']]
        processed['meta'] = {'methods' : methods, 'type' : fieldname, 'count': len(data), 'version' : 6} # methods
        fn = '{}{}_{}.json'.format(filename_base + '_' if filename_base else '', timestamp, fieldname)
        self.logger.debug('Writing Bloodhound {} output to: {}'.format(fieldname, fn))
        open(fn, 'w').write(json.dumps(processed, indent=4))


    def import_dump(self, dumpfile):
        '''Import a previously completed AD dump from file to populate internal structures and return data'''
        self.logger.info('Importing dump from file {}'.format(dumpfile))
        dump = json.load(open(dumpfile))
        if 'domains' in dump:
            self.domainLT = {a['objectSid']: '.'.join([b.split('=')[1].upper() for b in a['distinguishedName'].split(',')]) for a in dump['domains']}
            self.domainLTNB = {a['objectSid']: a['name'].upper() for a in dump['domains']}
        if 'schema' in dump:
            additional = {a['schemaIDGUID']: a['name'] for a in dump['schema'] if 'schemaIDGUID' in a and a['schemaIDGUID']}
            if additional:
                self.object_types.update(additional)
        if 'meta' in dump:
            self.output_timestamp = dump['meta']['end_time']
        for object in ['users', 'groups', 'computers']:
            self.update_sidlt(dump[object])
        self.logger.info('Import complete')
        return dump


    # allow building sid lookup table into already completed json dump files
    def export_dump(self, dumpfile):
        out = self.import_dump(dumpfile)
        out['meta'] = {'end_time' : self.output_timestamp, 'methods' : list([a for a in out.keys() if a not in ['schema', 'meta']]), 'sid_lookup' : self.sidLT}
        return out
        


def check_ipython():
    """Returns True if script is running in interactive iPython shell"""
    try:
        get_ipython()
        return True
    except NameError:
        return False

class MyParser(argparse.ArgumentParser):
    """
    Custom argument parser
    """
    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help()
        sys.exit(2)


def create_logger(loglevel: str, name: str) -> Logger:
    logger = logging.getLogger(name)
    logger.setLevel(loglevel)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


# creates a kerberos config file and configures it for use
# only required when your workstation cannot identify the KDC via DNS
def create_kerberos_config(realm, kdc):
    template = KRB_CONF_TEMPLATE.replace('[REALM]', realm.upper()).replace('[KDC]', kdc).replace('[REALM_LOWER]', realm.lower())
    krb_config = os.path.join(tempfile.gettempdir(), 'krb5.conf')
    open(krb_config, 'w').write(template)
    os.environ["KRB5_CONFIG"] = krb_config
    return krb_config
    


class PKCS12Cert:
    '''Object representing PKCS12 cert allowing extraction of useful data'''
    def __init__(self, pkcsfile):
        try:
            certdata = open(pkcsfile, 'rb').read()
            p12 = pkcs12.load_pkcs12(certdata, None)
        except (TypeError, ValueError) as e:
            raise Exception(error=f'Error in loading certificate: {str(e)}')
        self.certificate = p12.cert
        self.intermediates = p12.additional_certs
        self.private_key = p12.key

    def get_certificate(self):
        return self.certificate.certificate.public_bytes(
            encoding=serialization.Encoding.PEM).strip()

    def get_intermediates(self):
        if self.intermediates:
            int_data = [
                ic.certificate.public_bytes(
                    encoding=serialization.Encoding.PEM).strip()
                for ic in self.intermediates
            ]
            return int_data
        return None

    def get_private_key(self):
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()).strip()

    def get_private_key_passphrase(self):
        return None


def create_temporary_cert_files(pkcs12file):
    certobj = PKCS12Cert(pkcs12file)
    cert_data = certobj.get_certificate()
    key_data = certobj.get_private_key()
    pem_certfile = tempfile.NamedTemporaryFile()
    pem_certfile.write(cert_data)
    pem_certfile.flush()
    pem_keyfile = tempfile.NamedTemporaryFile()
    pem_keyfile.write(key_data)
    pem_keyfile.flush()
    return (pem_certfile, pem_keyfile)


def command_line():
    parser = MyParser()
    input_arg_group = parser.add_argument_group('Operation')
    mgroup = input_arg_group.add_mutually_exclusive_group(required=True)
    mgroup.add_argument('-d', '--domain-controller', type=str, help='Domain controller address to connect to if performing a fresh collection. If using Kerberos auth, provide a domain name')
    mgroup.add_argument('-i', '--input-file', type=str, help='Filename of a previous output file to export into Bloodhound format')
    
    
    input_arg_group.add_argument('-target-ip', type=str, default=None, help='IP Address of the target machine. If omitted it will use whatever was specified as target')
    input_arg_group.add_argument('-ssl', action='store_true', help='Force use of SSL for LDAP connection')
    input_arg_group.add_argument('-ssl_protocol', type=str, default=None, help='Use a specific SSL/TLS protocol version')
    input_arg_group.add_argument('-start_tls', action='store_true', help='Attempt to upgrade the plain text LDAP port/connection to SSL (post authentication)')
    input_arg_group.add_argument('-methods', type=str, default='', help='Comma seperated list of collection methods to use')
    input_arg_group.add_argument('-sleep', type=int, default=0, help='Time in seconds to sleep between each paged LDAP request and each enumeration method')
    input_arg_group.add_argument('-jitter', type=int, default=0, help='Set to a positive integer to add a random value of up to that many seconds to the sleep delay')
    input_arg_group.add_argument('-pagesize', type=int, default=500, help='Page size for LDAP requests')
    input_arg_group.add_argument('-custom-query', type=str, default=None, help='Perform custom LDAP query provided as string instead of normal enumeration')
    input_arg_group.add_argument('-port', type=int, default=None, help='Port to connect to. Determined automatically if not specified.')
    input_arg_group.add_argument('-query-config', type=str, default=None, help='Provide JSON config file that defines custom LDAP queries and attribute lists for each query category, overriding other settings')
    input_arg_group.add_argument('-bh-attributes', action='store_true', help='Collect object attributes compatible with BloodHound with object props only')
    input_arg_group.add_argument('-attributes', type=str, default=None, help='Provide comma seperated list of object attributes to return for all queries. Best used for custom queries as some attributes are required for normal operation.')
    
    mgroup_schema = input_arg_group.add_mutually_exclusive_group()
    mgroup_schema.add_argument('-only-schema', action='store_true', help='Only perform schema extraction')
    mgroup_schema.add_argument('-no-schema', action='store_true', help='Dont perform schema extraction')

    auth_arg_group = parser.add_argument_group('Authentication')
    agroup = auth_arg_group.add_mutually_exclusive_group()
    agroup.add_argument('-u', '--username', type=str, default = '', help='Username, use DOMAIN\\username format for NTLM authentication, user@domain for SIMPLE auth')
    agroup.add_argument('-k', '--kerberos', action='store_true', help='Authenticate using Kerberos via KRB5CCNAME environment variable')
    agroup_cert = agroup.add_mutually_exclusive_group()
    agroup_cert.add_argument('-cc', '--pem_client_cert', type=str, default = None, help='Authenticate using client certificate and key in PEM format - PEM cert file')
    agroup.add_argument('-ck', '--pem_client_key', type=str, default = None, help='Authenticate using client certificate and key in PEM format - PEM key file')
    agroup_cert.add_argument('-pc', '--pkcs12_client_cert', type=str, default = None, help='Authenticate using client certificate and key in (passwordless) PKCS12 format')

    auth_arg_group.add_argument('-no-password', action='store_true', help='Attempt to logon with an empty password (requires username in NTLM format)')
    auth_arg_group.add_argument('-password', type=str,  default = '', help='Password, hashes also accepted for NTLM. Will be prompted for if not provided and no-password not set')
    auth_arg_group.add_argument('-realm', type=str,  default = None, help='Manually specify a realm for your Kerberos ticket if you cannot resolve it from DNS')
    auth_arg_group.add_argument('-dc-ip', type=str,  default = None, help='Manually specify IP address of the domain controller for Kerberos ticket')

    output_arg_group = parser.add_argument_group('Output')
    output_arg_group.add_argument('-output', type=str,  help='Output filename. An automatically generated name will be used if not provided.')
    output_arg_group.add_argument('-bh-output', action='store_true',  help='Also output Bloodhound compatible files (EXPERIMENTAL and UNFINISHED functionality)')
    output_arg_group.add_argument('-loglevel', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default='WARNING', help='Set logging level')
    output_arg_group.add_argument('-exclude-raw', action='store_true', help='Exclude raw binary field data from output')

    args = parser.parse_args()
    raw = True if not args.exclude_raw else False
    logger = create_logger(args.loglevel, 'AdDumper')
    k_temp_file = None

    if args.input_file:
        if not args.bh_output:
            print('The bloodhound export must be enabled in import mode, use -b option')
            sys.exit(2)
        dumper = AdDumper(logger=logger, raw=raw, import_mode=True)
        data = dumper.import_dump(args.input_file)
    else:
        if args.realm:
            dc = args.dc_ip if args.dc_ip else args.target_ip if args.target_ip else args.domain_controller
            k_temp_file = create_kerberos_config(args.realm, dc)
            logger.debug('Writing temporary "KRB5_CONFIG" file "{}" to configure: Realm: {}, KDC: {}'.format(k_temp_file, args.realm, dc))

        password = args.password
        if args.username and not args.password and not args.no_password:
            print('Please enter the password for {}:'.format(args.username))
            password = getpass.getpass()
        if args.username and args.no_password:
            if not '\\' in args.username:
                print('No password not supported for SIMPLE binds, please specify username in DOMAIN\\username format to use NTLM')
                sys.exit()

        if args.query_config:
            try:
                query_config = json.load(open(args.query_config))
            except Exception as e:
                print('Query config file {} could not be opened with error: {}'.format(args.query_config, e.msg))
        else:
            query_config = None

        if args.attributes:
            if args.attributes in ['+', '*']:
                attributes = attributes
            else:
                attributes = [a.strip() for a in args.attributes.split(',')]
                attributes += [a for a in MINIMUM_ATTRIBUTES if a.lower() not in [b.lower() for b in attributes]]
        else:
            attributes = ldap3.ALL_ATTRIBUTES

        if args.bh_attributes:
            logger.debug('Collecting only BH compatible attributes...')

        client_cert = None 
        client_key = None

        if args.pem_client_cert:
            if args.pkcs12_client_cert:
                raise Exception('Cannot use PEM client certificates and pkcs12 certificates for the same operation')
            if args.pem_client_key:
                client_cert = args.pem_client_cert
                client_key = args.pem_client_key
            else:
                raise Exception('Cannot use a client PEM certificate without a key')

        if args.pkcs12_client_cert:
            client_cert_file,  client_key_file = create_temporary_cert_files(args.pkcs12_client_cert)
            client_cert = client_cert_file.name 
            client_key = client_key_file.name
            logger.info(f'Writing temporary PEM certificate and key files from PKCS12 conversion to {client_cert} and {client_key}')


            
        dumper = AdDumper(args.domain_controller, target_ip=args.target_ip, username=args.username, password=password, ssl=args.ssl, port=args.port, delay=args.sleep, 
                          jitter=args.jitter, paged_size=args.pagesize, logger=logger, raw=raw, kerberos=args.kerberos, no_password=args.no_password, query_config=query_config,
                          attributes=attributes, bh_attributes=args.bh_attributes, sslprotocol=args.ssl_protocol, start_tls=args.start_tls, client_cert_file=client_cert, client_key_file=client_key)
        outputfile = args.output if args.output else '{}_{}_AD_Dump.json'.format(dumper.generate_timestamp(), args.domain_controller)
        valid_methods = dumper.get_valid_methods()
        
        if args.methods:
            requested_methods = [a.strip() for a in args.methods.split(',')]    
            invalid_methods = [a for a in requested_methods if a not in valid_methods]
            if invalid_methods:
                print('Invalid methods were requested! The invalid methods requested were: {}'.format(', '.join(invalid_methods)))
                print('Valid methods are: {}'.format(', '.join(valid_methods)))
                sys.exit(1)
        else:
            requested_methods = valid_methods


        dumper.connect()
        if args.custom_query:
            data = dumper.run_custom_query(args.custom_query, attributes=attributes)
        else:
            data = dumper.query(methods=requested_methods, only_schema=args.only_schema, no_schema=args.no_schema)
        if 'meta' in data:
            data['meta']['launch_arguments'] = " ".join(sys.argv[:]) # this is imperfect in terms of quoting, but good enough
            if query_config:
                data['meta']['query_config'] = query_config
        open(outputfile, 'w').write(json.dumps(data, indent=4))
        logger.info('Wrote output to {}'.format(outputfile))

    if args.bh_output:
        fn = args.output if args.output else ''
        dumper.bloodhound_convert(data, fn.split('.')[0])

    if k_temp_file:
        os.remove(k_temp_file)
    
        

            
if __name__ == "__main__":
    # execute only if run as a script, helpful if script needs to be debugged
    
    if not check_ipython():
        command_line()

