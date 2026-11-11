@echo off
if not "%GITSYNC_PYTHON%"=="" (
  "%GITSYNC_PYTHON%" "%~dp0askpass.py" %*
) else (
  python "%~dp0askpass.py" %*
)
