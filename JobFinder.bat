@echo off
rem Menu do Job Finder: de dois cliques neste arquivo (ou no atalho da Area de Trabalho).
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Job Finder

:menu
cls
echo.
echo  ==============================
echo           JOB FINDER
echo  ==============================
echo.
echo   1. Buscar vagas novas
echo   2. Abrir relatorio de vagas
echo   3. Candidatar - assistido (voce confirma cada envio)
echo   4. Candidatar - automatico (envia onde e permitido)
echo   5. Simular candidatura (nao envia nada)
echo   6. Ver meu perfil
echo   7. Refazer meu perfil (curriculo + entrevista)
echo   0. Sair
echo.
set "op="
set /p op=  Escolha uma opcao:

if "%op%"=="1" (python -m jobfinder search & call :sync "Busca de vagas")
if "%op%"=="2" python -m jobfinder report --open
if "%op%"=="3" (python -m jobfinder apply --mode assisted & call :sync "Candidaturas")
if "%op%"=="4" (python -m jobfinder apply --mode auto & call :sync "Candidaturas")
if "%op%"=="5" python -m jobfinder apply --dry-run
if "%op%"=="6" python -m jobfinder profile
if "%op%"=="7" (python -m jobfinder setup & call :sync "Perfil atualizado")
if "%op%"=="0" exit /b
echo.
pause
goto menu

rem Salva vagas/perfil/candidaturas no GitHub (repositorio privado).
:sync
git add -A >nul 2>&1
git commit -q -m "%~1 (%date% %time:~0,5%)" >nul 2>&1
git push -q >nul 2>&1
exit /b
