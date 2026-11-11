# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
'''
Tones: a base preset plus level offset and knob settings, auditioned on the live slot
(docs/spec-amp.md, "Tones"). No amp needed: the agent is a fake HTTP server.
'''

import json
import os
import tempfile
import unittest

import ChordsPlay.db
from ChordsPlay.amp import (AmpClient, AmpCues, build_cues, build_tone, describe, tone_document,
                            tone_key, tone_knobs)
from test_amp import FakeAgent, TempDb, wait_until

BASE = {'info': {'displayName': 'Echo Dome'},
        'audioGraph': {'nodes': [
            {'nodeId': 'stomp', 'FenderId': 'DUBS_Unknown', 'dspUnitParameters': {'bypass': False}},
            {'nodeId': 'mod', 'FenderId': 'ACD_ChorusSine',
             'dspUnitParameters': {'bypass': False, 'wetlvl': 0.5, 'depth': 0.25, 'ratehz': 2.0}},
            {'nodeId': 'amp', 'FenderId': 'ACD_Twin65GT',
             'dspUnitParameters': {'volume': -23.6, 'gain': 0.52, 'bass': 0.87, 'mid': 0.69,
                                   'treb': 0.67, 'gatePreset': 'mid'}},
            {'nodeId': 'delay', 'FenderId': 'ACD_ModDelay',
             'dspUnitParameters': {'bypass': False, 'wetlvl': 0.3, 'feedback': 0.4, 'time': 0.45}},
            {'nodeId': 'reverb', 'FenderId': 'ACD_FenderLargeHall',
             'dspUnitParameters': {'bypass': False, 'wetlvl': 0.34, 'decay': 0.6}}]}}


def amp(doc):
    return next(n for n in doc['audioGraph']['nodes'] if n['nodeId'] == 'amp')['dspUnitParameters']


class BuildTone(unittest.TestCase):

    def test_level_correction_params_name(self):
        doc = build_tone(BASE, level_db=3, params={'amp.gain': 0.8, 'reverb.wetlvl': 0.1,
                                                   'delay.bypass': True, 'amp.gatePreset': 'off'},
                         name='Solo +3', correction_db=-2)
        self.assertAlmostEqual(amp(doc)['volume'], -22.6)
        self.assertEqual((amp(doc)['gain'], amp(doc)['gatePreset']), (0.8, 'off'))
        reverb = next(n for n in doc['audioGraph']['nodes'] if n['nodeId'] == 'reverb')
        delay = next(n for n in doc['audioGraph']['nodes'] if n['nodeId'] == 'delay')
        self.assertEqual((reverb['dspUnitParameters']['wetlvl'], delay['dspUnitParameters']['bypass']), (0.1, True))
        self.assertEqual(doc['info']['displayName'], 'Solo +3')
        self.assertEqual(amp(BASE)['volume'], -23.6)            # the base is not changed

    def test_empty_and_unknown_blocks_ignored(self):
        doc = build_tone(BASE, params={'stomp.gain': 0.9, 'nosuch.x': 1, 'amp': 3})
        stomp = next(n for n in doc['audioGraph']['nodes'] if n['nodeId'] == 'stomp')
        self.assertNotIn('gain', stomp['dspUnitParameters'])

    def test_needs_amp(self):
        with self.assertRaises(ValueError):
            build_tone({'audioGraph': {'nodes': []}})

    def test_knobs(self):
        knobs = {k['key']: k for k in tone_knobs(BASE)}
        self.assertEqual(sorted(knobs), sorted(['amp.gain', 'amp.bass', 'amp.mid', 'amp.treb',
                                                'mod.bypass', 'mod.wetlvl', 'mod.depth',
                                                'delay.bypass', 'delay.wetlvl', 'delay.feedback',
                                                'reverb.bypass', 'reverb.wetlvl', 'reverb.decay']))
        self.assertTrue(knobs['reverb.bypass']['value'])         # "on" = not bypassed
        self.assertEqual(knobs['mod.wetlvl']['kind'], 'level')
        self.assertNotIn('delay.time', knobs)                    # not in 0..1 knob set

    def test_key_identifies_content(self):
        self.assertEqual(tone_key(build_tone(BASE, 3)), tone_key(build_tone(BASE, 3)))
        self.assertNotEqual(tone_key(build_tone(BASE, 3)), tone_key(build_tone(BASE, 6)))


class ToneDb(TempDb):

    def test_tones_and_tone_cues(self):
        db = self.db
        tid = db.saveTone('Dome +3', 21, 'Echo Dome', 3.0, {'reverb.wetlvl': 0.2})
        self.assertEqual(db.saveTone('Dome +3', 21, 'Echo Dome', 6.0, {}), tid)   # same name updates
        self.assertEqual(db.getTone(tid)['level_db'], 6.0)
        db.setSongTone(self.song, 90000, tid, live_slot=100)
        db.setSongPreset(self.song, 0, 19, '80S Rock')
        self.assertEqual(db.getSongPresets(self.song), [(0, 19, '80S Rock', None), (90000, 100, 'Dome +3', tid)])
        self.assertTrue(db.moveSongPreset(self.song, 90000, 95000))           # keeps the tone
        self.assertEqual(db.getSongPresets(self.song)[1], (95000, 100, 'Dome +3', tid))
        db.saveTone('Dome +3', 21, 'Echo Dome', 6.0, {})
        self.assertEqual(db.toneUsage(), {tid: 1})
        db.delTone(tid)
        self.assertEqual(db.getSongPresets(self.song), [(0, 19, '80S Rock', None)])
        with self.assertRaises(ValueError):
            db.setSongTone(self.song, 0, 999, 100)
        with self.assertRaises(ValueError):
            db.saveTone('  ', 1, None, 0, {})

    def test_preset_levels_only_signal_counts(self):
        db = self.db
        db.importPresetLevels({'method': 'noise', 'presets': [{'slot': 4, 'name': 'Metal 2000', 'correction_db': -31.6}]})
        self.assertIsNone(db.getPresetLevel(4))
        db.importPresetLevels({'method': 'signal', 'presets': [{'slot': 4, 'name': 'Metal 2000', 'correction_db': -2.5}]})
        self.assertEqual(db.getPresetLevel(4), (-2.5, 'Metal 2000'))
        db.importPresetLevels({'method': 'played', 'presets': [{'slot': 5, 'name': 'BB15 Solo', 'correction_db': 1.5}]})
        self.assertEqual(db.getPresetLevel(5), (1.5, 'BB15 Solo'))
        with self.assertRaises(ValueError):
            db.importPresetLevels({'method': 'guess', 'presets': []})


class ToneClient(unittest.TestCase):

    def setUp(self):
        self.agent = FakeAgent()
        self.client = AmpClient(self.agent.url, poll=0.1, timeout=5, live_slot=100).start()

    def tearDown(self):
        self.client.stop()
        self.agent.close()

    def test_audition_on_live_slot_and_back_to_slot(self):
        self.client.audition(build_tone(BASE, 3, name='Dome +3'))
        self.assertTrue(wait_until(lambda: self.agent.auditions == [('Dome +3', 100)]))
        self.assertTrue(wait_until(lambda: describe(self.client.snapshot()) == 'amp ▶ Dome +3'))
        self.client.audition(build_tone(BASE, 3, name='Dome +3'))      # same content: not sent again
        self.client.request(19)                                          # a real preset ends it
        self.assertTrue(wait_until(lambda: self.agent.posts == [19]))
        self.assertEqual(len(self.agent.auditions), 1)

    def test_newest_wins_across_slots_and_tones(self):
        self.agent.switch_delay = 0.3
        self.client.request(2)
        self.assertTrue(wait_until(lambda: self.agent.posts == [2]))
        self.client.audition(build_tone(BASE, 0, name='old'))
        self.client.audition(build_tone(BASE, 6, name='new'))
        self.assertTrue(wait_until(lambda: self.agent.auditions == [('new', 100)]))

    def test_preset_fetched_on_demand(self):
        self.assertIsNone(self.client.preset(21))
        self.assertTrue(wait_until(lambda: self.client.preset(21) is not None))
        self.assertEqual(self.client.preset(21)['info']['displayName'], 'P21')
        self.assertEqual(self.agent.preset_gets, [21])


class FakeToneClient(object):
    '''In-memory client: presets arrive when release() is called.'''

    def __init__(self):
        self.requests, self.auditions, self.stored, self.held = [], [], {}, True

    def request(self, slot):
        self.requests.append(slot)

    def audition(self, doc):
        self.auditions.append(doc['info']['displayName'])

    def names(self):
        return []

    def preset(self, slot):
        return None if self.held else self.stored.get(slot, BASE)


class ToneCues(TempDb):

    def test_tone_cue_waits_for_base_preset(self):
        tid = self.db.saveTone('Dome +3', 21, 'Echo Dome', 3.0, {})
        self.db.setSongPreset(self.song, 0, 19, '80S Rock')
        self.db.setSongTone(self.song, 60000, tid, 100)
        client = FakeToneClient()
        cues = AmpCues(self.db, client)
        cues.tick(self.song, 0)
        cues.tick(self.song, 61000)
        self.assertEqual((client.requests, client.auditions), ([19], []))     # base not there yet
        client.held = False
        cues.tick(self.song, 61200)
        self.assertEqual(client.auditions, ['Dome +3'])
        cues.tick(self.song, 62000)
        self.assertEqual(client.auditions, ['Dome +3'])                        # once per cue
        cues.tick(self.song, 0)                                                # song repeats
        self.assertEqual(client.requests, [19, 19])

    def test_tone_document_uses_signal_corrections_only(self):
        tid = self.db.saveTone('Dome', 21, 'Echo Dome', 3.0, {})
        client = FakeToneClient()
        client.held = False
        self.db.importPresetLevels({'method': 'noise', 'presets': [{'slot': 21, 'correction_db': 9.9}]})
        self.assertAlmostEqual(amp(tone_document(self.db, client, self.db.getTone(tid)))['volume'], -20.6)
        self.db.importPresetLevels({'method': 'signal', 'presets': [{'slot': 21, 'correction_db': 2.0}]})
        self.assertAlmostEqual(amp(tone_document(self.db, client, self.db.getTone(tid)))['volume'], -18.6)


if __name__ == '__main__':
    unittest.main()
