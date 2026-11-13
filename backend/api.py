import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from engine import Engine, cancel
from materials import export_pages, slide_image
from model import DEFAULT_AI, validate_config
from rain import DOMAINS, Failure, describe, find_slide

ORIGINS = ["http://tauri.localhost","https://tauri.localhost","tauri://localhost","http://localhost:5173"]


class Login(BaseModel):
    domain: str


class Consent(BaseModel):
    confirmed: bool


class CheckinMode(BaseModel):
    mode: Literal["manual","authorized","all"]


class ReminderSetting(BaseModel):
    enabled: bool


class AIConfig(BaseModel):
    provider: str = Field(max_length=100)
    model: str = Field(min_length=1,max_length=200)
    url: str = Field(max_length=1000)
    vision: bool = False
    key: str | None = Field(default=None,max_length=3000)


def public_job(row):
    payload = json.loads(row.pop("payload"))
    row.pop("owner",None)
    row["question"] = {k:v for k,v in payload.items() if k not in {"context","images"}}
    row["answer"] = json.loads(row["answer"]) if row["answer"] else []
    return row


def make_app(store):
    e = Engine(store)

    @asynccontextmanager
    async def lifespan(_):
        e.start()
        store.log("后台服务已启动")
        yield
        await cancel([e.login_task])
        await e.reset()
        store.db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware,allow_origins=ORIGINS,allow_methods=["GET","POST","PUT","DELETE"],
                       allow_headers=["Authorization","Content-Type"])

    @app.middleware("http")
    async def auth(req:Request, call_next):
        if req.headers.get("origin") and req.headers["origin"] not in ORIGINS:
            return Response(status_code=403)
        if req.method!="OPTIONS" and not secrets.compare_digest(req.headers.get("authorization",""),"Bearer "+store.token):
            return Response(status_code=401)
        res = await call_next(req)
        res.headers["Cache-Control"] = "no-store"
        return res

    @app.exception_handler(Failure)
    async def failure(_,exc):
        return JSONResponse({"detail":str(exc)},status_code=409)

    @app.exception_handler(Exception)
    async def error(_,exc):
        store.log("请求失败："+describe(exc),"warning")
        return JSONResponse({"detail":"操作失败："+describe(exc)},status_code=502)

    def jobs(offset=0,limit=30):
        return [public_job(r) for r in store.rows("SELECT * FROM jobs WHERE owner=? ORDER BY created DESC LIMIT ? OFFSET ?",
                                                  (e.owner,limit,offset))]

    cached_revision = None
    cached_records = {}

    def snapshot():
        nonlocal cached_revision, cached_records
        grants = e.grants()
        rooms = [dict(id=k,name=v,connected=k in e.connected,session=bool(e.session(k)),auto=e.policy(k),
                      classroom=e.classrooms.get(k,""), remembered=e.classrooms.get(k) in grants,
                      joining=k in e.checking or k in e.join_tasks,joinError=e.join_errors.get(k,"")) for k,v in e.rooms.items()]
        account = {k:e.account[k] for k in ("name","domain")} if e.account else None
        revision = (e.owner,store.db.total_changes)
        if revision != cached_revision:
            stats = store.rows("""SELECT count(*) AS total, sum(state='verified') AS verified, round(avg(ms)) AS latency
                                  FROM jobs WHERE owner=?""",(e.owner,))[0]
            decks = store.rows("""SELECT lesson,id,title,updated,json_array_length(slides) AS pages FROM decks
                                  WHERE owner=? ORDER BY updated DESC""",(e.owner,))
            cached_records = dict(jobs=jobs(),stats=stats,decks=decks,logs=store.rows("SELECT * FROM events ORDER BY id DESC LIMIT 60"),
                                  ai=store.get("ai",DEFAULT_AI),hasKey=bool(store.get("api_key","",True)))
            cached_revision = revision
        return dict(**cached_records,started=e.started,health=e.health,account=account,rooms=rooms,
                    automation={"mode":e.checkin_mode(),"courses":grants,"reminders":e.reminders()},
                    qr=e.qr,loginError=e.login_error,
                    loggingIn=bool(e.login_task and not e.login_task.done()))

    @app.get("/state")
    async def state():
        return snapshot()

    @app.websocket("/events")
    async def events(ws:WebSocket):
        if ws.headers.get("origin") not in [None,*ORIGINS]:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            token = await asyncio.wait_for(ws.receive_text(),4)
            if not secrets.compare_digest(token,store.token):
                await ws.close(code=1008)
                return
            previous = None
            while True:
                data = snapshot()
                if data != previous:
                    await ws.send_json(data)
                    previous = data
                else:
                    await ws.send_json({"heartbeat":True})
                await asyncio.sleep(2)
        except (WebSocketDisconnect,TimeoutError,RuntimeError):
            return

    @app.get("/history")
    async def history(offset:int=0):
        return jobs(max(0,offset),50)

    @app.get("/alerts")
    async def alerts(after:int=0):
        return store.rows("SELECT id,message FROM events WHERE notify=1 AND id>? ORDER BY id LIMIT 100",(after,))

    @app.post("/login")
    async def login(body:Login):
        if body.domain not in DOMAINS:
            raise Failure("站点无效")
        await cancel([e.login_task])
        e.login_task = asyncio.create_task(e.login(body.domain))
        return {"ok":True}

    @app.delete("/login")
    async def logout():
        await cancel([e.login_task])
        for lid in list(e.rooms):
            await e.drop_room(lid)
        await e.reset()
        store.put("account",None,True)
        e.health="等待扫码"
        e.login_error = ""
        return {"ok":True}

    @app.put("/ai")
    async def ai(body:AIConfig):
        config = body.model_dump(exclude={"key"})
        validate_config(config)
        old = store.get("ai",DEFAULT_AI)
        if body.key is not None or old["url"] != config["url"]:
            store.put("api_key",body.key or "",True)
        store.put("ai",config)
        return {"ok":True}

    @app.post("/rooms/{lid}/join")
    async def join(lid:str,body:Consent):
        if not body.confirmed:
            raise Failure("需要本人确认本场签到")
        await e.checkin(lid)
        return {"ok":True}

    @app.put("/rooms/{lid}/auto")
    async def auto(lid:str,body:Consent):
        if lid not in e.rooms or not e.account:
            raise Failure("该场课堂已结束")
        store.put(e.scoped(lid+"/auto"),body.confirmed)
        if body.confirmed:
            e.release_ready_answers(lid)
        store.log(e.rooms[lid]+(" 已启用测试场次自动提交" if body.confirmed else " 已恢复答案核对模式"))
        return {"ok":True}

    @app.get("/automation/courses")
    async def courses():
        return await e.load_courses()

    @app.put("/automation/courses/{cid}")
    async def grant(cid:str,body:Consent):
        e.grant_class(cid,body.confirmed)
        return {"ok":True}

    @app.put("/automation/checkin")
    async def checkin_mode(body:CheckinMode):
        e.require_account()
        store.put(e.scoped("checkin-mode"),body.mode)
        e.wake.set()
        return {"ok":True}

    @app.put("/automation/reminders/{category}")
    async def reminders(category:Literal["classroom","question","result","problem"],body:ReminderSetting):
        e.require_account()
        store.put(e.scoped("reminders"),{**e.reminders(),category:body.enabled})
        return {"ok":True}

    @app.post("/jobs/{lid}/{pid}/retry")
    async def retry(lid:str,pid:str):
        row = store.job((e.owner,lid,pid))
        if not row or lid not in e.rooms or row["state"] not in {"error","accepted","uncertain","queued","ready"}:
            raise Failure("当前状态无法重试")
        e.queue((e.owner,lid,pid),json.loads(row["payload"])["deck"])
        return {"ok":True}

    def get_deck(lid,did):
        row = store.db.execute("SELECT slides FROM decks WHERE owner=? AND lesson=? AND id=?",(e.owner,lid,did)).fetchone()
        if not row:
            raise HTTPException(404,"课件不存在")
        return json.loads(row[0])

    @app.get("/decks/{lid}/{did}/pages/{index}")
    async def page(lid:str,did:str,index:int):
        slides = get_deck(lid,did)
        if not 0 <= index < len(slides):
            raise HTTPException(404,"页码超出范围")
        return Response(await slide_image(store,e.owner,lid,did,index,slides[index]),media_type="image/jpeg")

    @app.get("/jobs/{lid}/{pid}/image")
    async def question_image(lid:str,pid:str):
        row = store.job((e.owner,lid,pid))
        if not row:
            raise HTTPException(404,"题目不存在")
        did = json.loads(row["payload"])["deck"]
        slides = get_deck(lid,did)
        slide = find_slide(slides,pid)
        if not slide:
            raise HTTPException(404,"找不到题图")
        return Response(await slide_image(store,e.owner,lid,did,slides.index(slide),slide),media_type="image/jpeg")

    @app.get("/decks/{lid}/{did}/export/{fmt}")
    async def export(lid:str,did:str,fmt:Literal["pdf","pptx"]):
        slides = get_deck(lid,did)
        if not slides:
            raise Failure("课件暂无页面")
        owner = e.owner
        images = []
        for i, slide in enumerate(slides):
            images.append(await slide_image(store,owner,lid,did,i,slide))
        return Response(await asyncio.to_thread(export_pages,images,fmt),media_type="application/octet-stream")
    return app
