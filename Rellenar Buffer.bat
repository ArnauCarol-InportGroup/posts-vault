@echo off
REM Lanza el workflow "Rellenar Buffer" en GitHub y muestra su progreso.
REM Requiere GitHub CLI (gh) con sesion iniciada.
cd /d "%~dp0"
gh workflow run buffer-fill.yml || goto :error
echo Workflow lanzado. Esperando a que arranque...
timeout /t 5 /nobreak >nul
for /f %%i in ('gh run list --workflow buffer-fill.yml --limit 1 --json databaseId --jq ".[0].databaseId"') do set RUN_ID=%%i
gh run watch %RUN_ID% --exit-status
gh run view %RUN_ID% --log | findstr /C:"Queue:" /C:"Queued " /C:"Next:" /C:"Removed"
pause
exit /b 0

:error
echo No se pudo lanzar el workflow. Revisa que gh este instalado y con sesion iniciada.
pause
exit /b 1
