"""Read-only, anonymous reachability probe. Never print QR tickets or cookies."""
import asyncio
import json

import httpx
from websockets.asyncio.client import connect


async def main():
    domain = "www.yuketang.cn"
    async with httpx.AsyncClient(timeout=12) as client:
        for path in ("/api/v3/user/basic-info", "/api/v3/classroom/on-lesson-upcoming-exam"):
            response = await client.get(f"https://{domain}{path}")
            print(path, response.status_code, response.json().get("code"), flush=True)
    async with connect(f"wss://{domain}/wsapp/", open_timeout=12) as ws:
        await ws.send(json.dumps(dict(op="requestlogin", role="web", version=1.4, type="qrcode", **{"from": "web"})))
        frame = json.loads(await asyncio.wait_for(ws.recv(), 12))
        print("QR frame contains ticket:", bool(frame.get("ticket")), flush=True)
        if frame.get("ticket"):
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.get(frame["ticket"])
                print("QR image:", response.status_code, response.headers.get("content-type"), len(response.content))


if __name__ == "__main__":
    asyncio.run(main())
