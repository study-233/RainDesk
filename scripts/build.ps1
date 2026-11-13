param([switch]$BackendOnly)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot)
$python = Join-Path (Get-Location) '.venv/Scripts/python.exe'
if (!(Test-Path $python)) { throw 'Create .venv and install backend/requirements.txt first.' }
$icon = (Resolve-Path src-tauri/icons/icon.ico).Path
& $python -m PyInstaller --noconfirm --onedir --noconsole --name RainDeskAgent --distpath src-tauri/resources --workpath .work/pyinstaller --specpath .work --paths backend --hidden-import win32timezone --hidden-import uvicorn.logging --hidden-import uvicorn.loops.asyncio --hidden-import uvicorn.protocols.http.h11_impl --hidden-import uvicorn.protocols.websockets.websockets_impl --hidden-import uvicorn.lifespan.on --collect-data pptx --icon $icon backend/agent.py
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Copy-Item -LiteralPath THIRD-PARTY.txt -Destination src-tauri/resources/RainDeskAgent/THIRD-PARTY.txt -Force
if ($BackendOnly) { exit 0 }
& npm.cmd run tauri -- build
if ($LASTEXITCODE) { exit $LASTEXITCODE }
New-Item -Force -ItemType Directory release | Out-Null
Copy-Item src-tauri/target/release/bundle/nsis/*-setup.exe release/
