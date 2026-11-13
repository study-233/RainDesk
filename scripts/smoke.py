"""Smoke-test the packaged Windows agent in an isolated foreground data directory."""
import argparse
import asyncio
import json
import socket
import subprocess
import tempfile
from pathlib import Path

import httpx
from websockets.asyncio.client import connect


async def run(exe,online):
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        sock=socket.socket()
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
        sock.close()
        for cycle in range(2):
            proc=subprocess.Popen([str(exe),"--foreground",str(root),str(port)],creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",trust_env=False,timeout=5) as http:
                    for _ in range(100):
                        if proc.poll() is not None:
                            raise RuntimeError(f"Packaged backend exited {proc.returncode}")
                        try:
                            if (await http.get("/state")).status_code==401:
                                break
                        except httpx.RequestError:
                            pass
                        await asyncio.sleep(.15)
                    http.headers["Authorization"]="Bearer "+(root/"control.token").read_text()
                    state=(await http.get("/state")).raise_for_status().json()
                    assert state["account"] is None and not state["rooms"]
                    assert (await http.get("/state",headers={"Origin":"https://example.org"})).status_code==403
                    async with connect(f"ws://127.0.0.1:{port}/events",proxy=None) as ws:
                        await ws.send((root/"control.token").read_text())
                        assert "logs" in json.loads(await asyncio.wait_for(ws.recv(),5))
                    if cycle:
                        assert state["hasKey"] and state["ai"]["model"]=="local-smoke"
                    else:
                        cfg=dict(provider="LM Studio",url="http://127.0.0.1:1234/v1",model="local-smoke",vision=False,key="fixture")
                        (await http.put("/ai",json=cfg)).raise_for_status()
                        assert (await http.post("/rooms/inactive/join",json={"confirmed":True})).status_code==409
                        if online:
                            (await http.post("/login",json={"domain":"www.yuketang.cn"})).raise_for_status()
                            for _ in range(60):
                                q=(await http.get("/state")).json()
                                if q["qr"].startswith("data:image/"):
                                    print("Packaged anonymous QR: OK")
                                    break
                                if q["loginError"]:
                                    raise RuntimeError(q["loginError"])
                                await asyncio.sleep(.4)
                            else:
                                raise TimeoutError("QR unavailable")
            finally:
                proc.terminate()
                proc.wait(timeout=10)
        print("Packaged HTTP auth, origin checks, WebSocket, consent guard, DPAPI persistence and restart: OK")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("exe",type=Path)
    parser.add_argument("--online",action="store_true")
    args=parser.parse_args()
    asyncio.run(run(args.exe.resolve(),args.online))
