import http.cookiejar
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request


class APIError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self, url, verify_tls=True, timeout=30):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("URL deve usar http(s), sem credenciais, query ou fragmento")
        self.url = url.rstrip("/")
        self.timeout = timeout
        context = ssl.create_default_context()
        if not verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=context))

    def request(self, path, *, query=None, body=None, form=None, headers=None):
        url = self.url + "/" + path.lstrip("/")
        if query:
            url += "?" + urllib.parse.urlencode(query)
        hdr = {"Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdr["Content-Type"] = "application/json"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            with self.opener.open(urllib.request.Request(url, data=data, headers=hdr), timeout=self.timeout) as res:
                result = json.load(res)
        except urllib.error.HTTPError as exc:
            # Never include request URLs, credentials, or response bodies in errors.
            code = exc.code
            exc.close()
            raise APIError(f"HTTP {code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise APIError("Falha de conexão/TLS ou resposta JSON inválida") from None
        if not isinstance(result, dict):
            raise APIError("Resposta inesperada: esperado objeto JSON")
        return result


class Omada:
    def __init__(self, http, username, password):
        self.http, self.username, self.password = http, username, password
        self.controller = self.token = ""
        self.version = (0,)

    def call(self, path, **kwargs):
        headers = {"Csrf-Token": self.token, **kwargs.pop("headers", {})}
        response = self.http.request(path, headers=headers, **kwargs)
        if response.get("errorCode") != 0 or "result" not in response:
            raise APIError(f"Omada recusou a operação (código {response.get('errorCode')})")
        return response["result"]

    def login(self):
        import re
        info = self.call("api/info")
        self.controller = urllib.parse.quote(info["omadacId"], safe="")
        self.version = tuple(int(n) for n in re.findall(r"\d+", info["controllerVer"])[:3])
        self.token = self.call(f"{self.controller}/api/v2/login", body={
            "username": self.username, "password": self.password})["token"]

    def pages(self, path, modern=False, query=None):
        rows = []
        for page in range(1, 10001):
            if modern:
                result = self.call(path, body={"filters": {"active": True}, "sorts": {},
                    "hideHealthUnsupported": True, "scope": 1, "page": page, "pageSize": 500},
                    headers={"Omada-Request-Source": "web-local"})
            else:
                result = self.call(path, query={"currentPage": page, "currentPageSize": 500, **(query or {})})
            if isinstance(result, list):
                if page != 1:
                    raise APIError("Formato de paginação mudou")
                return result
            batch = result["data"]
            total = result["totalRows"]
            if not isinstance(batch, list) or not isinstance(total, int) or total < 0:
                raise APIError("Paginação Omada inválida")
            if page > 1 and result.get("currentPage", page) != page:
                raise APIError("Omada não avançou a página")
            rows.extend(batch)
            if len(rows) == total:
                return rows
            if not batch or len(rows) > total:
                raise APIError("Inventário Omada incompleto ou alterado durante paginação")
        raise APIError("Limite de paginação excedido")

    def snapshot(self, sites, include_devices, include_reservations):
        # A new session each cycle also recovers from session expiration on the next cycle.
        self.login()
        user = self.call(f"{self.controller}/api/v2/users/current")
        available = {s["name"]: s["key"] for s in user["privilege"]["sites"]}
        snapshots = []
        for site in sites:
            if site["name"] not in available:
                raise APIError(f"Site não encontrado: {site['name']}")
            key = urllib.parse.quote(available[site["name"]], safe="")
            base = f"{self.controller}/api/v2/sites/{key}"
            networks = site.get("networks")
            if networks is None:
                networks = [{"cidr": n["gatewaySubnet"], "domain": n["domain"]}
                    for n in self.pages(base + "/setting/lan/networks")
                    if "interface" in n.get("purpose", "") and n.get("domain")]
            if self.version >= (6, 2, 0):
                clients = self.pages(f"openapi/v2/{self.controller}/sites/{key}/clients", modern=True)
            else:
                clients = self.pages(base + "/clients", query={"filters.active": "true"})
            devices = self.pages(base + "/devices") if include_devices else []
            reservations = self.pages(base + "/setting/service/dhcp") if include_reservations else []
            snapshots.append((networks, clients, devices, reservations))
        return snapshots


class Technitium:
    def __init__(self, http, token):
        self.http, self.token = http, token

    def call(self, operation, **params):
        # Form body keeps the token out of URLs and supports older API versions too.
        result = self.http.request("api/zones/" + operation, form={"token": self.token, **params})
        if result.get("status") != "ok":
            raise APIError("Technitium recusou a operação " + operation)
        return result.get("response", {})

    def zones(self):
        return self.call("list")["zones"]

    def records(self, zone):
        return self.call("records/get", zone=zone, domain=zone, listZone="true")["records"]
