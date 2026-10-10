import sys
import unittest
import tempfile
from unittest.mock import patch, Mock
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_batch import Problem, article_doi, validate_url, public_ip, verify_metadata, validate_pdf_bytes
import paper_batch as batch


class ResolverTests(unittest.TestCase):
    def test_main_link_only(self):
        page = '''<script>10.1038/s41592-026-03196-6</script>
        <div id="js_content"><section>文章链接：</section><p><a href="https://www.nature.com/articles/s41566-026-02019-6">原论文</a></p>
        <section>相关推荐：</section><a href="https://www.nature.com/articles/s41592-026-03196-6">推荐</a></div>'''
        self.assertEqual(article_doi(page), '10.1038/s41566-026-02019-6')

    def test_plain_link_and_nested_div(self):
        self.assertEqual(article_doi('<div id="js_content"><div>标题</div><p>文章链接：</p><p>https://www.nature.com/articles/s41566-026-02019-6</p></div>'), '10.1038/s41566-026-02019-6')

    def test_ambiguous(self):
        with self.assertRaises(Problem):
            article_doi('<div id="js_content">文章链接： https://www.nature.com/articles/s41566-026-02019-6 https://www.nature.com/articles/s41592-026-03196-6</div>')

    def test_no_main_marker(self):
        with self.assertRaises(Problem):
            article_doi('<div id="js_content">参考文献 https://www.nature.com/articles/s41592-026-03196-6</div>')

    def test_reference_inside_main_section_rejected(self):
        with self.assertRaises(Problem):
            article_doi('<div id="js_content">文章链接： https://www.nature.com/articles/s41566-026-02019-6 参考文献： https://www.nature.com/articles/s41592-026-03196-6</div>')

    def test_no_body(self):
        with self.assertRaises(Problem): article_doi('<script>文章链接： https://www.nature.com/articles/s41592-026-03196-6</script>')

    def test_safe_urls(self):
        for url in ('https://www.nature.com/articles/s41592-026-03196-6.pdf', 'https://api.crossref.org/works/10.1038%2Fs41592-026-03196-6', 'https://mp.weixin.qq.com/s/gSh9FTL3moTyxnhTmuH3Lw'):
            validate_url(url)

    def test_bad_urls(self):
        for url in ('http://www.nature.com/articles/x', 'https://127.0.0.1/x', 'https://www.nature.com.evil.test/x', 'https://u:p@www.nature.com/x', 'https://www.nature.com:444/x', 'https://www.nature.com/articles/../x', 'https://www.nature.com/authorize', 'https://www.nature.com/articles/x#y'):
            with self.subTest(url=url), self.assertRaises(Problem): validate_url(url)

    def test_public_ip(self):
        self.assertTrue(public_ip('1.1.1.1'))
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1', '::ffff:127.0.0.1', '192.0.2.1'):
            self.assertFalse(public_ip(address))

    def test_metadata(self):
        value = {'DOI':'10.1038/s41592-026-03196-6', 'type':'journal-article', 'title':['Test paper'], 'published':{'date-parts':[[2026,8,31]]}}
        self.assertEqual(verify_metadata(value, value['DOI'])['year'], 2026)
        for bad in (dict(value, DOI='10.1038/wrong'), dict(value, title=[]), dict(value, type='book')):
            with self.assertRaises(Problem): verify_metadata(bad, value['DOI'])

    def test_pdf_envelope(self):
        validate_pdf_bytes(b'%PDF-1.7\n' + b'X'*200 + b'\n%%EOF')
        for data in (b'<html>login</html>', b'%PDF-1.7\n'+b'X'*200, b'%PDF-1.7\n%%EOF'):
            with self.assertRaises(Problem): validate_pdf_bytes(data)

    def test_license_requires_vor(self):
        entry = {'content-version':'vor','URL':'https://creativecommons.org/licenses/by/4.0','start':{'timestamp':0}}
        self.assertEqual(batch.open_license({'license':[entry]}), entry['URL'])
        for value in ({'license':[dict(entry, **{'content-version':'tdm'})]}, {'license':[]}, {'license':[dict(entry, start={'timestamp':99999999999999})]}):
            with self.assertRaises(Problem): batch.open_license(value)

    def test_repository_requires_identity(self):
        page = '<meta name="citation_doi" content="10.1038/s41592-026-03196-6"><meta name="citation_title" content="Test"><meta name="citation_pdf_url" content="https://infoscience.epfl.ch/bitstreams/7b2daaf8-1b40-4304-a781-cf2a7f469292/download">'
        self.assertIn('/bitstreams/', batch.repository_pdf(page, '10.1038/s41592-026-03196-6', 'Test'))
        with self.assertRaises(Problem): batch.repository_pdf(page, '10.1038/s41566-026-02019-6', 'Test')
        with self.assertRaises(Problem): batch.repository_pdf(page, '10.1038/s41592-026-03196-6', 'Wrong')

    def test_fetch_checks_redirect_and_closes(self):
        response = Mock(status=302)
        response.getheader.return_value = 'https://127.0.0.1/private'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(batch, 'PinnedHTTPS', return_value=connection), self.assertRaises(Problem):
            batch.fetch('https://www.nature.com/articles/s41592-026-03196-6.pdf', 1024)
        connection.close.assert_called_once()

    def test_fetch_size_limit(self):
        response = Mock(status=200)
        response.getheader.side_effect = lambda name, default=None: {'Content-Length':'900','Content-Encoding':'identity'}.get(name,default)
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(batch, 'PinnedHTTPS', return_value=connection), self.assertRaises(Problem):
            batch.fetch('https://www.nature.com/articles/s41592-026-03196-6.pdf', 100)
        connection.close.assert_called_once()

    def test_pinned_connection_private_dns(self):
        with patch.object(batch.socket, 'getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]), patch.object(batch.socket,'create_connection') as connect:
            with self.assertRaises(Problem): batch.PinnedHTTPS('www.nature.com').connect()
            connect.assert_not_called()

    def test_failure_not_marked_downloaded(self):
        with tempfile.TemporaryDirectory(dir=batch.ROOT.parent/'receiver/.tmp') as directory, patch.object(batch,'ROOT',Path(directory)):
            result = batch.process({'id':1,'kind':'doi','value':'10.1038/s41592-026-03196-6'}, network=Mock(side_effect=Problem('blocked')))
            self.assertEqual(result['status'], 'pending')
            self.assertEqual(result['error'], 'blocked')
            self.assertFalse((Path(directory)/'downloads').exists())
            self.assertTrue((Path(directory)/'jobs/1.json').exists())

    def test_non_pdf_does_not_publish(self):
        metadata = {'message':{'DOI':'10.1038/s41592-026-03196-6','type':'journal-article','title':['Test'],'published':{'date-parts':[[2026]]}, 'license':[{'content-version':'vor','URL':'https://creativecommons.org/licenses/by/4.0','start':{'timestamp':0}}]}}
        network = Mock(side_effect=[(json.dumps(metadata).encode(),'application/json',''), (b'login','text/html',''), (b'login','text/html','')])
        with tempfile.TemporaryDirectory(dir=batch.ROOT.parent/'receiver/.tmp') as directory, patch.object(batch,'ROOT',Path(directory)):
            result = batch.process({'id':1,'kind':'doi','value':'10.1038/s41592-026-03196-6'}, network=network)
            self.assertEqual(result['status'], 'resolved')
            self.assertEqual(result['route_errors'], ['not_pdf_mime','not_pdf_mime'])
            self.assertNotIn('pdf_path',result)

    def test_pdf_wrong_identity(self):
        reader = Mock()
        reader.is_encrypted = False
        reader.pages = [Mock()]
        reader.pages[0].extract_text.return_value = 'Another paper 10.1038/s41566-026-02019-6'
        with patch('pypdf.PdfReader',return_value=reader), self.assertRaises(Problem):
            batch.pdf_identity(b'%PDF-1.7\n'+b'x'*200+b'%%EOF','10.1038/s41592-026-03196-6','Test')


if __name__ == '__main__': unittest.main()
