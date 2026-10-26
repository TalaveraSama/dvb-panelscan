import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch, Mock


class ChannelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("DVB_PANEL_DATA_DIR")
        os.environ["DVB_PANEL_DATA_DIR"] = self.tmp.name
        spec = importlib.util.spec_from_file_location("settings_panel", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.panel); self.panel._auth = None  # Auth is covered separately in test_auth.py
        self.panel.STATE["transponders"] = [
            {"id": tid, "adapter": adapter, "freq_mhz":10970, "scan":{"services":[{"sid":672,"name":"Original"}]}}
            for adapter, tid in enumerate(("a", "b"))]
        self.server = self.panel.ThreadingHTTPServer(("127.0.0.1",0), self.panel.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port
        self.config = {"name":"Cuenta HD", "host":"127.0.0.1", "port":5000, "user":"user",
                       "pass":"test-only", "deskey":"0102030405060708091011121314"}

    def tearDown(self):
        self.panel.stream_stop(); self.server.shutdown(); self.server.server_close(); self.thread.join()
        if self.old is None: os.environ.pop("DVB_PANEL_DATA_DIR",None)
        else: os.environ["DVB_PANEL_DATA_DIR"] = self.old
        self.tmp.cleanup()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base+path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type":"application/json"})
        try: response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)
        with response: return response.status, json.load(response)

    def test_newcamd_protocol_validation_identity_and_broker_command(self):
        default = self.panel.validate_profile(self.config)
        self.assertEqual(default["proto"], 525)
        legacy = self.panel.validate_profile({**self.config, "proto": "524"})
        self.assertEqual(legacy["proto"], 524)
        self.assertEqual(self.panel.newcamd_identity(default), self.panel.newcamd_identity(self.config))
        self.assertNotEqual(self.panel.newcamd_identity(default), self.panel.newcamd_identity(legacy))
        for invalid in ("526", "auto", True, 524.0, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.panel.validate_profile({**self.config, "proto": invalid})
        identity = self.call("/api/newcamd/profiles", legacy)[1]["id"]
        self.panel._load()
        self.assertEqual(self.call("/api/newcamd/profiles/" + identity)[1]["proto"], 524)
        with patch.object(self.panel, "find_tbsstream", return_value="/test/tbsstream"), patch.object(
                self.panel._streams, "acquire", return_value="session") as acquire:
            self.panel._session_start(self.panel.STATE["transponders"][0], nc=legacy, sid=672)
            cmd = acquire.call_args.kwargs["broker_command"]
            self.assertEqual(cmd[cmd.index("--newcamd-proto")+1], "524")
            self.assertNotIn("--newcamd-proto", acquire.call_args.args[2])

    def test_fixed_caid_ecm_settings_validation_and_command(self):
        code, response = self.call("/api/channels/a%3A672/edit", {"caid": "0b00", "ecm_pid": 5169})
        self.assertEqual(code, 200)
        self.assertEqual(self.panel.STATE["channels"]["a:672"]["caid"], "0B00")
        self.assertEqual(self.panel.transponder_views()[0]["scan"]["services"][0]["ecm_pid"], 5169)
        with patch.object(self.panel, "find_tbsstream", return_value="/test/tbsstream"), patch.object(self.panel._streams, "acquire", return_value="session") as acquire:
            self.assertEqual(self.panel._session_start(self.panel.STATE["transponders"][0], nc=self.config, sid=672), "session")
            command = acquire.call_args.args[2]
            self.assertEqual(command[command.index("--caid") + 1], "0B00")
            self.assertEqual(command[command.index("--ecm-pid") + 1], "5169")
        for body in ({"caid": "GGGG", "ecm_pid": 100}, {"caid": "0000", "ecm_pid": 100}, {"caid": "0B00", "ecm_pid": True}, {"caid": "0B00", "ecm_pid": 8191}, {"caid": "", "ecm_pid": 100}):
            self.assertEqual(self.call("/api/channels/a%3A672/edit", body)[0], 400)
        self.assertEqual(self.call("/api/channels/a%3A672/edit", {"caid": "", "ecm_pid": None})[0], 200)
        self.assertNotIn("caid", self.panel.STATE["channels"]["a:672"])
        self.assertNotIn("ecm_pid", self.panel.STATE["channels"]["a:672"])

    def test_descramble_delay_validation_persistence_and_command(self):
        self.assertEqual(self.panel.transponder_views()[0]["scan"]["services"][0]["descramble_delay_ms"], 500)
        tp = self.panel.STATE["transponders"][0]
        with patch.object(self.panel, "find_tbsstream", return_value="/test/tbsstream"), patch.object(self.panel._streams, "acquire", return_value="session") as acquire:
            for delay in (0, 500, 2000):
                with self.subTest(delay=delay), patch.object(self.panel._streams, "stop_channel") as stop:
                    self.assertEqual(self.call("/api/channels/a:672/edit", {"descramble_delay_ms": delay})[0], 200)
                    stop.assert_called_once_with("a:672")
                    self.panel._load()
                    self.panel._session_start(tp, nc=self.config, sid=672)
                    cmd = acquire.call_args.args[2]
                    self.assertEqual(cmd[cmd.index("--descramble-delay-ms") + 1], str(delay))
                    self.assertEqual(self.panel.transponder_views()[0]["scan"]["services"][0]["descramble_delay_ms"], delay)
        for delay in (-1, 2001, True, "500", None, 1.5):
            self.assertEqual(self.call("/api/channels/a:672/edit", {"descramble_delay_ms": delay})[0], 400)
        self.assertEqual(self.panel.STATE["channels"]["a:672"]["descramble_delay_ms"], 2000)

    def test_active_broker_test_does_not_open_second_tcp(self):
        self.panel.STATE["newcamd"] = self.config
        broker = Mock()
        broker.process.poll.return_value = None
        identity = self.panel.newcamd_identity(self.config)
        self.panel._streams.brokers[identity] = broker
        real_connect = self.panel.socket.create_connection
        probes = []
        def connect(address, *args, **kwargs):
            if address[1] == self.server.server_port:
                return real_connect(address, *args, **kwargs)
            probes.append(address)
            return Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
        with patch.object(self.panel.socket, "create_connection", side_effect=connect):
            code, result = self.call("/api/newcamd/test", {})
            self.assertEqual(code, 200)
            self.assertTrue(result["ok"])
            self.assertIn("no se abre otra", result["message"])
            self.assertEqual(probes, [])
            self.assertEqual(identity, self.panel.newcamd_identity({**self.config, "name": "Otro perfil", "host": "127.0.0.1", "deskey": ":".join(self.config["deskey"][i:i+2] for i in range(0,28,2)).upper()}))
            broker.process.poll.return_value = 1
            self.assertEqual(self.call("/api/newcamd/test", {})[0], 200)
            self.assertEqual(probes, [(self.config["host"], self.config["port"])])

    def profile(self):
        code, response = self.call("/api/newcamd/profiles", self.config)
        self.assertEqual(code,200); return response["id"]

    def test_assign_profile_and_real_command_uses_it(self):
        identity = self.profile()
        code, _ = self.call("/api/channels/a:672/edit", {"name":"HD Editado", "newcamd_profile":identity})
        self.assertEqual(code,200)
        tp = self.panel.STATE["transponders"][0]
        with patch.object(self.panel, "find_tbsstream", return_value="/bin/true"), patch.object(self.panel._streams,"acquire") as acquire:
            self.panel._session_start(tp, sid=672)
            args = acquire.call_args.kwargs["broker_command"]
            self.assertEqual(args[args.index("--user")+1], "user")
            self.assertEqual(args[args.index("--pass")+1], "test-only")
        self.assertEqual(self.call("/api/newcamd/profiles/"+identity+"/delete",{})[0],409)
        listed = self.call("/api/newcamd/profiles")[1]
        self.assertTrue(all("pass" not in p and "deskey" not in p for p in listed))

    def test_metadata_persists_across_scan_and_changes_exports(self):
        changes = {"name":"Nuevo", "group":"Infantil", "number":12, "logo":"https://example.com/logo.png"}
        self.assertEqual(self.call("/api/channels/a:672/edit",changes)[0],200)
        self.panel.STATE["transponders"][0]["scan"] = {"services":[{"sid":672,"name":"Rescan"}]}
        self.panel._load()
        playlist = self.panel.build_m3u8("http://host/{channel_id}")
        self.assertIn('tvg-name="Nuevo"',playlist); self.assertIn('group-title="Infantil"',playlist)
        self.assertIn('tvg-chno="12"',playlist); self.assertIn('tvg-logo="https://example.com/logo.png"',playlist)
        self.assertIn("Nuevo",self.panel.build_xmltv())
        self.assertEqual(self.panel.transponder_views()[0]["scan"]["services"][0]["name"],"Nuevo")
        self.assertEqual(os.stat(Path(self.tmp.name)/"channels.json").st_mode & 0o777, 0o600)

    def test_disable_blocks_stream_and_excludes_exports(self):
        self.assertEqual(self.call("/api/channels/a:672/edit",{"enabled":False})[0],200)
        self.assertNotIn("http://host/a:672",self.panel.build_m3u8("http://host/{channel_id}"))
        self.assertNotIn('channel id="a:672"',self.panel.build_xmltv())
        with patch.object(self.panel,"find_tbsstream",return_value="/bin/true"):
            with self.assertRaises(self.panel.StreamError) as error:
                self.panel._session_start(self.panel.STATE["transponders"][0],sid=672)
        self.assertEqual(error.exception.status,403)

    def test_edits_target_only_canonical_channel_and_stop_it(self):
        with patch.object(self.panel._streams,"stop_channel") as stop:
            self.assertEqual(self.call("/api/channels/a:672/edit",{"name":"Uno"})[0],200)
            stop.assert_called_once_with("a:672")
        self.assertEqual(self.panel.transponder_views()[1]["scan"]["services"][0]["name"],"Original")
        self.assertEqual(self.call("/api/channels/672/delete",{})[0],409)
        self.assertEqual(self.call("/api/channels/a:672/delete",{})[0],200)
        self.assertEqual(len(self.panel.STATE["transponders"][1]["scan"]["services"]),1)

    def test_invalid_fields_and_profile_rejected_without_changes(self):
        for invalid in ({"newcamd_profile":"missing"},{"sid":1},{"logo":"javascript:alert(1)"},
                        {"enabled":"false"},{"number":True},{"number":-1},{"name":""}):
            self.assertEqual(self.call("/api/channels/a:672/edit",invalid)[0],400)
        self.assertEqual(self.panel.STATE["channels"],{})
        self.assertEqual(self.call("/api/newcamd/profiles",{"host":"x","port":5000,"deskey":"bad"})[0],400)

    def test_profile_change_stops_only_assigned_channels(self):
        identity = self.profile()
        self.call("/api/channels/a:672/edit",{"newcamd_profile":identity})
        with patch.object(self.panel._streams,"status",return_value=[{"channel_id":"a:672"},{"channel_id":"b:672"}]), patch.object(self.panel._streams,"stop_channel") as stop:
            self.assertEqual(self.call("/api/newcamd/profiles",{**self.config,"id":identity,"name":"Cambiado"})[0],200)
            stop.assert_called_once_with("a:672")

    def test_delete_transponder_cleans_assignments_and_invalid_test_id(self):
        identity = self.profile()
        self.call("/api/channels/a:672/edit", {"newcamd_profile":identity})
        self.assertEqual(self.call("/api/transponders/a/delete",{})[0],200)
        self.assertEqual(self.call("/api/newcamd/profiles/"+identity+"/delete",{})[0],200)
        self.assertEqual(self.call("/api/newcamd/test",{"id":{}})[0],400)

    def test_epg_settings_real_xmltv_and_same_sid_isolation(self):
        import time
        import datetime
        import xml.etree.ElementTree as ET
        self.assertEqual(self.call("/api/epg/settings",{"days":7})[0],200)
        self.assertEqual(self.call("/api/epg/settings")[1]["days"],7)
        for days in (0,15,"7",True):
            self.assertEqual(self.call("/api/epg/settings",{"days":days})[0],400)
        start=int(time.time())+2*86400
        self.panel._epg.put("a:672",{"event_id":12,"start":start,"stop":start+3600,"title":"Real & recibido"})
        self.panel._epg.flush()
        data=self.call("/api/epg/channel?channel=a:672")[1]
        self.assertEqual(data["events"][0]["start"],start)
        self.assertEqual(self.call("/api/epg/channel?channel=b:672")[1]["events"],[])
        xml=ET.fromstring(self.panel.build_xmltv())
        events=xml.findall("programme")
        self.assertEqual(len(events),1)
        self.assertEqual(events[0].attrib["channel"],"a:672")
        self.assertEqual(datetime.datetime.strptime(events[0].attrib["start"],"%Y%m%d%H%M%S %z").timestamp(),start)
        self.assertEqual(events[0].find("title").text,"Real & recibido")
        self.assertEqual(self.call("/api/epg/settings",{"days":1})[0],200)
        self.assertFalse(ET.fromstring(self.panel.build_xmltv()).findall("programme"))

    def test_catalogue_logos_in_view_m3u_xml_and_http(self):
        self.panel.STATE['transponders'][0]['scan']['services'][0]['name']='Discovery Kids HD'
        view=self.panel.transponder_views()[0]['scan']['services'][0]
        self.assertEqual(view['logo'],'/static/logo-discovery-kids.png')
        self.assertIn('tvg-logo="http://host/static/logo-discovery-kids.png"',self.panel.build_m3u8('http://host/stream/{channel_id}'))
        xml=self.panel.build_xmltv(base_url=self.base)
        self.assertIn('<icon src="'+self.base+'/static/logo-discovery-kids.png"/>',xml)
        self.assertNotIn('<programme',xml)
        with urllib.request.urlopen(self.base+'/api/playlist.m3u8?base_url=http://other/stream/{channel_id}') as response:
            self.assertIn('tvg-logo="'+self.base+'/static/logo-discovery-kids.png"',response.read().decode())
        with urllib.request.urlopen(self.base+view['logo']) as response:
            self.assertEqual(response.headers.get_content_type(),'image/png')
            self.assertTrue(response.read().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(self.call('/api/channels/a:672/edit',{'logo':view['logo']})[0],200)
        self.call('/api/channels/a:672/edit',{'logo':'https://example.com/custom.png'})
        self.assertEqual(self.panel.transponder_views()[0]['scan']['services'][0]['logo'],'https://example.com/custom.png')
        self.assertEqual(self.panel.transponder_views()[1]['scan']['services'][0]['logo'],'')

    def test_epg_status_only_active_receivers_and_no_history(self):
        self.assertEqual(self.call('/api/epg/status')[1]['receivers'],[])
        collector=self.panel.EITCollector(lambda events: None)
        with patch.dict(self.panel._streams.muxes,{0:('mux',Mock(ts_callback=collector.feed))}):
            reception=self.call('/api/epg/status')[1]
            self.assertEqual(reception['receivers'][0]['adapter'],0)
            self.assertEqual(reception['receivers'][0]['eit_packets'],0)
            self.assertEqual(reception['stored_events'],0)
        self.assertEqual(self.call('/api/epg/status')[1]['receivers'],[])

    def test_utc_offset_persist_validation_and_xmltv_instant(self):
        import datetime
        import time
        start=int(time.time())+3600
        self.panel._epg.put('a:672',{'event_id':1,'start':start,'stop':start+3600,'title':'Real'})
        self.assertEqual(self.call('/api/epg/settings',{'days':2,'utc_offset_minutes':-360})[0],200)
        self.assertEqual(self.call('/api/epg/settings')[1]['utc_offset_minutes'],-360)
        self.assertEqual(self.call('/api/epg/channel?channel=a:672')[1]['utc_offset_minutes'],-360)
        stamp=datetime.datetime.fromtimestamp(start,datetime.timezone(datetime.timedelta(hours=-6))).strftime('%Y%m%d%H%M%S %z')
        self.assertIn('start="'+stamp+'"',self.panel.build_xmltv())
        self.assertEqual(self.panel._epg.events_for('a:672')[0]['start'],start)
        restored=self.panel.EPGStore(self.panel._epg.path)
        self.assertEqual(restored.utc_offset_minutes,-360)
        self.assertEqual(restored.days,2)
        for bad in (-721,841,True,'-360',1,None):
            self.assertEqual(self.call('/api/epg/settings',{'days':7,'utc_offset_minutes':bad})[0],400)
        self.assertEqual(self.panel._epg.days,2)
        self.assertEqual(self.call('/api/epg/settings',{'days':3})[0],200)
        self.assertEqual(self.panel._epg.utc_offset_minutes,-360)
        self.assertEqual(self.call('/api/epg/settings',{'days':3,'utc_offset_minutes':345})[0],200)

    def test_version_matches_runtime_and_html(self):
        version=Path('VERSION').read_text().strip()
        self.assertEqual(self.call('/api/status')[1]['version'],version)
        with urllib.request.urlopen(self.base) as response:
            page=response.read().decode()
        self.assertIn('v'+version,page)
        self.assertNotIn('@VERSION@',page)

    def test_mosaic_group_metadata_keeps_channel_identity(self):
        tp=self.panel.STATE['transponders'][0]
        tp.update(adapter=3,pol='V',symbol_rate=30000000,name='Amazonas')
        groups=self.call('/api/epg')[1]
        self.assertEqual(groups[0]['adapter'],3)
        self.assertEqual(groups[0]['pol'],'V')
        self.assertEqual(groups[0]['symbol_rate'],30000000)
        self.assertEqual(groups[0]['services'][0]['channel_id'],'a:672')
        self.assertEqual(groups[1]['services'][0]['channel_id'],'b:672')

    def test_edit_transponder_preserves_scan_overrides_epg_and_identity(self):
        import time
        self.call('/api/channels/a:672/edit',{'name':'Conservar','group':'Mi grupo'})
        self.panel._epg.put('a:672',{'event_id':1,'start':int(time.time())+100,'stop':int(time.time())+200,'title':'EPG'})
        code,result=self.call('/api/transponders/a/edit',{'name':'Amazonas','freq_mhz':12230,'lnb_lo':10750,'symbol_rate':30000000,'type':'dvbs'})
        self.assertEqual(code,200)
        self.assertEqual(result['transponder']['id'],'a')
        self.assertEqual(result['transponder']['scan']['services'][0]['sid'],672)
        self.panel._load()
        self.assertEqual(self.panel.STATE['transponders'][0]['freq_mhz'],12230)
        self.assertEqual(self.panel.transponder_views()[0]['scan']['services'][0]['name'],'Conservar')
        self.assertEqual(len(self.panel._epg.events_for('a:672')),1)
        self.assertEqual(len(self.panel.STATE['transponders']),2)

    def test_tp_profile_inherited_overridden_and_used_in_broker(self):
        identity=self.profile()
        self.assertEqual(self.call('/api/transponders/a/edit',{'newcamd_profile':identity})[0],200)
        view=self.panel.transponder_views()[0]['scan']['services'][0]
        self.assertTrue(view['profile_inherited'])
        self.assertEqual(view['newcamd_profile'],identity)
        tp=self.panel.STATE['transponders'][0]
        with patch.object(self.panel,'find_tbsstream',return_value='/bin/true'),patch.object(self.panel._streams,'acquire') as acquire:
            self.panel._session_start(tp,sid=672)
            args=acquire.call_args.kwargs['broker_command']
            self.assertEqual(args[args.index('--user')+1],'user')
        self.assertEqual(self.call('/api/newcamd/profiles/'+identity+'/delete',{})[0],409)
        self.call('/api/channels/a:672/edit',{'newcamd_profile':'default'})
        view=self.panel.transponder_views()[0]['scan']['services'][0]
        self.assertFalse(view['profile_inherited'])
        self.assertEqual(view['newcamd_profile'],'default')
        self.call('/api/channels/a:672/edit',{'newcamd_profile':''})
        self.assertEqual(self.panel.channel_profile(tp,672),identity)
        self.assertEqual(self.panel.transponder_views()[1]['scan']['services'][0]['newcamd_profile'],'default')

    def test_tp_edit_rejects_active_scan_invalid_and_unknown_fields(self):
        with patch.object(self.panel._streams,'status',return_value=[{'adapter':0,'channel_id':'a:672'}]):
            self.assertEqual(self.call('/api/transponders/a/edit',{'freq_mhz':12230})[0],409)
            self.assertEqual(self.call('/api/transponders/a/edit',{'name':'Solo nombre'})[0],200)
        self.panel._streams.scanning.add(0)
        self.assertEqual(self.call('/api/transponders/a/edit',{'name':'No'})[0],409)
        self.panel._streams.scanning.clear()
        for body in ({'id':'otro'},{'scan':None},{'adapter':-1},{'freq_mhz':float('nan')},{'timeout':0},{'newcamd_profile':'missing'}):
            self.assertEqual(self.call('/api/transponders/a/edit',body)[0],400)
        self.assertEqual(self.call('/api/transponders/missing/edit',{'name':'No'})[0],404)
        self.assertEqual(self.panel.STATE['transponders'][0]['freq_mhz'],10970)

    def test_new_tp_profile_is_persisted_and_referenced(self):
        identity=self.profile()
        code,result=self.call('/api/transponders',{'type':'dvbs','freq_mhz':12230,'lnb_lo':10750,'symbol_rate':30000000,'newcamd_profile':identity})
        self.assertEqual(code,200)
        self.panel._load()
        tp=next(t for t in self.panel.STATE['transponders'] if t['id']==result['transponder']['id'])
        self.assertEqual(tp['newcamd_profile'],identity)
        self.assertEqual(self.call('/api/newcamd/profiles/'+identity+'/delete',{})[0],409)

    def test_universal_lnb_flags_for_scanner_and_shared_capture(self):
        self.assertEqual(self.call('/api/transponders/a/edit',{'lnb_mode':'universal','freq_mhz':12230,'symbol_rate':30000000})[0],200)
        tp=self.panel.STATE['transponders'][0]
        normalized=self.panel._norm_tp(tp)
        self.assertIn('--lnb-universal',self.panel._scan_cmd('/bin/true',normalized))
        with patch.object(self.panel,'find_tbsstream',return_value='/bin/true'),patch.object(self.panel._streams,'acquire') as acquire:
            self.panel._session_start(tp,sid=672)
            self.assertIn('--lnb-universal',acquire.call_args.kwargs['mux_command'])
        self.panel._load()
        self.assertEqual(self.panel.STATE['transponders'][0]['lnb_mode'],'universal')
        self.assertEqual(self.panel._norm_tp(self.panel.STATE['transponders'][1])['lnb_mode'],'manual')
        self.assertEqual(self.call('/api/transponders/a/edit',{'lnb_mode':'invalid'})[0],400)

    def test_astra_preview_apply_idempotency_and_no_users_import(self):
        from test_astra_import import options
        body=options()
        before=json.dumps(self.panel.STATE,sort_keys=True)
        code,preview=self.call('/api/import/astra/preview',body)
        self.assertEqual(code,200)
        self.assertEqual(preview['channels'],1)
        self.assertEqual(json.dumps(self.panel.STATE,sort_keys=True),before)
        self.assertNotIn('DO-NOT-IMPORT',json.dumps(preview))
        code,result=self.call('/api/import/astra/apply',{**body,'preview_token':preview['preview_token']})
        self.assertEqual(code,200)
        self.assertTrue((Path(self.tmp.name)/'import-backups'/result['backup']).is_file())
        tp=next(t for t in self.panel.STATE['transponders'] if t.get('astra_id')=='tp')
        self.assertEqual(tp['symbol_rate'],30000000)
        self.assertEqual(tp['adapter'],8)
        self.assertEqual(tp['lnb_mode'],'universal')
        self.assertEqual(self.panel.STATE['channels'][tp['id']+':672']['newcamd_profile'],'default')
        self.assertTrue(self.panel.STATE['channels'][tp['id']+':672']['enabled'])
        self.assertEqual(self.panel.STATE['transponders'][0]['id'],'a')
        self.assertNotIn('users',self.panel.STATE)
        again=self.call('/api/import/astra/preview',body)[1]
        self.assertEqual(again['transponders'][0]['action'],'actualizar')
        self.assertEqual(self.call('/api/import/astra/apply',{**body,'preview_token':again['preview_token']})[0],200)
        self.assertEqual(len(self.panel.STATE['transponders']),3)

    def test_astra_preview_missing_mapping_stale_confirmation_and_busy(self):
        from test_astra_import import options
        body=options()
        self.assertEqual(self.call('/api/import/astra/preview',{**body,'adapters':{}})[0],400)
        self.assertEqual(self.call('/api/import/astra/apply',body)[0],409)
        preview=self.call('/api/import/astra/preview',body)[1]
        self.panel.STATE['transponders'][0]['name']='Changed'
        self.assertEqual(self.call('/api/import/astra/apply',{**body,'preview_token':preview['preview_token']})[0],409)
        with patch.object(self.panel._streams,'status',return_value=[{'running':True}]):
            self.assertEqual(self.call('/api/import/astra/preview',body)[0],409)
        self.panel._streams.scanning.add(0)
        self.assertEqual(self.call('/api/import/astra/preview',body)[0],409)
        self.panel._streams.scanning.clear()

    def test_astra_account_import_requires_explicit_valid_deskey(self):
        from test_astra_import import options
        body=options();body.update(profiles={},import_profiles=True)
        self.assertEqual(self.call('/api/import/astra/preview',body)[0],400)
        body['deskey']='0102030405060708091011121314'
        code,preview=self.call('/api/import/astra/preview',body)
        self.assertEqual(code,200)
        self.assertNotIn('synthetic-only',json.dumps(preview))
        self.assertEqual(preview['profiles_created'],1)
        self.assertEqual(self.call('/api/import/astra/apply',{**body,'preview_token':preview['preview_token']})[0],200)
        account=next(iter(self.panel.STATE['profiles'].values()))
        self.assertEqual(account['pass'],'synthetic-only')
        self.assertEqual(account['deskey'],body['deskey'])

    def test_astra_r_polarization_not_silently_rewritten(self):
        from test_astra_import import options
        body=options();body['config']['dvb_tune'][0]['polarization']='R'
        code,preview=self.call('/api/import/astra/preview',body)
        self.assertEqual(code,200)
        self.assertEqual(preview['transponders'][0]['pol'],'R')
        self.assertTrue(any('NO se convierte' in message for message in preview['warnings']))
