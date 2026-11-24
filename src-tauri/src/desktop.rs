use std::{os::windows::process::CommandExt, path::PathBuf, process::Command};

use semver::Version;

async fn powershell(script: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = std::env::var_os("SystemRoot").ok_or("无法定位 Windows 系统目录")?;
        let exe = PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let output = Command::new(exe)
            .args(["-NoProfile", "-NonInteractive", "-Command", &script])
            .creation_flags(0x08000000)
            .output()
            .map_err(|_| "无法启动 Windows PowerShell")?;
        match output.status.code() {
            Some(0) => Ok(String::from_utf8_lossy(&output.stdout).trim().to_string()),
            Some(1223) => Err("已取消管理员授权".into()),
            _ => Err("Windows 命令执行失败".into()),
        }
    })
    .await
    .map_err(|_| "Windows 命令执行中断".to_string())?
}

#[tauri::command]
pub async fn service_status() -> Result<String, String> {
    powershell("(Get-Service -Name RainDeskAgent -ErrorAction Stop).Status.ToString()".into()).await
}

#[tauri::command]
pub async fn change_service(start: bool) -> Result<(), String> {
    let (verb, target, already) = if start { ("start", "Running", 1056) } else { ("stop", "Stopped", 1062) };
    // Only these two fixed SCM operations run elevated. The desktop window stays unelevated.
    let script = format!(r#"
        $ErrorActionPreference = 'Stop'
        try {{
            $sc = Join-Path ([Environment]::SystemDirectory) 'sc.exe'
            $p = Start-Process -FilePath $sc -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList '{verb}','RainDeskAgent'
            if ($p.ExitCode -notin 0,{already}) {{ exit 1 }}
            (Get-Service RainDeskAgent).WaitForStatus('{target}',[TimeSpan]::FromSeconds(30))
        }} catch {{
            if ($_.Exception.NativeErrorCode -eq 1223) {{ exit 1223 }}
            exit 1
        }}
    "#);
    powershell(script).await.map(|_| ())
}

#[tauri::command]
pub async fn check_update(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let tag = powershell(r#"
        $ErrorActionPreference = 'Stop'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        (Invoke-RestMethod -Uri 'https://api.github.com/repos/study-233/RainDesk/releases/latest' -UserAgent RainDesk -TimeoutSec 15).tag_name
    "#.into()).await.map_err(|_| "无法读取 GitHub 发布信息，请检查网络后重试".to_string())?;
    let latest = Version::parse(tag.strip_prefix('v').unwrap_or(&tag))
        .map_err(|_| "发布版本号格式无效".to_string())?;
    Ok((latest.cmp_precedence(&app.package_info().version).is_gt()).then(|| latest.to_string()))
}

#[tauri::command]
pub async fn open_release_page() -> Result<(), String> {
    powershell("$ErrorActionPreference='Stop'; Start-Process 'https://github.com/study-233/RainDesk/releases/latest'".into()).await.map(|_| ())
}
