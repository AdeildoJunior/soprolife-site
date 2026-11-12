#!/usr/bin/env python3
"""Regressão de topPages: fixtures sintéticas, sem rede/credencial/dado real."""
import argparse
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import pii_guard

spec = importlib.util.spec_from_file_location('marketing_pages', SCRIPTS / 'read-marketing-seo-adc.py')
mkt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mkt)
PROPERTY = 'https://soprolife.com.br/'
CFG = {'searchConsoleSiteUrl': PROPERTY, 'ga4PropertyId': '000', 'timezone': 'America/Sao_Paulo'}
RULES = {**mkt._PII_RULES, 'propriedade_search_console': PROPERTY}
LONG = 'espirometria-ocupacional-no-rio-de-janeiro-2026'
TOKEN = 'Ab9CdEfGhIjKlMnOpQrStUvWxYz0123456789ABCD'


def pages(value):
    return {'searchConsole': {'topPages': [{'page': value}]}}


def realistic():
    sc = {'totals': {'impressions': 900, 'clicks': 30, 'ctr': .0333, 'avgPosition': 8.1},
          'request': {'siteUrl': PROPERTY},
          'topQueries': [{'query': 'espirometria rio', 'impressions': 10}],
          'topPages': [{'page': PROPERTY + LONG + f'-{i}/', 'impressions': i + 1,
                        'clicks': 1, 'ctr': .01, 'avgPosition': 8.1} for i in range(25)]}
    return mkt._montar_snapshot(CFG, None, {
        'searchConsole': {'ok': True, 'data': sc},
        'ga4': {'ok': True, 'data': {'totals': {'users': 50, 'sessions': 60, 'pageviews': 70}}},
    }, ('2026-09-11', '2026-11-09', 60), '2026-11-11T02:40:00+00:00', 24, mkt.CRED_SERVICE_ACCOUNT)


class PublicPagesTests(unittest.TestCase):
    def check_blocked(self, value):
        self.assertTrue(pii_guard.validate_summary(pages(value), RULES))

    def test_A_public_slug_with_digits(self):
        url = PROPERTY + LONG + '/'
        self.assertTrue(pii_guard.validate_summary(pages(url), mkt._PII_RULES))
        self.assertEqual(pii_guard.validate_summary(pages(url), RULES), [])

    def test_B_all_indices(self):
        payload = {'searchConsole': {'topPages': [{'page': PROPERTY + LONG + f'-{i}/'} for i in range(40)]}}
        self.assertEqual(pii_guard.validate_summary(payload, RULES), [])

    def test_C_other_positions_unchanged(self):
        url = PROPERTY + LONG + '/'
        for payload in ({'other': url}, {'page': url}, {'ga4': {'topPages': [{'page': url}]}},
                        {'searchConsole': {'other': [{'page': url}]}},
                        {'nested': pages(url)}, {'searchConsole.topPages[0].page': url},
                        {'searchConsole': {'topPages': {'0': {'page': url}}}}):
            with self.subTest(payload=list(payload)):
                self.assertTrue(pii_guard.validate_summary(payload, RULES))
                self.assertEqual(pii_guard.validate_summary(payload, RULES),
                                 pii_guard.validate_summary(payload, mkt._PII_RULES))

    def test_D_pure_token(self):
        self.check_blocked(TOKEN)
        self.check_blocked(PROPERTY + TOKEN)
        self.check_blocked(PROPERTY + '12345678-abcd-1234-abcd-123456789abc')
        self.assertTrue(pii_guard.validate_summary({'other': TOKEN}, RULES))

    def test_E_unauthorized_domain(self):
        for url in ('https://example.invalid/', 'https://soprolife.com.br.example.invalid/',
                    'https://www.soprolife.com.br/', 'http://soprolife.com.br/',
                    'https://soprolife.com.br:444/'):
            with self.subTest(url=url): self.check_blocked(url)

    def test_F_userinfo(self):
        for prefix in ('user:password@', 'user@', '@', 'user%40example.invalid@'):
            self.check_blocked('https://' + prefix + 'soprolife.com.br/')

    def test_G_invalid_urls_and_types(self):
        for value in ('', 'not-a-url', '/espirometria/', '//soprolife.com.br/',
                      'ftp://soprolife.com.br/', PROPERTY + '\n', PROPERTY + '%zz',
                      PROPERTY + '%FF', PROPERTY + 'a/../b', PROPERTY + '\\evil',
                      PROPERTY + '%250a', None, 123, [], {}):
            with self.subTest(value=value): self.check_blocked(value)

    def test_H_remaining_detectors_including_encoded(self):
        for value in ('(21) 98888-7777', '123.456.789-09', '12.345.678-X',
                      'synthetic@example.invalid', 'bearer abc', 'AIza' + 'x' * 15,
                      'ya29.synthetic', 'script.google.com', 'docs.google.com', 'chave pix'):
            for path in (value, quote(value, safe='')):
                with self.subTest(kind=value.split()[0], encoded=path != value):
                    self.check_blocked(PROPERTY + path)
        self.assertTrue(pii_guard.validate_summary({'cpf': 'synthetic'}, RULES))
        self.assertTrue(pii_guard.validate_summary({'other': 'pedido medico'}, RULES))
        self.assertTrue(pii_guard.validate_summary({'other': 'Fulano Sintetico'}, RULES))

    def test_property_must_come_from_rules_not_payload(self):
        p = pages(PROPERTY + LONG)
        p['searchConsole']['request'] = {'siteUrl': PROPERTY}
        self.assertTrue(pii_guard.validate_summary(p, {**RULES, 'propriedade_search_console': None}))
        self.assertTrue(pii_guard.validate_summary(p, {**RULES, 'propriedade_search_console': 'https://example.invalid/'}))

    def test_property_path_boundary(self):
        rules = {**RULES, 'propriedade_search_console': PROPERTY + 'public'}
        self.assertEqual(pii_guard.validate_summary(pages(PROPERTY + 'public/' + LONG), rules), [])
        for path in ('public-other/', 'public/../private', 'public/%2e%2e/private'):
            self.assertTrue(pii_guard.validate_summary(pages(PROPERTY + path), rules))

    def test_query_fragment_rejected_until_normalized(self):
        for suffix in ('?click=' + TOKEN, '#fragment', '?synthetic=123.456.789-09#fragment'):
            self.check_blocked(PROPERTY + LONG + suffix)
            self.assertEqual(pii_guard.public_search_console_page(PROPERTY + LONG + suffix, PROPERTY,
                                                                  normalize=True), PROPERTY + LONG)

    def test_I_realistic_write_25_pages_normalizes_without_changing_metrics(self):
        p = realistic()
        # Mesma estrutura observada: 13 variantes longas de query + fragmento.
        for row in p['searchConsole']['topPages'][11:24]:
            row['page'] += '?tracking=' + TOKEN + '#fragment'
        original = copy.deepcopy(p)
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            out = Path(tmp) / 'public.json'
            private = Path(tmp) / 'private.json'
            with patch.object(mkt, '_OUT_PUBLIC', out), patch.object(mkt, '_OUT_PRIVATE', private):
                mkt._write_output(p, out_public=out, cfg=CFG)
            actual = json.loads(out.read_text())
            self.assertEqual(actual, json.loads(private.read_text()))
            self.assertEqual(len(actual['searchConsole']['topPages']), 25)
            self.assertEqual(actual['ga4'], p['ga4'])
            self.assertEqual(actual['searchConsole']['totals'], p['searchConsole']['totals'])
            for a, b in zip(actual['searchConsole']['topPages'], p['searchConsole']['topPages']):
                self.assertNotIn('?', a['page']); self.assertNotIn('#', a['page'])
                self.assertEqual({k: v for k, v in a.items() if k != 'page'},
                                 {k: v for k, v in b.items() if k != 'page'})
            self.assertEqual(p, original)
            self.assertEqual(pii_guard.validate_summary(actual, RULES), [])

    def test_J_PII_failure_preserves_both_snapshots_and_redacts_logs(self):
        for bad in (PROPERTY + '123.456.789-09', PROPERTY + '%73ynthetic%40example.invalid',
                    'https://user:password@soprolife.com.br/?tracking=' + TOKEN,
                    'https://example.invalid/?tracking=' + TOKEN, TOKEN, None):
            with self.subTest(kind=type(bad).__name__), tempfile.TemporaryDirectory() as tmp:
                out, private = Path(tmp) / 'public.json', Path(tmp) / 'private.json'
                before = b'{"lastKnownGood":true}\n'
                out.write_bytes(before); private.write_bytes(before)
                p = realistic(); p['searchConsole']['topPages'][17]['page'] = bad
                log = io.StringIO()
                with patch.object(mkt, '_OUT_PUBLIC', out), patch.object(mkt, '_OUT_PRIVATE', private), redirect_stdout(log):
                    with self.assertRaises(SystemExit): mkt._write_output(p, out_public=out, cfg=CFG)
                self.assertEqual(out.read_bytes(), before); self.assertEqual(private.read_bytes(), before)
                if bad: self.assertNotIn(bad, log.getvalue())
                self.assertNotIn(TOKEN, log.getvalue())

    def test_cmd_sync_passes_trusted_config_to_writer(self):
        p = realistic()
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            out = Path(tmp) / 'snapshot.json'
            args = argparse.Namespace(output=str(out), max_age_hours=None, no_network=False)
            with patch.object(mkt, '_load_config', return_value=CFG), \
                 patch.object(mkt, '_load_google_libs', return_value=(object(), object())), \
                 patch.object(mkt, 'resolver_credencial', return_value=(object(), mkt.CRED_SERVICE_ACCOUNT, None)), \
                 patch.object(mkt, '_fetch_search_console', return_value=(p['searchConsole'], [])), \
                 patch.object(mkt, '_fetch_ga4', return_value=(p['ga4'], [])):
                self.assertEqual(mkt.cmd_sync(args, 'write'), 0)
            self.assertEqual(len(json.loads(out.read_text())['searchConsole']['topPages']), 25)


if __name__ == '__main__':
    unittest.main(verbosity=2)
