"""Windows SCM entry point and interactive notification companion."""
import os
import json
import base64
import hashlib
import secrets
import subprocess
import sys
import time
import winreg
from pathlib import Path

import servicemanager
import uvicorn
import win32api
import win32event
import win32security
import win32service
import win32serviceutil
import win32ts

from api import make_app
from store import DATA, PORT, Store

NAME = "RainDeskAgent"
RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


def serve(root=DATA, port=PORT):
    config = uvicorn.Config(make_app(Store(root)),host="127.0.0.1",port=port,log_config=None,access_log=False,
                            ws="websockets",ws_max_size=16384,timeout_graceful_shutdown=8)
    return uvicorn.Server(config)


class Service(win32serviceutil.ServiceFramework):
    _svc_name_ = NAME
    _svc_display_name_ = "RainDesk 雨课堂后台"
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stopping = True
        if getattr(self,"server",None):
            self.server.should_exit = True

    def SvcDoRun(self):
        self.server = serve()
        if not getattr(self,"stopping",False):
            self.server.run()
            if not self.server.started or not getattr(self,"stopping",False):
                os._exit(2)  # Let SCM failure recovery restart an unhealthy worker.


def sc(*args, allowed=()):
    result = subprocess.run(["sc.exe",*args],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode and result.returncode not in allowed:
        raise RuntimeError(f"SCM {args[0]} error {result.returncode}")
    return result.returncode


def stop_service():
    if sc("query",NAME,allowed=(1060,))==1060:
        return
    sc("stop",NAME,allowed=(1062,))
    win32serviceutil.WaitForServiceStatus(NAME,win32service.SERVICE_STOPPED,30)


def install():
    exe = f'"{sys.executable}"'
    sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(
        win32security.OpenProcessToken(win32api.GetCurrentProcess(),8),win32security.TokenUser)[0])
    session = win32ts.ProcessIdToSessionId(os.getpid())
    user = win32ts.WTSQuerySessionInformation(None,session,win32ts.WTSUserName)
    if user:
        domain = win32ts.WTSQuerySessionInformation(None,session,win32ts.WTSDomainName)
        sid = win32security.ConvertSidToStringSid(win32security.LookupAccountName(None,domain+"\\"+user)[0])
    DATA.mkdir(parents=True,exist_ok=True)
    if DATA.stat().st_file_attributes & 0x400:
        raise RuntimeError("Unexpected data directory reparse point")
    def acl(path,*entries):
        subprocess.run(["icacls.exe",str(path),"/inheritance:r","/grant:r",*entries],
                       check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    acl(DATA,"*S-1-5-18:(OI)(CI)F","*S-1-5-32-544:(OI)(CI)F","*S-1-5-19:(OI)(CI)M",f"*{sid}:(RX)")
    token = DATA/"control.token"
    if not token.exists():
        token.write_text(secrets.token_urlsafe(48),encoding="ascii")
    if token.stat().st_file_attributes & 0x400 or token.stat().st_nlink!=1:
        raise RuntimeError("Unexpected control token file")
    acl(token,"*S-1-5-18:F","*S-1-5-32-544:F","*S-1-5-19:R",f"*{sid}:R")
    command = "create" if sc("query",NAME,allowed=(1060,)) else "config"
    sc(command,NAME,"binPath=",exe,"start=","auto","obj=","NT AUTHORITY\\LocalService","DisplayName=","RainDesk 雨课堂后台")
    sc("failure",NAME,"reset=","86400","actions=","restart/5000/restart/15000/restart/60000")
    sc("failureflag",NAME,"1")
    sc("start",NAME,allowed=(1056,))
    win32serviceutil.WaitForServiceStatus(NAME,win32service.SERVICE_RUNNING,30)
    # RUNNING is set by SCM before ASGI initialization. Check the actual API too.
    import httpx
    with httpx.Client(trust_env=False,timeout=2) as http:
        for _ in range(30):
            try:
                res = http.get(f"http://127.0.0.1:{PORT}/state",headers={"Authorization":"Bearer "+token.read_text()})
                if res.status_code==200 and "health" in res.json():
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Local API did not become ready")
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key,"RainDeskNotify",0,winreg.REG_SZ,exe+" --notify")
    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE,r"SOFTWARE\Classes\AppUserModelId\RainDesk") as key:
        winreg.SetValueEx(key,"DisplayName",0,winreg.REG_SZ,"RainDesk 雨课堂助手")
    (DATA/"maintenance").unlink(missing_ok=True)


def stop_companions():
    # Notify logged-in sessions before replacing binaries during upgrade/uninstall.
    if DATA.is_dir():
        (DATA/"maintenance").touch()
        time.sleep(3)


def uninstall():
    stop_companions()
    stop_service()
    sc("delete",NAME,allowed=(1060,))
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key,"RainDeskNotify")
        except FileNotFoundError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE,r"SOFTWARE\Classes\AppUserModelId\RainDesk")
    except FileNotFoundError:
        pass


def show_alerts(alerts):
    # Pass data over stdin. Neither classroom names nor notification text become PowerShell code.
    script = """
    $ErrorActionPreference = 'Stop'
    [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
    [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] > $null
    $sender = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('RainDesk')
    $batch = ConvertFrom-Json ([Console]::In.ReadToEnd())
    foreach ($item in $batch) {
        $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
        $doc.LoadXml('<toast><visual><binding template="ToastGeneric"><text>RainDesk 雨课堂助手</text><text/></binding></visual></toast>')
        [void]$doc.GetElementsByTagName('text').Item(1).AppendChild($doc.CreateTextNode([string]$item.message))
        $toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
        $toast.Tag = [string]$item.id
        $sender.Show($toast)
        [Console]::WriteLine($item.id)
    }
    """
    cmd = Path(os.environ["SystemRoot"])/"System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(cmd),"-NoProfile","-NonInteractive","-EncodedCommand",
                             base64.b64encode(script.encode("utf-16le")).decode()],
                            input=json.dumps(alerts,ensure_ascii=True).encode("ascii"),capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW,timeout=20)
    delivered = {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}
    last = None
    for alert in alerts:
        if alert["id"] not in delivered:
            break
        last = alert["id"]
    return last


def notifier():
    import httpx
    mutex = win32event.CreateMutex(None,False,r"Local\RainDeskNotifications")
    if win32api.GetLastError()==183:
        return
    token = (DATA/"control.token").read_text()
    cursor = "LastAlert-"+hashlib.sha256(token.encode("ascii")).hexdigest()[:16]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r"Software\RainDesk") as prefs:
        try:
            after = winreg.QueryValueEx(prefs,cursor)[0]
        except FileNotFoundError:
            after = 0
        with httpx.Client(trust_env=False,timeout=3,headers={"Authorization":"Bearer "+token}) as http:
            while True:
                if (DATA/"maintenance").exists():
                    return
                try:
                    res = http.get(f"http://127.0.0.1:{PORT}/alerts",params={"after":after})
                    if res.status_code==401:
                        return
                    alerts = res.raise_for_status().json()
                    if alerts:
                        last = show_alerts(alerts)
                        if last is not None:
                            after = last
                            winreg.SetValueEx(prefs,cursor,0,winreg.REG_DWORD,after)
                except (httpx.HTTPError,subprocess.SubprocessError):
                    pass
                for _ in range(2):
                    if (DATA/"maintenance").exists():
                        return
                    time.sleep(1)


if __name__=="__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    args = sys.argv[1:]
    if args==["--install"]:
        install()
    elif args==["--stop"]:
        stop_companions()
        stop_service()
    elif args==["--uninstall"]:
        uninstall()
    elif args==["--notify"]:
        notifier()
    elif args and args[0]=="--foreground":
        serve(Path(args[1]) if len(args)>1 else DATA,int(args[2]) if len(args)>2 else PORT).run()
    elif not args:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(Service)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        sys.exit(2)
