@echo off
rem DSH PC Actor - thin client. Usage: act ping | act shot path=D:/tmp/a.png | act run skills\x.json
rem State (pylibs, logs, port.txt, tmp) lives in ACTOR_HOME, else in home.txt next to this file.
setlocal
set "PY=C:\Users\28794\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
set "ACTOR_HOME="
if exist "%~dp0home.txt" set /p ACTOR_HOME=<"%~dp0home.txt"
if not defined ACTOR_HOME set "ACTOR_HOME=%~dp0"
set "ACTOR_HOME=%ACTOR_HOME:"=%"
set "PYTHONPATH=%ACTOR_HOME%\pylibs"
set "TEMP=%ACTOR_HOME%\tmp"
set "TMP=%ACTOR_HOME%\tmp"
"%PY%" "%~dp0act.py" %*
