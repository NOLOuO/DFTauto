@echo off
setlocal
call D:\miniconda3\Scripts\activate.bat
call conda activate p312env
python "%~dp0launch_gui.py"
endlocal
