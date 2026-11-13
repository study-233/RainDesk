import asyncio
import json
import time
import httpx

from model import DEFAULT_AI, solve
from materials import slide_image
from rain import Rain, Expired, Failure, deadline, describe, find_slide, question
from store import pack

DEFAULT_REMINDERS = {"classroom":True, "question":True, "result":True, "problem":True}


async def cancel(tasks):
    tasks = [t for t in tasks if t and t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def problem_id(event):
    if not isinstance(event, dict):
        return str(event)
    return str(event.get("prob") or event.get("problemId") or event.get("problemid") or event.get("id") or "")


class Engine:
    def __init__(self, store):
        self.s = store
        self.started = time.time()
        self.rooms = {}
        self.classrooms = {}
        self.catalog = {}
        self.join_tasks = {}
        self.join_errors = {}
        self.join_retry = {}
        self.lookup_after = {}
        self.open = {}
        self.connected = set()
        self.listeners = {}
        self.workers = {}
        self.gates = {}
        self.deck_locks = {}
        self.downloads = {}
        self.media_slots = asyncio.Semaphore(2)
        self.checking = set()
        self.ai_slots = asyncio.Semaphore(4)
        self.login_task = self.poll_task = None
        self.qr = self.login_error = ""
        self.health = "等待扫码"
        self.account = self.s.get("account", None, True)
        self.rain = None
        self.wake = asyncio.Event()

    @property
    def owner(self):
        return self.account["domain"]+"/"+self.account["uid"] if self.account else ""

    def scoped(self, key):
        return self.owner+"/"+key

    def start(self):
        if self.account:
            self.rain = Rain(self.account["domain"], self.account["cookies"])
            self.health = "连接中"
            self.poll_task = asyncio.create_task(self.monitor())

    async def reset(self):
        await cancel([self.poll_task, *self.join_tasks.values(), *self.listeners.values(), *self.workers.values(), *self.downloads.values()])
        if self.rain:
            await self.rain.http.aclose()
        self.rooms.clear()
        self.classrooms.clear()
        self.catalog.clear()
        self.join_errors.clear()
        self.join_retry.clear()
        self.join_tasks.clear()
        self.lookup_after.clear()
        self.open.clear()
        self.connected.clear()
        self.listeners.clear()
        self.workers.clear()
        self.gates.clear()
        self.deck_locks.clear()
        self.downloads.clear()
        self.account = self.rain = None

    async def login(self, domain):
        self.login_error = self.qr = ""
        client = Rain(domain)
        try:
            account = await client.login(lambda img:setattr(self, "qr", img))
            await self.reset()
            self.s.put("account", account, True)
            self.account = account
            self.start()
            self.s.log("扫码成功，已开始发现课堂")
        except Exception as exc:
            self.login_error = "二维码已过期，请重新获取" if isinstance(exc, TimeoutError) else describe(exc)
            self.s.log("扫码失败："+self.login_error, "warning")
        finally:
            self.qr = ""
            await client.http.aclose()

    def session(self, lid):
        key = self.scoped(lid+"/session")
        if lid not in self.gates:
            self.gates[lid] = self.s.get(key, None, True)
        return self.gates[lid]

    def policy(self, lid):
        override = self.s.get(self.scoped(lid+"/auto"))
        if override is not None:
            return override
        return self.classrooms.get(lid) in self.grants()

    def grants(self):
        return self.s.get(self.scoped("authorized-classes"), {})

    def checkin_mode(self):
        return self.s.get(self.scoped("checkin-mode"), "authorized")

    def reminders(self):
        return {**DEFAULT_REMINDERS, **self.s.get(self.scoped("reminders"), {})}

    def remind(self, category, message, level="info", once=None):
        self.s.log(message,level,self.reminders()[category],self.scoped(once) if once else None)

    def require_account(self):
        if not self.account or not self.account.get("cookies") or not self.rain:
            raise Failure("请先登录学生账号")

    def release_ready_answers(self, lid):
        if not self.policy(lid):
            return
        for pid, event in self.open.get(lid,{}).items():
            key = (self.owner,lid,pid)
            row = self.s.job(key)
            if row and row["state"]=="ready" and self.eligible(key):
                self.queue(key,event["deck"])

    async def load_courses(self):
        self.require_account()
        owner = self.owner
        catalog = await self.rain.courses()
        if self.owner != owner:
            raise Failure("账号已切换，请刷新班级列表")
        self.catalog = catalog
        return catalog

    def grant_class(self, cid, enable):
        self.require_account()
        grants = self.grants()
        if enable:
            name = self.catalog.get(cid) or next((self.rooms[lid] for lid in self.rooms if self.classrooms.get(lid)==cid),None)
            if not cid or not name:
                raise Failure("请从当前账号的班级列表中选择")
            grants[cid] = name
        else:
            grants.pop(cid,None)
        self.s.put(self.scoped("authorized-classes"),grants)
        for lid in self.rooms:
            if self.classrooms.get(lid)==cid:
                if not enable:
                    self.s.put(self.scoped(lid+"/auto"),False)
                self.schedule_join(lid)
                self.release_ready_answers(lid)
        self.wake.set()

    def auto_join(self,lid):
        mode = self.checkin_mode()
        return mode=="all" or (mode=="authorized" and self.classrooms.get(lid) in self.grants())

    def schedule_join(self,lid):
        if (not self.auto_join(lid) or self.session(lid) or lid in self.checking or lid in self.join_tasks
                or lid in self.join_errors or time.monotonic() < self.join_retry.get(lid,0)):
            return
        self.join_tasks[lid] = asyncio.create_task(self.automatic_checkin(lid))

    async def automatic_checkin(self,lid):
        try:
            if not self.auto_join(lid) or self.session(lid):
                return
            await self.checkin(lid)
        except Exception as exc:
            if lid in self.rooms:
                if isinstance(exc,(httpx.ConnectError,httpx.ConnectTimeout,httpx.PoolTimeout)):
                    self.s.put(self.scoped(lid+"/join-error"),None)
                    self.join_retry[lid] = time.monotonic()+30
                    self.remind("problem",self.rooms[lid]+" 签到前连接失败，后台将重试","warning",f"{lid}/join-network")
                    return
                # A timeout may occur after the remote side registered attendance.
                self.join_errors[lid] = describe(exc)
                self.s.put(self.scoped(lid+"/join-error"),self.join_errors[lid])
                self.remind("problem",self.rooms[lid]+" 自动签到未获确认，请检查后重试："+describe(exc),"attention")
        finally:
            self.join_tasks.pop(lid,None)

    async def checkin(self, lid):
        if lid not in self.rooms or lid in self.checking:
            raise Failure("课堂已结束或正在签到")
        owner = self.owner
        self.checking.add(lid)
        try:
            # If Windows stops during the POST, require a receipt check before another write.
            self.s.put(self.scoped(lid+"/join-error"),"上次签到中断，请核对签到状态")
            session = await self.rain.checkin(lid)
            if owner != self.owner or lid not in self.rooms:
                raise Failure("课堂或账号已切换")
            self.gates[lid] = session
            self.s.put(self.scoped(lid+"/session"), session, True)
            self.join_errors.pop(lid,None)
            self.s.put(self.scoped(lid+"/join-error"),None)
            self.remind("classroom",self.rooms[lid]+" 已签到",once=f"{lid}/checked-in")
            self.launch_listener(lid)
        finally:
            self.checking.discard(lid)

    def launch_listener(self, lid):
        if lid not in self.listeners or self.listeners[lid].done():
            self.listeners[lid] = asyncio.create_task(self.listen(lid))

    async def drop_room(self, lid):
        await cancel([self.listeners.pop(lid, None), self.join_tasks.pop(lid,None), *(t for k,t in list(self.workers.items()) if k[1] == lid)])
        self.rooms.pop(lid, None)
        self.classrooms.pop(lid,None)
        self.lookup_after.pop(lid,None)
        self.join_errors.pop(lid,None)
        self.join_retry.pop(lid,None)
        self.open.pop(lid, None)
        self.gates.pop(lid, None)
        self.connected.discard(lid)
        self.s.put(self.scoped(lid+"/auto"), None)
        self.s.put(self.scoped(lid+"/join-error"),None)
        self.s.put(self.scoped(lid+"/session"), None, True)

    async def discover(self):
        live = await self.rain.rooms()
        self.health = "监听中"
        for lid in self.rooms.keys()-live.keys():
            await self.drop_room(lid)
        for lid, info in live.items():
            new = lid not in self.rooms
            self.rooms[lid] = info["name"]
            cid = info["classroom"] or self.classrooms.get(lid)
            if not cid and time.monotonic() >= self.lookup_after.get(lid,0):
                self.lookup_after[lid] = time.monotonic()+60
                try:
                    cid = await self.rain.lesson_classroom(lid)
                except Exception:
                    pass  # A manual, single-session join remains available.
            self.classrooms[lid] = cid or ""
            if new:
                issue = self.s.get(self.scoped(lid+"/join-error"))
                if issue:
                    self.join_errors[lid] = issue
                self.remind("classroom",info["name"]+(" 已开课" if self.auto_join(lid) or self.session(lid) else " 已开课，请确认本场签到"),
                            once=f"{lid}/started")
            if self.session(lid):
                self.launch_listener(lid)
            else:
                self.schedule_join(lid)

    async def monitor(self):
        delay = 0
        while True:
            try:
                await self.discover()
                delay = 15
            except Expired as exc:
                self.health = "登录已过期"
                self.remind("problem",str(exc),"attention")
                for lid in list(self.rooms):
                    await self.drop_room(lid)
                self.account["cookies"] = {}
                self.s.put("account", self.account, True)
                return
            except Exception as exc:
                if self.health != "等待重连":
                    self.remind("problem","课堂发现中断："+describe(exc),"warning")
                self.health = "等待重连"
                delay = min(60, max(3, delay*2))
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
                self.wake.clear()
            except TimeoutError:
                pass

    async def listen(self, lid):
        backoff = 1
        failures = 0
        while lid in self.rooms:
            try:
                async for event in self.rain.stream(lid, self.session(lid)):
                    if event.get("op") == "hello":
                        self.connected.add(lid)
                        backoff = 1
                        failures = 0
                    if event.get("op") == "lessonfinished":
                        await self.drop_room(lid)
                        return
                    await self.event(lid, event)
                raise Failure("连接已关闭")
            except Exception as exc:
                failures += 1
                if failures == 1:
                    self.remind("problem",self.rooms.get(lid,lid)+"："+describe(exc),"warning")
            finally:
                self.connected.discard(lid)
                self.open[lid] = {}
            if failures >= 3 and isinstance(self.session(lid), dict):
                self.remind("problem","课堂连接多次失败，可在实时课堂重新确认签到","attention")
                failures = 0
            await asyncio.sleep(backoff)
            backoff = min(45, backoff*2)

    async def deck(self, lid, did, refresh=False):
        async with self.deck_locks.setdefault((lid,did), asyncio.Lock()):
            key = (self.owner,lid,did)
            row = self.s.db.execute("SELECT slides FROM decks WHERE owner=? AND lesson=? AND id=?",key).fetchone()
            if row and not refresh:
                return json.loads(row[0])
            session = self.session(lid)
            data = await self.rain.request(lid,session,"presentation/fetch",params={"presentation_id":did})
            if not isinstance(data.get("slides"), list):
                raise Failure("课件返回缺少页面列表")
            slides = data["slides"]
            self.s.put(self.scoped(lid+"/session"),session,True)
            self.s.db.execute("INSERT OR REPLACE INTO decks VALUES (?,?,?,?,?,?)",
                              (*key,data.get("title") or self.rooms[lid],pack(slides),time.time()))
            if key not in self.downloads:
                task = asyncio.create_task(self.cache_pages(key,slides))
                self.downloads[key] = task
                task.add_done_callback(lambda _:self.downloads.pop(key,None))
            return slides

    async def cache_pages(self,key,slides):
        failed = 0
        for index,slide in enumerate(slides):
            if slide.get("cover") or slide.get("coverAlt") or slide.get("thumbnail"):
                try:
                    async with self.media_slots:
                        await slide_image(self.s,*key,index,slide)
                except Exception:
                    failed += 1
        if failed:
            self.s.log(f"课件有 {failed} 页暂未缓存，可在浏览或导出时重试","warning")

    async def event(self, lid, event):
        op = event.get("op")
        active = self.open.setdefault(lid,{})
        timeline = [e for e in event.get("timeline",[]) if isinstance(e,dict)]
        item = event.get("problem") or event.get("slide") or {}
        did = str(item.get("pres") or event.get("presentation") or next((e["pres"] for e in reversed(timeline) if e.get("pres")), "") or "")
        if did:
            self.s.put(self.scoped(lid+"/deck"),did)
        else:
            did = self.s.get(self.scoped(lid+"/deck"),"")
        pid = problem_id(item)
        if op in {"lockproblem","closeproblem","finishproblem","stopproblem"} and pid:
            active.pop(pid,None)
            return
        events = []
        if isinstance(event.get("unlockedproblem"),list):
            ids = {problem_id(v) for v in event["unlockedproblem"]}
            for old in set(active)-ids:
                active.pop(old,None)
            history = {problem_id(e):e for e in timeline if e.get("type") == "problem"}
            for value in event["unlockedproblem"]:
                p = problem_id(value)
                detail = {**history.get(p,{}), **(value if isinstance(value,dict) else {})}
                events.append((p,detail))
        if op in {"unlockproblem","extendtime"} and pid:
            events.append((pid,item))
        for p, detail in events:
            if not p:
                continue
            pres = str(detail.get("pres") or did)
            active[p] = {"deadline":deadline(detail) if detail else active.get(p,{}).get("deadline"), "deck":pres}
            key = (self.owner,lid,p)
            row = self.s.job(key)
            if not row:
                self.remind("question",self.rooms[lid]+" 收到课堂题目",once=f"{lid}/{p}/question")
            if row:
                if detail and deadline(detail):
                    self.s.db.execute("UPDATE jobs SET deadline=? WHERE owner=? AND lesson=? AND problem=?",(deadline(detail),*key))
                if row["state"] not in {"queued","uncertain","accepted","ready"}:
                    continue
            self.queue(key, pres)
        if did and (op in {"hello","showpresentation","presentationupdated","presentationcreated","slidenav","fetchtimeline"}):
            await self.deck(lid,did,op=="presentationupdated")
        if op == "hello":
            for row in self.s.rows("SELECT problem,payload FROM jobs WHERE owner=? AND lesson=? AND state IN ('uncertain','accepted')",(self.owner,lid)):
                self.queue((self.owner,lid,row["problem"]),json.loads(row["payload"])["deck"])
        if op in {"locationcheckin","locationcheckinext","startcheckin","checkin","locationcheckinstarted"}:
            self.remind("problem","课堂发起签到，请在雨课堂中确认","attention")

    def queue(self, key, did):
        if key in self.workers or not did:
            return
        task = asyncio.create_task(self.process(key,did))
        self.workers[key] = task
        task.add_done_callback(lambda _:self.workers.pop(key,None))

    def eligible(self, key):
        _, lid, pid = key
        end = self.open.get(lid,{}).get(pid,{}).get("deadline")
        return bool(lid in self.connected and self.policy(lid) and end and time.time()+1.5 < end)

    async def process(self, key, did):
        _, lid, pid = key
        try:
            row = self.s.job(key)
            if row and row["state"] in {"uncertain","accepted"}:
                await self.verify(key,did,json.loads(row["answer"]))
                return
            if row and row["state"] == "ready" and not self.eligible(key):
                return
            slide = None
            for attempt in range(5):
                if attempt:
                    await asyncio.sleep(min(8,2**(attempt-1)))
                try:
                    slides = await self.deck(lid,did,refresh=attempt>0)
                    slide = find_slide(slides,pid)
                    if slide:
                        break
                except Exception:
                    if attempt==4:
                        raise
            if not slide:
                raise Failure("已收到发题，但课件暂缺对应题目")
            q = question(slides,slide)
            q.update(deck=did,room=self.rooms[lid])
            end = self.open.get(lid,{}).get(pid,{}).get("deadline")
            self.s.db.execute("INSERT OR IGNORE INTO jobs(owner,lesson,problem,created,payload,deadline,state) VALUES(?,?,?,?,?,?,'queued')",
                              (*key,time.time(),pack(q),end))
            if slide["problem"].get("result") not in (None, [], {}, ""):
                self.s.state(key,"answered","服务器已有作答记录")
                return
            if q["kind"] not in {1,2,4,6}:
                self.s.state(key,"manual","请在雨课堂中作答")
                self.remind("problem",self.rooms[lid]+f" 第 {q['page']} 页需要人工作答","attention",f"{lid}/{pid}/manual")
                return
            if row and row["answer"]:
                answers = json.loads(row["answer"])
            else:
                self.s.state(key,"thinking")
                async with self.ai_slots:
                    answers, reason, ms = await solve(self.s.get("ai",DEFAULT_AI),self.s.get("api_key","",True),q)
                self.s.db.execute("UPDATE jobs SET state='ready',answer=?,reason=?,ms=?,detail='' WHERE owner=? AND lesson=? AND problem=?",
                                  (pack(answers),reason,ms,*key))
            if self.eligible(key):
                # Re-fetch the student's result before a single, durable submission attempt.
                fresh = find_slide(await self.deck(lid,did,True),pid)
                if fresh is None or fresh["problem"].get("result") not in (None, [], {}, ""):
                    self.s.state(key,"answered","题目已变更或已有答案")
                    return
                if not self.eligible(key):
                    self.notify_ready(lid,pid,q)
                    return
                self.s.state(key,"sending")
                await self.rain.request(lid,self.session(lid),"problem/answer",json={
                    "dt":int(time.time()*1000),"problemId":pid,"problemType":q["kind"],"result":answers})
                self.s.state(key,"accepted","服务器已受理，等待独立回读")
                await self.verify(key,did,answers)
            else:
                self.notify_ready(lid,pid,q)
        except asyncio.CancelledError:
            row = self.s.job(key)
            if row and row["state"] == "sending":
                self.s.state(key,"uncertain","发送中断，请核对课堂回执")
            elif row and row["state"] == "thinking":
                self.s.state(key,"queued")
            raise
        except Exception as exc:
            row = self.s.job(key)
            if row:
                if row["state"] == "sending":
                    self.s.state(key,"uncertain","请求已发出，回执不明："+describe(exc))
                elif row["state"] not in {"uncertain","accepted"}:
                    self.s.state(key,"error",describe(exc))
            self.remind("problem",self.rooms.get(lid,lid)+" 处理题目失败："+describe(exc),"warning")

    def notify_ready(self,lid,pid,q):
        self.remind("result",self.rooms[lid]+f" 第 {q['page']} 页 AI 答案已生成，可在答题记录里核对",
                    once=f"{lid}/{pid}/ready")

    async def verify(self,key,did,answers):
        _, lid, pid = key
        for delay in (0,1,3):
            await asyncio.sleep(delay)
            slide = find_slide(await self.deck(lid,did,True),pid)
            result = slide["problem"].get("result") if slide else None
            if isinstance(result,dict):
                result = result.get("result",result.get("myAnswer"))
            if isinstance(result,list) and all(isinstance(x,str) for x in result):
                equal = sorted(result)==sorted(answers) if slide["problem"]["problemType"]==2 else result==answers
                self.s.state(key,"verified" if equal else "mismatch","服务器答案一致" if equal else "服务器答案与本地不同")
                self.remind("result" if equal else "problem",self.rooms[lid]+f" 第 {slide.get('index','?')} 页"+
                            ("提交已验证" if equal else "提交回读不一致，请核对"),"info" if equal else "attention",
                            f"{lid}/{pid}/"+("verified" if equal else "mismatch"))
                return
        self.remind("problem",self.rooms[lid]+" 提交回执尚未核实，请查看答题记录","attention",f"{lid}/{pid}/pending")
