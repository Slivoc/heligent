"""Disposable local database/server for web/tests/tar1090.browser.cjs."""
import os
from urllib.parse import urlparse


def main():
    dsn = os.environ.get('TEST_DATABASE_URL', '')
    url = urlparse(dsn)
    if url.hostname not in ('localhost', '127.0.0.1') or not url.path.startswith('/heligent_tar1090_test_'):
        raise SystemExit('Use a local TEST_DATABASE_URL with a database named heligent_tar1090_test_*. Its public schema will be replaced.')
    from test_tar1090_integration import Tar1090IntegrationTests
    from adsb_ingest.webapp import create_app
    Tar1090IntegrationTests.setUpClass()
    Tar1090IntegrationTests().setUp()
    create_app(dsn, start_worker=False).run(host='127.0.0.1', port=5089, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()
