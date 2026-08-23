@echo off
cd /d "%~dp0"
python verify_xauusdm.py > verify_output.txt 2>&1
notepad verify_output.txt
