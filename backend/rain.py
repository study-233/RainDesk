import asyncio
import base64
import io
import json
from html import unescape
from urllib.parse import urlsplit
import re

import httpx
from PIL import Image
from websockets.asyncio.client import connect

DOMAINS = {"www.yuketang.cn", "pro.yuketang.cn", "changjiang.yuketang.cn"}


class Failure(Exception):
    """A credential-free message suitable for local diagnostics."""


class Expired(Failure):
    pass


def describe(exc):
    return str(exc)[:240] if isinstance(exc, Failure) else type(exc).__name__


def plain(value):
    return unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def classroom_id(value):
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        raw = str(value)
        if raw.isascii() and raw.isdecimal() and 0 < len(raw) <= 32 and int(raw) > 0:
            return raw
    return ""


def secure_image_url(value):
    if not isinstance(value, str):
        return False
    uri = urlsplit(value)
    return uri.scheme == "https" and bool(uri.hostname) and not uri.username


async def image_bytes(url):
    if not secure_image_url(url):
        raise Failure("课件未提供 HTTPS 图片")
    # Independent client prevents classroom credentials from reaching media/CDN hosts.
    async with httpx.AsyncClient(timeout=15) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            buf = bytearray()
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > 16_000_000:
                    raise Failure("图片超过 16 MB")
    return bytes(buf)


def jpeg(data):
    with Image.open(io.BytesIO(data)) as im:
        if im.width * im.height > 32_000_000:
            raise Failure("图片尺寸过大")
        im.thumbnail((2000, 2000))
        output = io.BytesIO()
        im.convert("RGB").save(output, "JPEG", quality=88)
        return output.getvalue()


def business(response, account_check=False):
    if response.status_code == 401 or (account_check and response.is_redirect):
        raise Expired("雨课堂登录已过期，请重新扫码")
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        code = result.get("code")
        message = str(result.get("msg", "")).upper()
        if account_check and (code in (50000, 401) or "LOGIN" in message):
            raise Expired("雨课堂登录已过期，请重新扫码")
        raise Failure(f"雨课堂拒绝请求（业务码 {code}）")
    return result.get("data")


class Rain:
    def __init__(self, domain, cookies=None):
        if domain not in DOMAINS:
            raise Failure("不支持的雨课堂站点")
        self.domain = domain
        self.ws = f"wss://{domain}/wsapp/"
        self.http = httpx.AsyncClient(base_url=f"https://{domain}", timeout=15, cookies=cookies or {},
                                      headers={"Referer": f"https://{domain}/v2/web/index", "User-Agent": "Mozilla/5.0 RainDesk/1.0"})

    async def login(self, display):
        async with asyncio.timeout(100), connect(self.ws, open_timeout=12) as socket:
            await socket.send(json.dumps({"op":"requestlogin","role":"web","type":"qrcode","from":"web","version":1.4}))
            async for frame in socket:
                event = json.loads(frame)
                if event.get("ticket"):
                    img = await image_bytes(event["ticket"])
                    display("data:image/jpeg;base64," + base64.b64encode(img).decode())
                if event.get("UserID") and event.get("Auth"):
                    response = await self.http.post("/pc/web_login", json={k:event[k] for k in ("UserID", "Auth")})
                    response.raise_for_status()
                    cookies = dict(self.http.cookies)
                    if not cookies.get("sessionid"):
                        raise Failure("扫码未返回有效会话")
                    profile = await self.profile()
                    return {"uid":str(profile.get("id") or event["UserID"]), "name":profile.get("name") or "已登录学生",
                            "domain":self.domain, "cookies":cookies}
        raise Failure("扫码连接已关闭")

    async def profile(self):
        return business(await self.http.get("/api/v3/user/basic-info"), True)

    async def rooms(self):
        data = business(await self.http.get("/api/v3/classroom/on-lesson-upcoming-exam"), True)
        return {str(r["lessonId"]): {"name":str(r["classroomName"]), "classroom":classroom_id(r.get("classroomId"))}
                for r in data["onLessonClassrooms"]}

    async def lesson_classroom(self, lesson):
        response = await self.http.get("/api/v3/classroom-report/student/lesson-info", params={"lesson_id":lesson})
        data = business(response)
        if isinstance(data, dict) and str(data.get("lessonId")) == lesson:
            return classroom_id(data.get("classroomId"))
        return ""

    async def courses(self):
        res = await self.http.get("/v2/api/web/courses/list", params={"identity":2})
        res.raise_for_status()
        result = res.json()
        if result.get("errcode") != 0 or not isinstance(result.get("data", {}).get("list"), list):
            raise Failure("班级列表暂时无法获取，请刷新或重新扫码")
        courses = {}
        for row in result["data"]["list"]:
            cid = classroom_id(row.get("classroom_id"))
            if cid:
                courses[cid] = str(row.get("name") or (row.get("course") or {}).get("name") or cid)
        return courses

    async def checkin(self, lesson):
        headers = {"x-csrftoken":self.http.cookies.get("csrftoken", "")}
        res = await self.http.post("/api/v3/lesson/checkin", json={"source":5,"lessonId":lesson}, headers=headers)
        data = business(res)
        if not data or not all((data.get("lessonToken"), data.get("identityId"), res.headers.get("Set-Auth"))):
            raise Failure("签到未返回课堂连接凭证，请到雨课堂核对")
        return dict(bearer=res.headers["Set-Auth"], auth=data["lessonToken"], userid=data["identityId"])

    async def request(self, lesson, session, path, **kwargs):
        headers = {"Authorization":"Bearer "+session["bearer"], "Referer":f"https://{self.domain}/lesson/fullscreen/v3/{lesson}?source=5",
                   "x-csrftoken":self.http.cookies.get("csrftoken", "")}
        res = await self.http.request("POST" if "json" in kwargs else "GET", "/api/v3/lesson/"+path, headers=headers, **kwargs)
        if res.headers.get("Set-Auth"):
            session["bearer"] = res.headers["Set-Auth"]
        return business(res)

    async def stream(self, lesson, session):
        async with connect(self.ws, open_timeout=12, ping_interval=20, ping_timeout=20) as socket:
            await socket.send(json.dumps(dict(op="hello", role="student", lessonid=lesson,
                                              userid=session["userid"], auth=session["auth"])))
            first = json.loads(await asyncio.wait_for(socket.recv(), 18))
            if first.get("op") != "hello":
                raise Failure("课堂 WebSocket 握手未获确认，请检查课堂授权")
            yield first
            async for frame in socket:
                yield json.loads(frame)


def deadline(event):
    def stamp(value):
        value = float(value or 0)
        return value / 1000 if value > 1e11 else value
    try:
        end = stamp(event.get("endTime"))
        if end > 1e9:
            return end
        start = stamp(event.get("dt") or event.get("unlockedAt"))
        limit = float(event.get("limit", event.get("timeLimit", 0)))
        if start > 1e9 and limit > 0:
            return start + limit
    except (ValueError, TypeError):
        pass
    return None


def slide_text(slide):
    return " ".join(plain(s.get("Text")) for s in slide.get("shapes") or [] if s.get("Text")).strip()


def find_slide(slides, pid):
    return next((s for s in slides if isinstance(s.get("problem"), dict)
                 and str(s["problem"].get("problemId") or s["id"]) == pid), None)


def question(slides, slide):
    p = slide["problem"]
    pos = slides.index(slide)
    context = [slide_text(s)[:1800] for s in slides[max(0,pos-2):pos] if not s.get("problem")]
    opts = [{"key":str(o["key"]), "text":plain(o.get("value"))} for o in p.get("options") or []]
    images = []
    for content in [p.get("body")] + [o.get("value") for o in p.get("options") or []]:
        for src in re.findall(r"""<img[^>]+src=["']([^"']+)""", str(content or ""), re.I):
            if secure_image_url(unescape(src)):
                images.append(unescape(src))
    if secure_image_url(slide.get("cover")):
        images.append(slide["cover"])
    return dict(body=plain(p.get("body")) or slide_text(slide), kind=int(p["problemType"]), options=opts,
                blanks=len(p.get("blanks") or []), images=list(dict.fromkeys(images)), context=context,
                page=slide.get("index"), visual=bool(images and (not plain(p.get("body")) or any(not o["text"] for o in opts)
                or "<img" in str(p.get("body")) or any("<img" in str(o.get("value")) for o in p.get("options") or [])
                or re.search(r"如图|下图|图中|见图",plain(p.get("body"))))))
