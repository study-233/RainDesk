"""Exercise the production adapters and durable state machine against isolated transports."""
import asyncio
import json
import io
import sys
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import AsyncMock, patch

import httpx
from websockets.asyncio.server import serve

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from engine import Engine
from model import parse_answer
from rain import Rain, Failure, Expired
from store import Store
from PIL import Image
from pptx import Presentation
from materials import export_pages
from rain import question


class Chat(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path!="/v1/chat/completions" or "messages" not in body:
            self.send_error(400)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"choices":[{"message":{"content":json.dumps({"answers":["B"],"explanation":"local fixture"})}}]}).encode())

    def log_message(self,*_):
        pass


class Pipeline(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = ThreadingHTTPServer(("127.0.0.1",0),Chat)
        cls.thread = Thread(target=cls.model.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.model.shutdown()
        cls.model.server_close()

    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name))
        self.e=Engine(self.store)
        self.e.account={"domain":"www.yuketang.cn","uid":"student","cookies":{"sessionid":"fixture"}}
        self.store.put("ai",{"url":f"http://127.0.0.1:{self.model.server_port}/v1","model":"test","vision":False})
        self.e.rain=Rain("www.yuketang.cn")
        await self.e.rain.http.aclose()
        self.e.rain.http=httpx.AsyncClient(base_url="https://www.yuketang.cn",transport=httpx.MockTransport(self.remote))
        self.e.rooms["1"]="授权课堂"
        self.e.gates["1"]={"bearer":"test","auth":"room","userid":2}
        self.e.connected.add("1")
        self.answers={}
        self.sent=[]
        self.drop_ack=False
        self.expired=False
        self.live=[{"lessonId":"1","classroomName":"授权课堂"}]
        self.checkins=[]

    async def asyncTearDown(self):
        await self.e.reset()
        self.store.db.close()
        self.tmp.cleanup()

    def remote(self,request):
        data=None
        path=request.url.path.split("/")[-1]
        if path=="on-lesson-upcoming-exam":
            if self.expired:
                return httpx.Response(200,json={"code":50000,"msg":"未登录"})
            data={"onLessonClassrooms":self.live,"exams":[{"id":42}]}
        elif path=="list":
            return httpx.Response(200,json={"errcode":0,"data":{"list":[{"classroom_id":300,"name":"授权课堂"}]}})
        elif path=="checkin":
            self.checkins.append(json.loads(request.content)["lessonId"])
            return httpx.Response(200,headers={"Set-Auth":"bearer"},json={"code":0,"data":{"lessonToken":"room","identityId":2}})
        elif path=="fetch":
            slides=[{"id":p,"index":n,"problem":{"problemType":1,"problemId":p,"body":"Which?",
                    "options":[{"key":"A","value":"A"},{"key":"B","value":"B"}],"result":self.answers.get(p)}}
                    for n,p in enumerate(["101","102","103","104","105"],1)]
            data={"title":"Test","slides":slides}
        elif path=="answer":
            body=json.loads(request.content)
            self.sent.append(body["problemId"])
            self.answers[body["problemId"]]=body["result"]
            if self.drop_ack:
                raise httpx.ReadTimeout("fixture lost response")
        else:
            self.fail(path)
        return httpx.Response(200,json={"code":0,"msg":"OK","data":data})

    def unlock(self,p,limit=60):
        return {"op":"unlockproblem","problem":{"pres":"10","prob":p,"dt":time.time()*1000,"limit":limit}}

    async def drain(self):
        await asyncio.gather(*list(self.e.workers.values()))

    def state(self,p):
        return self.store.job((self.e.owner,"1",p))["state"]

    async def test_review_deadline_dedup_and_verification(self):
        await self.e.event("1",self.unlock("101"))
        await self.drain()
        self.assertEqual(self.state("101"),"ready")
        self.assertEqual(self.sent,[])
        self.store.put(self.e.scoped("1/auto"),True)
        event=self.unlock("102")
        for ev in (event,event,self.unlock("103",0),self.unlock("104",-1),self.unlock("105")):
            await self.e.event("1",ev)
        await self.drain()
        await self.e.event("1",event)
        await self.drain()
        self.assertCountEqual(self.sent,["102","105"])
        self.assertEqual(self.state("102"),"verified")
        self.assertEqual(self.state("103"),"ready")

    async def test_closed_and_expired(self):
        self.store.put(self.e.scoped("1/auto"),True)
        event=self.unlock("101")
        event["problem"]["dt"]-=90000
        await self.e.event("1",event)
        await self.e.event("1",self.unlock("102"))
        await self.e.event("1",{"op":"lockproblem","problem":{"prob":"102"}})
        await self.drain()
        self.assertEqual(self.sent,[])

    async def wait_for_delay(self, pid):
        async with asyncio.timeout(5):
            while True:
                row = self.store.job((self.e.owner,"1",pid))
                if row and row["state"] == "waiting":
                    return row["submit_after"]
                await asyncio.sleep(.01)

    async def test_delay_survives_retry_and_duplicate_unlock(self):
        self.store.put(self.e.scoped("1/auto"),True)
        self.store.put(self.e.scoped("submit-delay"),1)
        event = self.unlock("101")
        await self.e.event("1",event)
        due = await self.wait_for_delay("101")
        self.assertGreater(due,time.time())
        self.assertFalse(self.sent)
        task = self.e.workers[(self.e.owner,"1","101")]
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.state("101"),"ready")
        self.store.put(self.e.scoped("submit-delay"),60)
        sent_at = []
        request = self.e.rain.request
        async def record(*args,**kwargs):
            if args[2] == "problem/answer":
                sent_at.append(time.time())
            return await request(*args,**kwargs)
        with patch.object(self.e.rain,"request",record):
            await self.e.event("1",event)
            await self.e.event("1",event)
            await self.drain()
        self.assertEqual(self.sent,["101"])
        self.assertGreaterEqual(sent_at[0],due)
        self.assertEqual(self.state("101"),"verified")

    async def test_delayed_submission_respects_pause_and_deadline(self):
        self.store.put(self.e.scoped("1/auto"),True)
        self.store.put(self.e.scoped("submit-delay"),10)
        await self.e.event("1",self.unlock("101",5))
        await self.drain()
        self.assertIn("剩余答题时间",self.store.job((self.e.owner,"1","101"))["detail"])
        await self.e.event("1",self.unlock("102"))
        await self.wait_for_delay("102")
        self.store.put(self.e.scoped("1/auto"),False)
        await self.drain()
        self.assertEqual(self.state("102"),"ready")
        self.assertFalse(self.sent)

    async def test_delay_setting_api(self):
        from api import make_app
        self.store.put("account",self.e.account | {"name":"test"},True)
        app = make_app(self.store)
        with patch.object(Engine,"require_account"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://local",
                                         headers={"Authorization":"Bearer "+self.store.token}) as http:
                self.assertEqual((await http.get("/state")).json()["automation"]["submitDelay"],0)
                for invalid in (-1,0.5,True,3601):
                    self.assertEqual((await http.put("/automation/submit-delay",json={"seconds":invalid})).status_code,422)
                (await http.put("/automation/submit-delay",json={"seconds":20})).raise_for_status()
                self.assertEqual((await http.get("/state")).json()["automation"]["submitDelay"],20)
        self.assertEqual(self.e.submit_delay(),20)
        self.e.account = {**self.e.account,"uid":"other"}
        self.assertEqual(self.e.submit_delay(),0)

    async def test_lost_ack_recovery_reads_without_resend(self):
        self.store.put(self.e.scoped("1/auto"),True)
        self.drop_ack=True
        event=self.unlock("101")
        await self.e.event("1",event)
        await self.drain()
        self.assertEqual(self.state("101"),"uncertain")
        await self.e.event("1",event)
        await self.drain()
        self.assertEqual(self.state("101"),"verified")
        self.assertEqual(self.sent,["101"])

    async def test_discovery_consent_session_and_websocket(self):
        self.assertEqual(await self.e.rain.rooms(),{"1":{"name":"授权课堂","classroom":""}})
        self.expired=True
        with self.assertRaises(Expired):
            await self.e.rain.rooms()
        session=await self.e.rain.checkin("1")
        async def teacher(ws):
            frame=json.loads(await ws.recv())
            self.assertEqual(frame,{"op":"hello","userid":2,"role":"student","lessonid":"1","auth":"room"})
            await ws.send(json.dumps({"op":"hello","presentation":"10"}))
            await ws.send(json.dumps(self.unlock("101")))
        with patch("websockets.asyncio.client.get_proxy",return_value=None):
            await self.receive_classroom(teacher,session)
        await self.drain()
        self.assertEqual(self.state("101"),"ready")

    async def receive_classroom(self,teacher,session):
        async with serve(teacher,"127.0.0.1",0) as server:
            self.e.rain.ws=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async for frame in self.e.rain.stream("1",session):
                await self.e.event("1",frame)

    async def test_credentials_and_restart(self):
        self.store.put("test","secret-marker",True)
        self.assertNotIn(b"secret-marker",self.store.db.execute("SELECT value FROM vault WHERE key='test'").fetchone()[0])
        await self.e.event("1",self.unlock("101"))
        await self.drain()
        self.store.state((self.e.owner,"1","101"),"sending")
        self.store.db.close()
        self.store=Store(Path(self.tmp.name))
        self.e.s=self.store
        self.assertEqual(self.state("101"),"uncertain")
        self.assertEqual(self.store.get("test",None,True),"secret-marker")

    async def test_authorize_once_two_sessions_and_reminders(self):
        self.assertEqual(await self.e.load_courses(),{"300":"授权课堂"})
        self.e.grant_class("300",True)
        async def teacher(ws):
            hello=json.loads(await ws.recv())
            pid="101" if hello["lessonid"]=="A" else "102"
            await ws.send(json.dumps({"op":"hello","presentation":"10"}))
            for _ in range(2):
                await ws.send(json.dumps(self.unlock(pid)))
            await ws.wait_closed()
        async with serve(teacher,"127.0.0.1",0) as server:
            self.e.rain.ws=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            with patch("websockets.asyncio.client.get_proxy",return_value=None):
                for lid,pid in [("A","101"),("B","102")]:
                    self.live=[{"lessonId":lid,"classroomId":300,"classroomName":"授权课堂"},
                               {"lessonId":"unrelated","classroomId":400,"classroomName":"授权课堂"}]
                    await self.e.discover()
                    await self.e.discover()
                    async with asyncio.timeout(8):
                        while True:
                            row=self.store.job((self.e.owner,lid,pid))
                            if row and row["state"]=="verified": break
                            await asyncio.sleep(.03)
                    await self.e.discover()
                    self.assertFalse(self.e.policy("unrelated"))
                    self.assertIsNone(self.e.session("unrelated"))
                    self.live=[]
                    await self.e.discover()
        self.assertEqual(self.checkins,["A","B"])
        self.assertEqual(self.sent,["101","102"])
        for prefix in ("A/101","B/102"):
            for suffix in ("question","verified"):
                notices=self.store.rows("SELECT notify FROM events WHERE event_key=?",(self.e.scoped(f"{prefix}/{suffix}"),))
                self.assertEqual(notices,[{"notify":1}])

    async def test_pause_revoke_scopes_and_restart(self):
        self.e.catalog={"300":"授权课堂"}
        self.e.classrooms["1"]="300"
        self.e.grant_class("300",True)
        self.store.put(self.e.scoped("1/auto"),False)
        self.store.put(self.e.scoped("1/session"),self.e.session("1"),True)
        next_engine=Engine(self.store)
        next_engine.account=self.e.account
        next_engine.classrooms={"1":"300","2":"300","unrelated":"400"}
        self.assertFalse(next_engine.policy("1"))
        self.assertTrue(next_engine.policy("2"))
        self.assertTrue(next_engine.session("1"))
        self.assertTrue(next_engine.auto_join("2"))
        self.store.put(next_engine.scoped("checkin-mode"),"manual")
        self.assertFalse(next_engine.auto_join("2"))
        self.store.put(next_engine.scoped("checkin-mode"),"all")
        self.assertTrue(next_engine.auto_join("unrelated"))
        self.assertFalse(next_engine.policy("unrelated"))
        next_engine.account={**self.e.account,"uid":"different-user"}
        self.assertFalse(next_engine.policy("2"))
        self.assertFalse(next_engine.auto_join("2"))
        self.e.grant_class("300",False)
        self.assertFalse(self.e.grants())

    async def test_unidentified_class_and_failed_checkin(self):
        self.e.catalog={"300":"授权课堂"}
        self.e.grant_class("300",True)
        self.live=[{"lessonId":"unknown","classroomName":"授权课堂"}]
        with patch.object(self.e.rain,"lesson_classroom",AsyncMock(return_value="")):
            await self.e.discover()
        self.assertFalse(self.e.auto_join("unknown"))
        self.assertFalse(self.e.policy("unknown"))
        self.live[0]["classroomId"]=300
        with patch.object(self.e.rain,"checkin",AsyncMock(side_effect=Failure("需要额外授权"))) as checkin:
            await self.e.discover()
            await asyncio.gather(*self.e.join_tasks.values())
            await self.e.discover()
            self.assertEqual(checkin.await_count,1)
            self.assertIn("unknown",self.e.join_errors)

    def test_notification_preferences_keep_local_history(self):
        self.store.put(self.e.scoped("reminders"),{"question":False})
        for _ in range(2):
            self.e.remind("question","收到新题",once="1/101/question")
        self.e.remind("result","提交已验证",once="1/101/verified")
        self.assertEqual([r["notify"] for r in self.store.rows("SELECT notify FROM events ORDER BY id")],[0,1])

    async def test_permission_while_question_open(self):
        self.e.classrooms["1"]="300"
        await self.e.event("1",self.unlock("101"))
        await self.drain()
        self.assertEqual(self.state("101"),"ready")
        self.e.grant_class("300",True)
        await self.drain()
        self.assertEqual(self.state("101"),"verified")
        self.assertEqual(self.sent,["101"])

    def test_structured_answer_validation(self):
        q={"options":[{"key":"A"},{"key":"B"}],"kind":2,"blanks":2}
        for kind,valid,invalid in [(1,["A"],["A","B"]),(2,["A","B"],["X"]),(6,["B"],["true"]),(4,["一","二"],["一"])]:
            q["kind"]=kind
            self.assertEqual(parse_answer(json.dumps({"answers":valid,"explanation":"ok"}),q)[0],valid)
            with self.assertRaises(Failure):
                parse_answer(json.dumps({"answers":invalid,"explanation":"no"}),q)

    def test_visual_detection_and_material_export(self):
        slide={"index":1,"cover":"https://www.yuketang.cn/test.jpg","problem":{"problemType":1,"body":"如图所示",
               "options":[{"key":"A","value":"甲"},{"key":"B","value":"乙"}]}}
        q=question([slide],slide)
        self.assertTrue(q["visual"])
        self.assertEqual(q["images"],[slide["cover"]])
        output=io.BytesIO()
        Image.new("RGB",(80,45),"white").save(output,"JPEG")
        images=[output.getvalue()]*2
        self.assertEqual(len(Presentation(io.BytesIO(export_pages(images,"pptx"))).slides),2)
        pdf=export_pages(images,"pdf")
        self.assertTrue(pdf.startswith(b"%PDF") and b"/Count 2" in pdf)


if __name__=="__main__":
    unittest.main()
