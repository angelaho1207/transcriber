@echo off
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /setactive SCHEME_CURRENT
echo Lecture mode OFF: closing the lid will sleep the laptop again (normal behavior).
ping -n 4 127.0.0.1 >nul
