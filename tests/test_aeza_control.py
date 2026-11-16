from pathlib import Path
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch
from tempfile import TemporaryDirectory
from urllib.request import Request
from app.control import prepare, consume, owner_allowed, reserve_action
from app.aeza import AezaClient
from app.formatting import dashboard_text

class AezaControlTests(TestCase):
    def test_owner(self):
        self.assertTrue(owner_allowed(1111111111,1111111111))
        self.assertFalse(owner_allowed(2222222222,1111111111))
        self.assertFalse(owner_allowed(None,1111111111))
    def test_confirm_once_expiration(self):
        pending={}; token=prepare(pending, 1, '1234567', now=100)
        self.assertFalse(consume(pending, 1, '1234567', 'bad', now=110))
        token=prepare(pending,1,'1234567',now=100)
        self.assertTrue(consume(pending, 1, '1234567', token, now=140))
        self.assertFalse(consume(pending,1,'1234567',token,now=141))
        token=prepare(pending,1,'1234567',now=100)
        self.assertFalse(consume(pending,1,'1234567',token,now=192))
    def test_rate_persistent(self):
        with TemporaryDirectory() as d:
            path=Path(d)/'state.json'
            self.assertTrue(reserve_action(path,'1234567',now=100000))
            self.assertFalse(reserve_action(path,'1234567',now=100001))
            self.assertTrue(reserve_action(path,'1234567',now=101801))
    def test_restart_validation(self):
        client=AezaClient('secret',service_id='1234567')
        with self.assertRaises(ValueError): client.restart_once('bad')
        with self.assertRaises(ValueError): client.restart_once('12')
        client2=AezaClient('secret',base_url='https://my.aeza.net/api',service_id='1234567')
        with self.assertRaises(ValueError): client2.restart_once('1234567')
    def test_restart_one_post(self):
        client=AezaClient('secret',service_id='1234567')
        calls=[]
        class FakeReply:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'{}'
        class FakeOpener:
            def open(self,req,timeout):
                calls.append((req.full_url,req.data,req.get_method()))
                return FakeReply()
        with patch('urllib.request.build_opener',return_value=FakeOpener()):
            # build_opener imported locally in function
            client.restart_once('1234567')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2],'POST')
        self.assertIn('/services/1234567/ctl/restart',calls[0][0])
        self.assertIn(b'false',calls[0][1])
