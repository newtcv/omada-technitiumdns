import copy
import unittest

from omada_technitium.api import APIError, Omada, Technitium
from omada_technitium.__main__ import cycle, load_config
from omada_technitium.sync import Record, apply, desired_records, plan

OWNER = "omada-technitium:test"
R = Record("home.arpa", "pc.home.arpa", "A", "192.168.1.10")


class DNS:
    def __init__(self, records=()):
        self.data = list(records)
        self.calls = []

    def zones(self):
        return [{"name": "home.arpa", "type": "Primary"}]

    def records(self, zone):
        return self.data

    def call(self, op, **params):
        self.calls.append((op, params))


def item(r=R, owner=OWNER, ttl=300):
    return {"name": r.name, "type": r.type, "ttl": ttl, "comments": owner,
            "rData": {"ipAddress": r.value}}


def planning(api, desired, **kwargs):
    defaults = dict(owner=OWNER, ttl=300, create_zones=True, prune=True,
                    stale_seconds=60, missing={}, now=100)
    return plan(api, desired, **(defaults | kwargs))


class RecordTests(unittest.TestCase):
    def snapshot(self, clients=(), devices=(), reservations=()):
        return [([{"cidr": "192.168.1.0/24", "domain": "home.arpa"},
                  {"cidr": "fd00::/64", "domain": "home.arpa"}], clients, devices, reservations)]

    def test_names_ipv6_ptr_and_reserved_priority(self):
        rows = self.snapshot(
            [{"name": "old", "mac": "aa:bb", "ip": "192.168.1.20"}],
            [{"name": "Câmera Sala", "mac": "cc:dd", "ip": "192.168.1.30", "ipv6List": ["fd00::3", "fe80::3"]}],
            [{"clientName": "aa-bb", "mac": "aa-bb", "description": "PC", "ip": R.value, "status": True}])
        records = desired_records(rows)
        self.assertIn(R, records)
        self.assertIn(Record("home.arpa", "camera-sala.home.arpa", "AAAA", "fd00::3"), records)
        self.assertEqual(len(records), 6)
        self.assertFalse(any("old" in r.name for r in records))

    def test_collision_aborts(self):
        with self.assertRaisesRegex(ValueError, "Colisão"):
            desired_records(self.snapshot(clients=[
                {"name": "PC", "mac": "aa", "ip": R.value},
                {"name": "pc", "mac": "bb", "ip": "192.168.1.11"}]))

    def test_site_networks_do_not_leak(self):
        snapshots = self.snapshot(clients=[{"name": "pc", "ip": R.value}])
        snapshots += [([{"cidr": "192.168.1.0/24", "domain": "branch.arpa"}], [], [], [])]
        self.assertEqual(desired_records(snapshots, False), {R})

    def test_wildcards_have_no_ptr(self):
        records = desired_records(self.snapshot(reservations=[{
            "clientName": "*.apps", "ip": R.value, "status": True}]))
        self.assertEqual({r.name for r in records}, {"*.apps.home.arpa"})

    def test_bad_and_out_of_scope_addresses_are_skipped(self):
        records = desired_records(self.snapshot(clients=[
            {"name": "x", "ip": "bad"}, {"name": "x", "ip": "10.0.0.1"},
            {"name": "x", "ip": R.value, "active": False}]))
        self.assertEqual(records, set())


class ReconcileTests(unittest.TestCase):
    def test_idempotent(self):
        self.assertEqual(planning(DNS([item()]), {R})[0], [])

    def test_manual_same_value_not_adopted(self):
        dns = DNS([item(owner="manual")])
        with self.assertRaisesRegex(ValueError, "preservado"):
            planning(dns, {R})
        self.assertEqual(dns.calls, [])

    def test_manual_records_never_deleted(self):
        self.assertEqual(planning(DNS([item(owner="manual")]), set())[0], [])

    def test_stale_grace_and_restart(self):
        dns = DNS([item()])
        actions, missing = planning(dns, set())
        self.assertEqual(actions, [])
        actions, _ = planning(dns, set(), missing=copy.deepcopy(missing), now=161)
        self.assertEqual(actions, [("records/delete", R.params())])

    def test_prune_disabled(self):
        actions, _ = planning(DNS([item()]), set(), prune=False, missing={R.key: 0})
        self.assertEqual(actions, [])

    def test_returning_client_clears_missing(self):
        actions, missing = planning(DNS([item()]), {R}, missing={R.key: 0})
        self.assertEqual((actions, missing), ([], {}))

    def test_ttl_update_does_not_overwrite_rrset(self):
        actions, _ = planning(DNS([item(ttl=60)]), {R})
        self.assertEqual(actions[0][0], "records/update")
        self.assertNotIn("overwrite", actions[0][1])

    def test_dry_run_never_calls_mutations(self):
        dns = DNS()
        actions, _ = planning(dns, {R})
        apply(dns, actions, True)
        self.assertEqual(dns.calls, [])

    def test_cname_and_delegation_conflicts(self):
        for typ, name in [("CNAME", R.name), ("NS", "sub.home.arpa"), ("DNAME", "sub.home.arpa")]:
            r = R if typ == "CNAME" else Record(R.zone, "pc.sub.home.arpa", "A", R.value)
            with self.assertRaises(ValueError):
                planning(DNS([{"name": name, "type": typ}]), {r})

    def test_read_failure_prevents_all_writes(self):
        class Broken(DNS):
            def records(self, zone):
                raise APIError("offline")
        dns = Broken()
        with self.assertRaises(APIError):
            planning(dns, {R})
        self.assertEqual(dns.calls, [])

    def test_add_failure_stops_before_deletion(self):
        class Broken(DNS):
            def call(self, operation, **params):
                super().call(operation, **params)
                raise APIError("write failed")
        dns = Broken([item()])
        new = Record(R.zone, "new.home.arpa", R.type, "192.168.1.11")
        actions, _ = planning(dns, {new}, missing={R.key: 0})
        with self.assertRaises(APIError):
            apply(dns, actions)
        self.assertEqual(len(dns.calls), 1)
        self.assertEqual(dns.calls[0][0], "records/add")

    def test_dhcp_change_updates_owned_value_without_prune(self):
        new = Record(R.zone, R.name, R.type, "192.168.1.11")
        actions, missing = planning(DNS([item()]), {new}, prune=False)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0][0], "records/update")
        self.assertEqual(actions[0][1]["ipAddress"], R.value)
        self.assertEqual(actions[0][1]["newIpAddress"], new.value)
        self.assertEqual(missing, {})

    def test_existing_reverse_zone_selected(self):
        dns = DNS()
        dns.zones = lambda: [{"name": "168.192.in-addr.arpa", "type": "Primary"}]
        r = Record("1.168.192.in-addr.arpa", "10.1.168.192.in-addr.arpa", "PTR", R.name)
        actions, _ = planning(dns, {r})
        self.assertEqual(actions[0][1]["zone"], "168.192.in-addr.arpa")
        self.assertEqual(actions[0][0], "records/add")


class OmadaTests(unittest.TestCase):
    def test_full_cycle_both_versions(self):
        for version in ("5.15.20", "6.20.0"):
            calls = []
            class Transport:
                def request(self, path, **kwargs):
                    calls.append((path, kwargs))
                    if path == "api/info":
                        result = {"omadacId": "ctrl", "controllerVer": version}
                    elif path.endswith("login"):
                        result = {"token": "csrf"}
                    elif path.endswith("users/current"):
                        result = {"privilege": {"sites": [{"name": "Default", "key": "site"}]}}
                    elif path.endswith("clients"):
                        modern = path.startswith("openapi/")
                        page = kwargs["body"]["page"] if modern else kwargs["query"]["currentPage"]
                        result = {"totalRows": 2, "currentPage": page, "data": [{
                            "name": "pc" + str(page), "mac": "aa0" + str(page), "ip": "192.168.1." + str(page)}]}
                    else:
                        raise AssertionError(path)
                    return {"errorCode": 0, "result": result}
            config = load_config("config.example.json")
            config.update(include_devices=False, include_reservations=False, reverse=False)
            dns = DNS()
            cycle(config, Omada(Transport(), "u", "p"), dns, {})
            self.assertEqual(len(dns.calls), 2)
            client_calls = [(p, k) for p, k in calls if p.endswith("clients")]
            self.assertEqual(len(client_calls), 2)
            self.assertEqual(client_calls[0][0].startswith("openapi/"), version.startswith("6"))
            self.assertEqual(client_calls[0][1]["headers"]["Csrf-Token"], "csrf")

    def test_incomplete_pagination_fails_closed(self):
        class Transport:
            def request(self, *a, **kw):
                return {"errorCode": 0, "result": {"totalRows": 1, "data": []}}
        with self.assertRaises(APIError):
            Omada(Transport(), "u", "p").pages("clients")

    def test_technitium_parameters_and_error(self):
        calls = []
        class Transport:
            def request(self, path, **kw):
                calls.append((path, kw))
                return {"status": "error", "errorMessage": "sensitive server detail"}
        with self.assertRaises(APIError) as error:
            Technitium(Transport(), "SECRET").call("records/delete", **R.params())
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(calls[0][1]["form"]["ipAddress"], R.value)
        self.assertNotIn("SECRET", calls[0][0])


if __name__ == "__main__":
    unittest.main()
