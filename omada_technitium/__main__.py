import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time

from .api import APIError, HTTP, Omada, Technitium
from .sync import apply, domain, plan, desired_records


def secret(section, name):
    value = os.environ.get(section[name + "_env"], "")
    if not value:
        raise ValueError("Variável obrigatória não definida: " + section[name + "_env"])
    return value


def load_config(path):
    config = json.loads(Path(path).read_text())
    defaults = {"interval_seconds": 300, "ttl": 300, "stale_seconds": 86400,
                "create_zones": False, "prune": False, "reverse": True,
                "include_devices": True, "include_reservations": True,
                "owner": "omada-technitium:default", "state_file": "data/state.json",
                "name_conflict_policy": "error"}
    allowed = set(defaults) | {"omada", "technitium", "sites"}
    if set(config) - allowed:
        raise ValueError("Opções desconhecidas: " + ", ".join(sorted(set(config) - allowed)))
    config = {**defaults, **config}
    if config["name_conflict_policy"] not in ("error", "mac_suffix"):
        raise ValueError("name_conflict_policy deve ser error ou mac_suffix")
    for key in ("interval_seconds", "ttl", "stale_seconds"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(key + " deve ser inteiro positivo")
    for key in ("create_zones", "prune", "reverse", "include_devices", "include_reservations"):
        if type(config[key]) is not bool:
            raise ValueError(key + " deve ser booleano")
    if not isinstance(config["owner"], str) or not config["owner"].startswith("omada-technitium:") or len(config["owner"]) < 18:
        raise ValueError("owner deve usar o formato omada-technitium:identificador")
    if not isinstance(config["sites"], list) or not config["sites"]:
        raise ValueError("Configure pelo menos um site")
    names = set()
    import ipaddress
    for site in config["sites"]:
        if not site["name"] or site["name"] in names:
            raise ValueError("Nome de site vazio ou duplicado")
        names.add(site["name"])
        if "networks" in site:
            if not site["networks"]:
                raise ValueError("networks deve conter redes ou ser omitido para descoberta")
            for network in site["networks"]:
                ipaddress.ip_network(network["cidr"], strict=False)
                domain(network["domain"])
    for name in ("omada", "technitium"):
        section = config[name]
        if type(section.get("verify_tls", True)) is not bool:
            raise ValueError("verify_tls deve ser booleano")
        timeout = section.get("timeout_seconds", 30)
        if type(timeout) is not int or timeout < 1:
            raise ValueError("timeout_seconds deve ser inteiro positivo")
    return config


def cycle(config, omada, dns, missing, dry_run=False):
    snapshots = omada.snapshot(config["sites"], config["include_devices"], config["include_reservations"])
    desired = desired_records(snapshots, config["reverse"], config.get("name_conflict_policy", "error"))
    actions, next_missing = plan(dns, desired, config["owner"], config["ttl"],
        config["create_zones"], config["prune"], config["stale_seconds"], missing, time.time())
    apply(dns, actions, dry_run)
    logging.info("Ciclo concluído: %d registros desejados, %d operações", len(desired), len(actions))
    return next_missing


def main():
    parser = argparse.ArgumentParser(description="Sincroniza Omada com Technitium DNS")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true", help="executa um ciclo")
    parser.add_argument("--dry-run", action="store_true", help="simula um ciclo sem alterar DNS ou estado")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        config = load_config(args.config)
        o, t = config["omada"], config["technitium"]
        def transport(section):
            return HTTP(section["url"], section.get("verify_tls", True), section.get("timeout_seconds", 30))
        omada = Omada(transport(o), secret(o, "username"), secret(o, "password"))
        dns = Technitium(transport(t), secret(t, "token"))
        path = Path(config["state_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        # A single writer per shared state directory, including preview executions.
        with open(str(path) + ".lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Outra instância usa este arquivo de estado") from None
            missing = {}
            if path.exists():
                state = json.loads(path.read_text())
                if state.get("owner") != config["owner"]:
                    raise ValueError("owner difere do arquivo de estado; use outro state_file")
                missing = state["missing"]
                if not isinstance(missing, dict):
                    raise ValueError("Estado inválido")
            while not stop.is_set():
                try:
                    updated = cycle(config, omada, dns, missing, args.dry_run)
                    if not args.dry_run:
                        temp = path.with_suffix(".tmp")
                        temp.write_text(json.dumps({"owner": config["owner"], "missing": updated}))
                        temp.replace(path)
                        missing = updated
                except (APIError, ValueError, KeyError, TypeError, OSError) as exc:
                    logging.error("Ciclo interrompido (%s): %s", type(exc).__name__, exc)
                    if args.once or args.dry_run:
                        return 1
                if args.once or args.dry_run:
                    return 0
                stop.wait(config["interval_seconds"])
    except (APIError, ValueError, KeyError, TypeError, OSError) as exc:
        logging.error("Falha de configuração/inicialização (%s): %s", type(exc).__name__, exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
