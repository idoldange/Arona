@echo off
REM Chuyển đến thư mục chứa file .bat
cd /d %~dp0
REM Chạy Java UI
start "" javaw ServerUI.java
