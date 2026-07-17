@echo off
title Obra360 - Worker (pipeline completo: trajetoria + portas + panoramas)
color 0A

:: ===========================================================================
:: Este .bat foi feito para ser copiado para a PASTA DO VIDEO (ex.: a mesma
:: pasta de "VID_20260703_110303_00_021.mp4"). O projeto Obra360 (worker.py,
:: .env, serviceAccountKey.json) fica em outro lugar - o caminho abaixo aponta
:: pra la, nao precisa mexer.
:: ATENCAO: assume 1 video por pasta = 1 vistoria. Se tiver mais de um .mp4/.mov
:: aqui, separe cada video numa pasta diferente e rode o .bat em cada uma.
:: ===========================================================================

set PROJETO=C:\Users\HomePC\.gemini\antigravity-ide\scratch\video360-obras-app
set WORKER=%PROJETO%\worker.py

echo =======================================================
echo   Obra360 - Worker (odometria + portas + panoramas)
echo =======================================================
echo.

:: 1. Python instalado?
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado no sistema!
    echo Instale o Python e marque "Add Python to PATH" no instalador.
    echo.
    pause
    exit /b
)

:: 2. Confere se o worker.py existe no projeto (caminho fixo acima)
if not exist "%WORKER%" (
    echo [ERRO] Nao encontrei o worker.py em:
    echo   %WORKER%
    echo Confira se a pasta do projeto Obra360 mudou de lugar.
    echo.
    pause
    exit /b
)

:: 3. Instala dependencias necessarias (rapido se ja estiver tudo instalado)
echo [INFO] Verificando dependencias (pode demorar na primeira vez)...
python -m pip install --quiet firebase-admin requests opencv-python numpy pymupdf boto3 python-dotenv
echo.

:: 4. Pede o ID da vistoria (o mesmo da URL do site: /visita/ESSE_ID)
set VISITA_ID=
set /p VISITA_ID="Digite o ID da vistoria (veja na URL do site, /visita/...): "
if "%VISITA_ID%"=="" (
    echo [ERRO] ID da vistoria e' obrigatorio - a vistoria precisa existir no
    echo site com Ancora A e planta PDF ja configuradas.
    echo.
    pause
    exit /b
)

:: 5. Roda o worker para o(s) .mp4/.mov encontrado(s) nesta pasta, salvando um
::    log em arquivo (mais facil de me mandar do que copiar do terminal).
::    .mov entrou aqui pra cobrir exports em ProRes (que saem em container
::    MOV, nao MP4, do Insta360 Studio/DJI Studio) - o video_io.py do projeto
::    ja decodifica qualquer codec via ffmpeg, entao a extensao do arquivo em
::    si nao importa pro processamento, so' pro .bat conseguir ACHAR o arquivo.
set found=0
for %%f in ("%~dp0*.mp4" "%~dp0*.mov") do (
    set found=1
    echo.
    echo [PROCESSANDO] Video: "%%~nxf"
    echo Isso pode levar varios minutos - NAO feche esta janela.
    echo Log completo sendo salvo em: resultado_%%~nf.log
    echo =======================================================
    python "%WORKER%" --id %VISITA_ID% --video "%%f" 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%~dp0resultado_%%~nf.log'"
)

if %found% equ 0 (
    echo [AVISO] Nenhum arquivo .mp4 ou .mov encontrado nesta pasta.
    echo Coloque este .bat na mesma pasta do video 360 e rode de novo.
)

echo.
echo =======================================================
echo Concluido. Me manda o conteudo do arquivo resultado_*.log
echo desta mesma pasta (ou so' copia/cola o que apareceu acima).
echo =======================================================
pause
