import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'panel'))
import astra_import


def source():
    return {'dvb_tune':[
        {'id':'tp','mac':'00:22:ab:93:60:90','frequency':'12230','polarization':'V','type':'S','symbolrate':'30000','name':'Astra TP','enable':True},
        {'id':'off','mac':'00:22:ab:93:60:91','frequency':'12350','polarization':'H','type':'S2','symbolrate':'30000','enable':False}],
        'make_stream':[{'id':'s','name':'Canal','enable':True,'input':['dvb://tp#pnr=672&cam=cam']},
                       {'id':'off','name':'Canal duplicado apagado','enable':False,'input':['dvb://tp#pnr=672&cam=cam']}],
        'softcam':[{'id':'cam','type':'newcamd','name':'Cuenta','host':'localhost','port':'15050','user':'test','pass':'synthetic-only'}],
        'users':{'admin':{'cipher':'DO-NOT-IMPORT'}}, 'auth':{'enable':False}}


def options():
    return {'config':source(),'adapters':{'00:22:ab:93:60:90':8},'profiles':{'cam':'default'}}


class ImportStorageTests(unittest.TestCase):
    def test_backup_and_restart_recovery(self):
        state={'profiles':{},'transponders':[],'channels':{}}
        changed=copy.deepcopy(state);changed['channels']={'tp:1':{'name':'New'}}
        with tempfile.TemporaryDirectory() as directory:
            backup=astra_import.apply(directory,state,changed)
            self.assertEqual(state,changed)
            path=Path(directory)/'import-backups'/backup
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            old=json.loads(path.read_text())
            astra_import.atomic_json(Path(directory)/'astra-recovery.json',old)
            astra_import.recover(directory)
            self.assertEqual(json.loads((Path(directory)/'channels.json').read_text()),{})
            self.assertFalse((Path(directory)/'astra-recovery.json').exists())

    def test_write_failure_restores_previous_state(self):
        state={'profiles':{},'transponders':[],'channels':{}}
        changed=copy.deepcopy(state);changed['channels']={'new':{}}
        real=astra_import.atomic_json
        failed=[False]
        def write(path,value):
            if str(path).endswith('transponders.json') and not failed[0]:
                failed[0]=True;raise OSError('simulated disk failure')
            real(path,value)
        with tempfile.TemporaryDirectory() as directory,patch.object(astra_import,'atomic_json',side_effect=write):
            with self.assertRaises(OSError):astra_import.apply(directory,state,changed)
            self.assertEqual(state['channels'],{})
            self.assertEqual(json.loads((Path(directory)/'channels.json').read_text()),{})
