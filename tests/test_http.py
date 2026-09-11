from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from urllib.parse import parse_qs

from omada_technitium.api import APIError, HTTP, Omada, Technitium


class HTTPTests(unittest.TestCase):
    def test_real_http_cookies_csrf_form_body_and_redirect_block(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_api()

            def do_POST(self):
                self.handle_api()

            def handle_api(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requests.append((self.path, self.headers, body))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/steal-token")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if self.path.endswith("login"):
                    self.send_header("Set-Cookie", "session=example; Path=/")
                    result = {"token": "csrf"}
                elif self.path == "/api/info":
                    result = {"omadacId": "ctrl", "controllerVer": "5.15.0"}
                else:
                    result = {"data": [], "totalRows": 0}
                self.end_headers()
                response = {"status": "ok", "response": {"zones": []}} if self.path.startswith("/api/zones") else {"errorCode": 0, "result": result}
                self.wfile.write(json.dumps(response).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            http = HTTP(f"http://127.0.0.1:{server.server_port}")
            omada = Omada(http, "user", "password")
            omada.login()
            omada.pages("ctrl/api/v2/sites/site/clients")
            self.assertEqual(requests[-1][1]["Cookie"], "session=example")
            self.assertEqual(requests[-1][1]["Csrf-Token"], "csrf")
            self.assertEqual(Technitium(http, "secret&token").zones(), [])
            self.assertEqual(parse_qs(requests[-1][2].decode())["token"], ["secret&token"])
            self.assertNotIn("secret", requests[-1][0])
            with self.assertRaisesRegex(APIError, "HTTP 302"):
                http.request("redirect", headers={"Authorization": "Bearer secret"})
            self.assertFalse(any(p == "/steal-token" for p, _, _ in requests))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
