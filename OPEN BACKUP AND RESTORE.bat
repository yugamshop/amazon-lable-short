@echo off
cd /d "%~dp0"
echo Opening Backup and Restore. LabelDesk must be running from start.bat.
start "" "http://localhost:8765/backup?v=backup-4"
