@echo off
setlocal
cd /d "C:\arona"

echo [%date% %time%] Checking for Git repository changes...

:: Check for changes before adding everything
git status --porcelain | findstr /r "^" >nul
if %errorlevel% neq 0 (
    echo [%date% %time%] No changes detected. Nothing to commit.
    echo Waiting 30 seconds before exiting...
    timeout /t 30
    exit /b
)

echo [%date% %time%] Changes detected. Adding and committing files...
git add .
git commit -m "Auto-sync: %date% %time%"

echo [%date% %time%] Pushing changes to origin main (force)...
git push origin main --force

if %errorlevel% equ 0 (
    echo [%date% %time%] Auto-sync completed successfully!
) else (
    echo [%date% %time%] An error occurred during push!
)

echo Waiting 30 seconds before exiting...
timeout /t 30