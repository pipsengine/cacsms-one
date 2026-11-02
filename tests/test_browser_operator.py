import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

from browser_operator import research_pages


@unittest.skipUnless(
    os.environ.get('SPORTS_RUN_BROWSER_TESTS') == '1',
    'Set SPORTS_RUN_BROWSER_TESTS=1 after installing Playwright Chromium.',
)
class BrowserOperatorIntegrationTests(unittest.TestCase):
    def test_explicitly_permitted_local_site_is_extracted_and_screenshotted(self):
        os.environ['SPORTS_BROWSER_ALLOW_LOCAL_TESTS']='1'

        class PermittedPage(BaseHTTPRequestHandler):
            def do_GET(self):
                body=(
                    b'<html><head><title>Permitted Local Match</title></head>'
                    b'<body><h2 class="score">Home 2 - Away 1</h2></body></html>'
                )
                self.send_response(200)
                self.send_header('Content-Type','text/html')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self,*args):
                return

        server=ThreadingHTTPServer(('127.0.0.1',0),PermittedPage)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/match'
            with tempfile.TemporaryDirectory() as directory:
                pages=research_pages(
                    [url],{'127.0.0.1'},{'statistics':'h2.score'},Path(directory),
                )
                self.assertEqual(pages[0]['title'],'Permitted Local Match')
                self.assertEqual(pages[0]['fields']['statistics'],['Home 2 - Away 1'])
                self.assertTrue(Path(pages[0]['screenshot']).is_file())
        finally:
            server.shutdown()
            server.server_close()
            os.environ.pop('SPORTS_BROWSER_ALLOW_LOCAL_TESTS',None)


if __name__=='__main__':
    unittest.main()
