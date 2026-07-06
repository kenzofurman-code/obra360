@echo off
title Processador de Trajetoria - Obra360
color 0A

echo =======================================================
echo          Obra360 - Processador de Trajetoria
echo =======================================================
echo.

:: 1. Verifica se o Python esta instalado
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado no sistema!
    echo Por favor, instale o Python e marque a opcao "Add Python to PATH" no instalador.
    echo.
    pause
    exit /b
)

:: 2. Instala dependencias necessarias caso nao existam
echo [INFO] Verificando e instalando dependencias (opencv-python, numpy)...
python -m pip install opencv-python numpy --quiet
if %errorlevel% neq 0 (
    echo [AVISO] Falha ao rodar o pip. Certifique-se de estar conectado a internet.
)
echo.

:: 3. Procura por arquivos MP4 na mesma pasta do arquivo .bat
set found=0
for %%f in ("%~dp0*.mp4") do (
    set found=1
    echo [PROCESSANDO] Encontrado video: "%%~nxf"
    echo Processando a odometria visual... Aguarde, isso pode levar alguns minutos...
    
    python "%~dp0process_trajectory.py" --video "%%f" --out "%~dp0%%~nf_caminho.json"
    
    if %errorlevel% equ 0 (
        echo.
        echo [SUCESSO] Trajetoria gerada: "%%~nf_caminho.json"
    ) else (
        echo.
        echo [FALHA] Erro ao processar o video "%%~nxf"
    )
    echo =======================================================
)

if %found% equ 0 (
    echo [AVISO] Nenhum arquivo de video .mp4 foi encontrado nesta pasta.
    echo.
    echo Como usar:
    echo 1. Coloque este arquivo 'processar.bat' e o script 'process_trajectory.py' na mesma pasta onde esta o seu video .mp4 de 360 graus.
    echo 2. Clique duas vezes neste arquivo 'processar.bat' para rodar.
)

echo.
echo Processamento concluido!
pause
