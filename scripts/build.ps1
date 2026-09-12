param(
    [Parameter(Mandatory=$true)][string]$InnoCompiler,
    [string]$PythonExecutable = ".venv/Scripts/python.exe"
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
& $PythonExecutable -m pip install -r packaging/requirements-ocr-win.txt
if ($LASTEXITCODE -ne 0) { throw 'OCR 執行環境安裝失敗。' }
& $PythonExecutable -m pytest -q
if ($LASTEXITCODE -ne 0) { throw '測試未通過，停止建置。' }
& $PythonExecutable -m PyInstaller --clean --noconfirm packaging/pdf_editor.spec
if ($LASTEXITCODE -ne 0) { throw '應用程式封裝失敗。' }
& $InnoCompiler packaging/installer.iss
if ($LASTEXITCODE -ne 0) { throw '安裝包建置失敗。' }
