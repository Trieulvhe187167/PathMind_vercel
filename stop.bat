@echo off
rem ==== Stop PathMind dev servers (ports 8001 and 3782) ====
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8001 .*LISTENING"') do taskkill /PID %%p /T /F
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":3782 .*LISTENING"') do taskkill /PID %%p /T /F
echo PathMind stopped.
