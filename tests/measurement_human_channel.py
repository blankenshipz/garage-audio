"""Actual shared-wildcard HTTP rejection plus precise socket-context unit checks."""
from email.message import Message
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from garage_audio.application import Application
from garage_audio.webserver import Handler, Server
from measurement_jobs import provision

class Channel(unittest.TestCase):
    def test_actual_wildcard_listener_with_accidental_verifier_denies_before_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); verifier,password = provision(root/'human')
            app = Application(root/'state',human_reference=str(verifier))
            server = Server(('0.0.0.0',0),app,'isolated-automation-'+'x'*40,tmp)
            thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                port = server.server_address[1]
                conn = http.client.HTTPConnection('127.0.0.1',port)
                # Matching spoofed loopback Host AND Origin, actual localhost TCP:
                # wildcard bind still denies, regardless collapsed Docker/proxy peer.
                conn.request('POST','/api/human/login',json.dumps({'user':'operator','password':password}),
                    {'Host':f'127.0.0.1:{port}','Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json'})
                response = conn.getresponse(); body = json.loads(response.read())
                self.assertEqual(response.status,403)
                self.assertEqual(body['error']['code'],'confidential_origin_required')
                self.assertFalse(app.execute('capabilities',{})['result']['human_approval_available'])
                conn.close()
            finally: server.shutdown(); server.server_close(); thread.join()
    def test_local_and_peer_are_os_socket_context_not_headers(self):
        # Isolated unit socket seam supplements actual HTTP test; not live LAN proof.
        from types import SimpleNamespace
        for bind,local,peer,allowed in [('127.0.0.1','127.0.0.1','127.0.0.1',True),
                                      ('127.0.0.1','127.0.0.1','192.168.68.99',False),
                                      ('127.0.0.1','192.168.68.74','127.0.0.1',False),
                                      ('0.0.0.0','127.0.0.1','127.0.0.1',False)]:
            handler = object.__new__(Handler)
            handler.server = SimpleNamespace(server_address=(bind,8098),app=SimpleNamespace(human_approval_available=True))
            handler.connection = SimpleNamespace(getsockname=lambda:(local,8098))
            handler.client_address = (peer,12345)
            handler.headers = Message(); handler.headers['Host'] = '127.0.0.1:8098'
            handler.headers['Origin'] = 'http://127.0.0.1:8098'; handler.headers['X-Forwarded-For'] = '127.0.0.1'
            self.assertEqual(handler.human_local(),allowed)

if __name__ == '__main__': unittest.main()
