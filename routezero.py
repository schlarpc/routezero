import collections
import itertools
import os
import re
import runpy
import sys
import tempfile
import urllib.parse

import requests
from troposphere import Template
from troposphere.route53 import RecordSet, RecordSetGroup

NAME_NAMESPACE = "zerotier"
NODE_NAMESPACE = "zerotier-node"


class Zerotier:
    def __init__(self, api_key, *, api_url="https://my.zerotier.com"):
        self._api_url = api_url
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"bearer {api_key}"

    def get(self, url, *args, **kwargs):
        response = self._session.get(
            urllib.parse.urljoin(self._api_url, url), *args, **kwargs
        )
        response.raise_for_status()
        return response.json()

    def get_network(self, network_id):
        network = self.get(f"/api/network/{network_id}")
        network["members"] = self.get(f"/api/network/{network_id}/member")
        return network


def run_as_module(*args):
    original_argv = sys.argv[:]
    sys.argv = list(args)
    try:
        runpy.run_module(args[0], run_name="__main__")
    except SystemExit as ex:
        if ex.code:
            raise
    finally:
        sys.argv = original_argv


def punify_label(label):
    try:
        return label.encode("ascii").decode("ascii")
    except Exception:
        return "xn--" + label.encode("punycode").decode("ascii")


def is_valid_hostname(hostname):
    if len(hostname) > 255:
        return False
    if hostname[-1] == ".":
        hostname = hostname[:-1]
    allowed = re.compile("(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
    return all(allowed.match(x) for x in hostname.split("."))


def get_rfc4193_address(network_id, node_id):
    address = "fd" + network_id + "9993" + node_id
    return ":".join(address[i : i + 4] for i in range(0, len(address), 4))


def dnsjoin(*args):
    labels = itertools.chain.from_iterable(arg.strip(".").split(".") for arg in args)
    return ".".join([punify_label(label) for label in labels])


def create_records(zone_name, network):
    records = collections.defaultdict(dict)
    for namespace in (NAME_NAMESPACE, NODE_NAMESPACE):
        records[dnsjoin(namespace, zone_name)]["TXT"] = ['"' + network["id"] + '"']
    for member in network["members"]:
        if not member["config"]["authorized"] or not member["config"]["ipAssignments"]:
            continue
        node = dnsjoin(member["nodeId"], NODE_NAMESPACE, zone_name)
        name = dnsjoin(member["name"], NAME_NAMESPACE, zone_name)
        ipv4 = [ip for ip in member["config"]["ipAssignments"] if ":" not in ip]
        ipv6 = [ip for ip in member["config"]["ipAssignments"] if ":" in ip]
        if network["config"]["v6AssignMode"]["rfc4193"]:
            ipv6.append(get_rfc4193_address(network["id"], member["nodeId"]))
        records[node]["A"] = ipv4
        records[node]["AAAA"] = ipv6
        if is_valid_hostname(name):
            records[name]["CNAME"] = [node]
    return dict(records)


def create_template(zone_name, records):
    template = Template(Description="Dynamic DNS entries for ZeroTier")
    record_sets = []
    zone_name = zone_name.rstrip(".") + "."
    for name in records:
        for type, values in records[name].items():
            record_sets.append(
                RecordSet(Name=name, Type=type, ResourceRecords=values, TTL=300)
            )
    template.add_resource(
        RecordSetGroup("Records", HostedZoneName=zone_name, RecordSets=record_sets)
    )
    return template


def handler(event, context):
    api_key = os.environ["ZEROTIER_API_KEY"]
    network_id = os.environ["ZEROTIER_NETWORK_ID"]
    network = Zerotier(api_key).get_network(network_id)
    records = create_records(network["config"]["name"], network)
    template = create_template(network["config"]["name"], records)
    print(template.to_json(indent=None))
    with tempfile.TemporaryDirectory() as tempdir:
        template_path = os.path.join(tempdir, "template.json")
        with open(template_path, "w") as f:
            f.write(template.to_json())
        run_as_module(
            "awscli",
            "cloudformation",
            "deploy",
            "--no-fail-on-empty-changeset",
            "--template-file",
            template_path,
            "--stack-name",
            os.environ["ROUTE53_RECORD_STACK_NAME"],
        )


if __name__ == "__main__":
    handler(None, None)
