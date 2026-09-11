from dataclasses import dataclass
import ipaddress
import json
import logging
import re
import unicodedata


def domain(value):
    value = value.rstrip(".").lower().encode("idna").decode("ascii")
    if len(value) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in value.split(".")):
        raise ValueError(f"Domínio inválido: {value}")
    return value


def label(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9-]+", "-", value).strip("-")[:63].rstrip("-")


@dataclass(frozen=True, order=True)
class Record:
    zone: str
    name: str
    type: str
    value: str

    @property
    def key(self):
        return json.dumps([self.zone, self.name, self.type, self.value])

    def params(self):
        return {"zone": self.zone, "domain": self.name, "type": self.type,
                "ptrName" if self.type == "PTR" else "ipAddress": self.value}


def desired_records(snapshots, reverse=True):
    records, owners = set(), {}
    for networks, clients, devices, reservations in snapshots:
        nets = sorted([(ipaddress.ip_network(n["cidr"], strict=False), domain(n["domain"]))
                       for n in networks], key=lambda n: -n[0].prefixlen)
        reservations = [r for r in reservations if r.get("status") is True]
        reserved_macs = {re.sub(r"[^a-f0-9]", "", r.get("mac", "").lower()) for r in reservations}
        rows = [(r, "reservation") for r in reservations] + [(r, "device") for r in devices]
        rows += [(r, "client") for r in clients if r.get("active", True) and
                 re.sub(r"[^a-f0-9]", "", r.get("mac", "").lower()) not in reserved_macs]
        for row, kind in rows:
            mac = row.get("mac", "")
            name = row.get("clientName" if kind == "reservation" else "name", "")
            if not name or name == mac or name == "--":
                name = row.get("description" if kind == "reservation" else "hostName", "")
            if not name or name == "--":
                name = "device-" + mac if mac else ""
            wildcard = kind == "reservation" and name.startswith("*.")
            host = label(name[2:] if wildcard else name)
            if not host:
                logging.warning("Ignorando item sem nome utilizável")
                continue
            addresses = [row.get("ip", "")] + (row.get("ipv6List") or [])
            for raw in addresses:
                try:
                    ip = ipaddress.ip_address(raw)
                except ValueError:
                    continue
                if ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_loopback:
                    continue
                matches = [(net, zone) for net, zone in nets if ip in net]
                if not matches:
                    continue
                if len({z for n, z in matches if n.prefixlen == matches[0][0].prefixlen}) > 1:
                    raise ValueError("Redes com mesmo prefixo e domínios conflitantes")
                zone = matches[0][1]
                fqdn = ("*." if wildcard else "") + host + "." + zone
                if len(fqdn) > 253:
                    raise ValueError("Nome DNS excede 253 caracteres")
                identity = re.sub(r"[^a-f0-9]", "", mac.lower()) or str(ip)
                owner = owners.setdefault(fqdn, identity)
                if owner != identity:
                    raise ValueError(f"Colisão de nomes no Omada: {fqdn}; renomeie um dispositivo")
                records.add(Record(zone, fqdn, "A" if ip.version == 4 else "AAAA", str(ip)))
    if reverse:
        # One deterministic canonical PTR when a device has multiple aliases.
        canonical = {}
        for r in sorted(records):
            if not r.name.startswith("*."):
                canonical.setdefault(r.value, r.name)
        for value, name in canonical.items():
            ip = ipaddress.ip_address(value)
            ptr = ip.reverse_pointer
            zone = ".".join(ptr.split(".")[1 if ip.version == 4 else 16:])
            records.add(Record(zone, ptr, "PTR", name))
    return records


def plan(api, desired, owner, ttl, create_zones, prune, stale_seconds, missing, now):
    zones = {z["name"].rstrip(".").lower(): z for z in api.zones()}
    # Prefer existing authoritative reverse zones over generated /24 or /64 zones.
    resolved = set()
    for record in desired:
        if record.type == "PTR":
            matches = [z for z in zones if z and (record.name == z or record.name.endswith("." + z))
                       and not zones[z].get("internal")]
            if matches:
                record = Record(max(matches, key=len), record.name, record.type, record.value)
        resolved.add(record)
    desired = resolved
    needed = {r.zone for r in desired}
    actions = []
    for zone in sorted(needed):
        if zone not in zones:
            if not create_zones:
                raise ValueError(f"Crie a zona Primary '{zone}' ou habilite create_zones")
            actions.append(("create", {"zone": zone, "type": "Primary"}))
        elif zones[zone]["type"] != "Primary" or zones[zone].get("disabled") or zones[zone].get("internal"):
            raise ValueError(f"Zona não editável: {zone}")
    existing, blockers = {}, set()
    # Scan primary zones to recover ownership after restarts, including removed networks.
    for zone, info in sorted(zones.items()):
        if info["type"] != "Primary" or info.get("disabled") or info.get("internal"):
            continue
        for item in api.records(zone):
            name = item["name"].rstrip(".").lower()
            typ = item["type"]
            if typ not in ("A", "AAAA", "PTR"):
                if typ in ("CNAME", "DNAME", "NS") and name != zone:
                    blockers.add((zone, name, "*"))
                continue
            value = item["rData"]["ptrName" if typ == "PTR" else "ipAddress"]
            value = domain(value) if typ == "PTR" else str(ipaddress.ip_address(value))
            record = Record(zone, name, typ, value)
            if item.get("comments") == owner:
                existing[record] = item
            else:
                blockers.add((zone, name, typ))
    for r in sorted(desired):
        authoritative = [z for z in zones if z and (r.name == z or r.name.endswith("." + z))]
        if authoritative and max(authoritative, key=len) != r.zone and r.zone in zones:
            raise ValueError(f"Zona filha intercepta o registro {r.name}; ajuste o mapeamento")
        conflict = (r.zone, r.name, r.type) in blockers or any(
            z == r.zone and t == "*" and (r.name == n or r.name.endswith("." + n)) for z, n, t in blockers)
        if conflict:
            raise ValueError(f"Registro manual/conflitante preservado: {r.name} {r.type}")
    # Replace a single owned value atomically (e.g. DHCP IP change), even with prune off.
    replacements = {}
    for r in sorted(desired - existing.keys()):
        old = [e for e in existing if (e.zone, e.name, e.type) == (r.zone, r.name, r.type)]
        wanted = [e for e in desired if (e.zone, e.name, e.type) == (r.zone, r.name, r.type)]
        if len(old) == len(wanted) == 1 and old[0] not in desired:
            replacements[r] = old[0]
    for r in sorted(desired):
        params = {**r.params(), "ttl": ttl, "comments": owner}
        if r in replacements:
            old = replacements[r]
            params = {**old.params(), "ttl": ttl, "comments": owner, "disable": "false", "ptr": "false",
                      "newPtrName" if r.type == "PTR" else "newIpAddress": r.value}
            actions.append(("records/update", params))
        elif r not in existing:
            actions.append(("records/add", {**params, "overwrite": "false", "ptr": "false"}))
        elif existing[r].get("ttl") != ttl or existing[r].get("disabled"):
            if r.type == "PTR":
                params["newPtrName"] = r.value
            actions.append(("records/update", {**params, "disable": "false", "ptr": "false"}))
    next_missing = {}
    for r in sorted(existing.keys() - desired - set(replacements.values())):
        since = missing.get(r.key, now)
        if not isinstance(since, (float, int)) or since > now:
            since = now
        next_missing[r.key] = since
        if prune and now - since >= stale_seconds:
            actions.append(("records/delete", r.params()))
    return actions, next_missing


def apply(api, actions, dry_run=False):
    for operation, params in actions:
        logging.info("%s%s %s", "SIMULAÇÃO " if dry_run else "", operation, json.dumps(params, ensure_ascii=False))
        if not dry_run:
            api.call(operation, **params)
