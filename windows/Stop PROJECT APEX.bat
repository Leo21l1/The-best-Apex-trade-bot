@echo off
title Stop PROJECT APEX
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-apex.ps1"
