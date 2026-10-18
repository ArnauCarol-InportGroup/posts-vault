@echo off
REM Cifra las carpetas de posts locales y sube los cambios a GitHub.
REM Usalo despues de anadir, editar o borrar posts.
cd /d "%~dp0"
git pull --rebase || goto :error
python tools\vault.py encrypt --prune || goto :error
git add vault
git diff --cached --quiet && (echo No hay cambios que subir. & pause & exit /b 0)
git commit -m "chore: update vault" || goto :error
git push || goto :error
echo Posts sincronizados.
pause
exit /b 0

:error
echo Algo ha fallado. Revisa el mensaje de arriba.
pause
exit /b 1
