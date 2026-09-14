import unittest
from datetime import UTC, date, datetime, timedelta

from adsb_ingest.aircraft_history import history_window, quiet_intervals


def day(n, stamp, **changes):
    return dict(dataset_day_id=n, utc_date=stamp.date(), address='abcdef', registration='G-TEST',
                first_seen_at=stamp, last_seen_at=stamp, derivation_version='flight-visits-v1',
                dataset_status='PROCESSED', **changes)


def visit(n, stamp, **changes):
    result = dict(dataset_day_id=n,address='abcdef',airport_ident='TEST',airport_name='Test airport',
                  first_evidence_at=stamp,last_evidence_at=stamp,open_at_start=True,open_at_end=True,
                  ground_observation_count=0)
    result.update(changes)
    return result


class AircraftHistoryTests(unittest.TestCase):
    def setUp(self):
        self.a=datetime(2026,8,20,12,36,tzinfo=UTC)
        self.b=datetime(2026,9,9,11,27,tzinfo=UTC)
        self.days=[day(1,self.a),day(2,self.b)]
        self.visits=[visit(1,self.a),visit(2,self.b)]

    def test_infers_twenty_day_gap_without_ground_reports_or_mro_links(self):
        gaps=quiet_intervals(self.days,self.visits,'GTEST',None)
        self.assertEqual(len(gaps),1)
        self.assertEqual(gaps[0]['elapsed_seconds'],int((self.b-self.a).total_seconds()))
        self.assertFalse(gaps[0]['ground_at_both_boundaries'])
        self.assertFalse(gaps[0]['open_end'])

    def test_intervening_observation_without_an_airport_prevents_false_stay(self):
        self.days.insert(1,day(3,self.a+timedelta(days=5)))
        self.assertEqual(quiet_intervals(self.days,self.visits,'GTEST',None),[])

    def test_changed_registration_or_address_never_joins_a_gap(self):
        self.days.insert(1,{**day(3,self.a+timedelta(days=5)), 'registration':'G-OTHER'})
        self.assertEqual(quiet_intervals(self.days,self.visits,'GTEST',None),[])
        self.days=self.days[::2]
        self.days[1]['address']='123456'
        self.visits[1]['address']='123456'
        self.assertEqual(quiet_intervals(self.days,self.visits,'GTEST',None),[])

    def test_requires_matching_airport_at_actual_trace_boundaries(self):
        for change in ({'airport_ident':'ELSE'},{'open_at_start':False},
                       {'first_evidence_at':self.b+timedelta(seconds=61)}):
            with self.subTest(change=change):
                self.assertEqual(quiet_intervals(self.days,[self.visits[0],{**self.visits[1],**change}],'GTEST',None),[])

    def test_open_gap_stops_at_available_data_and_is_not_a_bounded_stay(self):
        through=self.a+timedelta(days=4)
        gaps=quiet_intervals(self.days[:1],self.visits[:1],'GTEST',through)
        self.assertEqual(len(gaps),1)
        self.assertTrue(gaps[0]['open_end'])
        self.assertIsNone(gaps[0]['after'])
        self.assertEqual(gaps[0]['ended_at'],through)
        self.assertEqual(quiet_intervals(self.days[:1],self.visits[:1],'GTEST',self.a+timedelta(hours=5)),[])

    def test_legacy_or_incomplete_endpoint_cannot_start_a_stay(self):
        for change in ({'derivation_version':None},{'dataset_status':'PROCESSING'}):
            self.assertEqual(quiet_intervals([{**self.days[0],**change},self.days[1]],self.visits,'GTEST',None),[])

    def test_default_and_custom_ranges(self):
        self.assertEqual(history_window(None,None,date(2026,9,9),date(2026,8,1)),(date(2026,8,1),date(2026,9,9)))
        for start,end in [('2025-01-01','2026-09-01'),('2026-09-09','2026-08-01'),('bad','2026-08-01')]:
            with self.assertRaises(ValueError): history_window(start,end,None)
