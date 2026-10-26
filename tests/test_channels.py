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
