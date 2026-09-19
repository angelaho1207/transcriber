Set objShell = CreateObject("WScript.Shell")
proj = "C:\Users\angel\OneDrive\Desktop\transcriber\transcriber"
objShell.CurrentDirectory = proj
objShell.Run """" & proj & "\.venv\Scripts\python.exe"" ""app.py""", 0, False
