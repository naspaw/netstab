# Builds the portable app dist\netstab.exe (one file, nothing to install).
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -r requirements-dev.txt
python -m pytest -q
python tools\build_exe.py
