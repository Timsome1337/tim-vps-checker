"""Smart monitoring: sustained incidents and control audit."""
from __future__ import annotations
import ast
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from app.config import Config, Server
from app.smart_alerts import evaluate_smart
from app.history import HistoryStore, russian_measurements

TODAY=date(2026,11,15)


def configured(expiry=None):
    s = Server('sweden','🇸🇪 VPS',True,'local',None,22,'root','x','Aeza',
               'SWE-PROMO','1,99 €',expiry,('sing-box','x-ui','xray'))
    c = Config((s,),300,90,90,90,(14,7,3,1,0))
    return s,c


def result(cpu=10,ram=20,disk=60,free=4_000_000_000,
           services=None):
    total=10_000_000_000
    return {'ok':True, 'data': {
        'cpu_percent': cpu,
        'memory': {'percent':ram},
        'disk': {'percent':disk,'total':total,'used':total-free},
        'services':services if services is not None else {'xray':'inactive','sing-box':'active'},
    }}


def cycle(s,c,sequence,state=None):
    messages=[]
    state=state or {}
    for data in sequence:
        msg,state = evaluate_smart(s,data,state,c,TODAY)
        messages.append(msg)
    return messages,state


class SmartTests(unittest.TestCase):
    def setUp(self):
        self.s,self.c=configured()

    def test_single_cpu_spike_is_silent(self):
        msgs,_ = cycle(self.s,self.c,[result(cpu=98),result(cpu=5)])
        self.assertFalse(any('CPU' in x for row in msgs for x in row))

    def test_cpu_incident_after_three_samples_and_once(self):
        msgs,state=cycle(self.s,self.c,[result(cpu=96)]*5)
        self.assertFalse(msgs[0]);self.assertFalse(msgs[1])
        self.assertEqual(sum('CPU' in x for row in msgs for x in row),1)
        self.assertTrue(state['smart_alerts']['resources']['cpu_percent']['active'])

    def test_cpu_recovery_requires_two_good_samples(self):
        msgs,_=cycle(self.s,self.c,[result(cpu=95)]*3+[result(cpu=83),result(cpu=82)])
        self.assertFalse(any('восстановился' in x for x in msgs[-2]))
        self.assertTrue(any('восстановился' in x for x in msgs[-1]))

    def test_memory_and_disk_independent(self):
        msgs,_=cycle(self.s,self.c,[result(ram=91,disk=91)]*3)
        self.assertTrue(any('ОЗУ' in x for x in msgs[2]))
        self.assertTrue(any('Диск' in x for x in msgs[2]))

    def test_between_hysteresis_doesnt_resolve(self):
        msgs,_=cycle(self.s,self.c,[result(cpu=92)]*3+[result(cpu=89)]*4)
        self.assertFalse(any('восстановился' in x for row in msgs for x in row))

    def test_no_false_alert_for_baseline_inactive_xray(self):
        msgs,_=cycle(self.s,self.c,[result()]*4)
        self.assertFalse(any('xray' in x for row in msgs for x in row))

    def test_service_requires_two_bad_samples(self):
        normal=result(services={'sing-box':'active'})
        off=result(services={'sing-box':'inactive'})
        msgs,_=cycle(self.s,self.c,[normal,off,off,off])
        self.assertFalse(msgs[1])
        self.assertEqual(sum('sing-box' in x for row in msgs for x in row),1)

    def test_service_recovery_after_two_good(self):
        up=result(services={'sing-box':'active'})
        down=result(services={'sing-box':'failed'})
        msgs,_=cycle(self.s,self.c,[up,down,down,up,up])
        self.assertFalse(msgs[3])
        self.assertTrue(any('снова active' in x for x in msgs[4]))

    def test_service_single_flap_is_silent(self):
        up=result(services={'sing-box':'active'})
        down=result(services={'sing-box':'inactive'})
        msgs,_=cycle(self.s,self.c,[up,down,up,down,up])
        self.assertFalse(any('сервис' in x for row in msgs for x in row))

    def test_not_a_proof_of_vps_power_off(self):
        msg,_=cycle(self.s,self.c,[{'ok':False},{'ok':False}])
        self.assertIn('проверка не удалась', ' '.join(msg[-1]))
        self.assertNotIn('выключился',' '.join(msg[-1]))

    def test_connection_recovery_after_confirmed_failure(self):
        msg,_=cycle(self.s,self.c,[{'ok':False},{'ok':False},result()])
        self.assertEqual(sum('восстановлены' in x for row in msg for x in row),1)

    def test_disk_free_persistent_and_recovery(self):
        lo=result(free=750_000_000)
        hi=result(free=2_000_000_000)
        msgs,_=cycle(self.s,self.c,[lo,lo,lo,hi,hi])
        self.assertFalse(msgs[0]);self.assertTrue(any('1 ГБ' in x for x in msgs[1]))
        self.assertFalse(msgs[2]);self.assertTrue(any('восстановлен' in x for x in msgs[4]))

    def test_data_gap_resets_cpu_streak(self):
        msgs,_=cycle(self.s,self.c,[result(cpu=98),{'ok':False},result(cpu=98),result(cpu=98)])
        self.assertFalse(any('CPU' in x for row in msgs for x in row))

    def test_date_reminder_no_daily_spam(self):
        s,c=configured(date(2026,11,18))
        msgs,state=cycle(s,c,[result()]*4)
        self.assertEqual(sum('аренды' in x for row in msgs for x in row),1)
        msgs2,_=cycle(s,c,[result()],state)
        self.assertFalse(any('аренды' in x for row in msgs2 for x in row))

    def test_existing_state_not_discarded(self):
        state={'available':False,'reminder_sent':'x','high_cpu_percent':True}
        _,new=evaluate_smart(self.s,result(),state,self.c,TODAY)
        self.assertTrue(new['high_cpu_percent'])

    def test_russian_measurement_declension(self):
        for n,expected in [(1,'1 измерение'),(2,'2 измерения'),(4,'4 измерения'),
                           (11,'11 измерений'),(21,'21 измерение'),
                           (41,'41 измерение'),(111,'111 измерений')]:
            self.assertEqual(russian_measurements(n),expected)


class AuditTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup)
        self.path=Path(td.name)/'history.sqlite3'
        self.h=HistoryStore(self.path)

    def test_action_audit(self):
        id=self.h.start_control_action('sweden',1111111111,'reboot',timestamp=1000)
        self.assertEqual(self.h.recent_control_actions('sweden')[0]['status'],'requested')
        self.h.finish_control_action(id,'accepted','API принял запрос',timestamp=1002)
        r=self.h.recent_control_actions('sweden')[0]
        self.assertEqual((r['actor_id'],r['action'],r['status']),
                         (1111111111,'reboot','accepted'))
        self.assertEqual(len(self.h.recent_control_actions('elsewhere')),0)

    def test_actions_persist_after_reopen(self):
        id=self.h.start_control_action('sweden',2222222222,'renewal_page',timestamp=1020)
        self.h.finish_control_action(id,'page_shown',timestamp=1021)
        self.assertEqual(HistoryStore(self.path).recent_control_actions('sweden')[0]['status'],
                         'page_shown')

    def test_migration_preserves_samples(self):
        with self.h.connect() as con:
            con.execute('INSERT INTO events (server_id,ts,kind,description) VALUES (?,?,?,?)',
                        ('sweden',100,'startup','previous'))
        HistoryStore(self.path)
        self.assertEqual(self.h.recent_events('sweden')[0]['description'],'previous')

    def test_action_validation(self):
        with self.assertRaises(ValueError): self.h.start_control_action('sweden',0,'reboot')
        with self.assertRaises(ValueError): self.h.start_control_action('sweden',1,'remove')
        with self.assertRaises(ValueError): self.h.finish_control_action(123,'rebooted')

    def test_different_actor_ids(self):
        for n in (1111111111,2222222222,3333333333):
            self.h.start_control_action('sweden',n,'renewal_page',timestamp=n)
        self.assertEqual({x['actor_id'] for x in self.h.recent_control_actions('sweden')},
                         {1111111111,2222222222,3333333333})

    def test_bot_exposes_actions_without_daily_jobs(self):
        src=(Path(__file__).resolve().parents[1]/'bot.py').read_text()
        tree=ast.parse(src)
        self.assertIn('evaluate_smart',src)
        self.assertIn("CommandHandler('actions', actions_view)",src)
        self.assertNotIn('run_daily(',src)
        self.assertNotIn('run_monthly(',src)


if __name__=='__main__': unittest.main()
