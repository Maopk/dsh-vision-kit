@echo off
rem DSH PC Actor - thin client. Usage: act ping | act shot path=D:/tmp/a.png | act run skills\x.json
rem State (pylibs, logs, port.txt, tmp) lives in ACTOR_HOME, else in home.txt next to this file.
setlocal
rem Interpreter, best first: %ACTOR_PY%, DSH's own runtime (3.12 + numpy - the one the actor is
rem tested on), python on PATH, then the py launcher. No machine-specific path is required.
set "PY="
if defined ACTOR_PY (
  if exist "%ACTOR_PY%" ( set "PY=%ACTOR_PY%" ) else ( echo act.cmd: ACTOR_PY not found: %ACTOR_PY% & exit /b 1 )
)
if not defined PY if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY for /f "delims=" %%I in ('where python 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY (
  echo act.cmd: no python found - set ACTOR_PY to a python.exe, or put python on PATH
  exit /b 1
)
set "ACTOR_HOME="
if exist "%~dp0home.txt" set /p ACTOR_HOME=<"%~dp0home.txt"
if not defined ACTOR_HOME set "ACTOR_HOME=%~dp0"
set "ACTOR_HOME=%ACTOR_HOME:"=%"
set "PYTHONPATH=%ACTOR_HOME%\pylibs"
set "TEMP=%ACTOR_HOME%\tmp"
set "TMP=%ACTOR_HOME%\tmp"
"%PY%" "%~dp0act.py" %*
