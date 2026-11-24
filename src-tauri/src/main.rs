#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{fs, path::PathBuf};
mod desktop;

#[tauri::command]
fn control_token() -> Result<String, String> {
    let root = std::env::var_os("ProgramData").ok_or("无法定位 Windows 应用数据")?;
    fs::read_to_string(PathBuf::from(root).join("RainDesk/control.token"))
        .map_err(|_| "无法读取服务连接凭证，请检查 RainDeskAgent 服务及当前用户的安装权限".into())
}

#[tauri::command]
async fn save_export(title: String, format: String, bytes: Vec<u8>) -> Result<bool, String> {
    if !matches!(format.as_str(), "pptx" | "pdf") {
        return Err("不支持该格式".into());
    }
    let name: String = title.chars().map(|c| if c.is_alphanumeric() || "-_ ".contains(c) { c } else { '_' }).take(90).collect();
    let file = rfd::AsyncFileDialog::new().add_filter(&format, &[format.as_str()])
        .set_file_name(format!("{name}.{format}")).save_file().await;
    if let Some(file) = file {
        file.write(&bytes).await.map_err(|e| e.to_string())?;
        Ok(true)
    } else {
        Ok(false)
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            use std::os::windows::process::CommandExt;
            use tauri::Manager;
            let agent = app.path().resource_dir()?.join("agent/RainDeskAgent.exe");
            if agent.is_file() {
                let _ = std::process::Command::new(agent).arg("--notify").creation_flags(0x08000000).spawn();
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![control_token, save_export, desktop::change_service,
            desktop::check_update, desktop::open_release_page, desktop::service_status])
        .run(tauri::generate_context!())
        .expect("RainDesk window initialization failed");
}
