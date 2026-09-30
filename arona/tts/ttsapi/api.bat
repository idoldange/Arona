chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
set PATH=%~dp0runtime;%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem
set PYTHONUTF8=1
runtime\python.exe -I api_v2.py --bind_addr 0.0.0.0 --device cpu %*
pause
