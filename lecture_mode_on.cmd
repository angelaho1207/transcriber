@echo off
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
echo Lecture mode ON: closing the lid will NOT put the laptop to sleep.
echo Run "Lecture Mode OFF" when you're done to restore normal sleep behavior.
ping -n 4 127.0.0.1 >nul
