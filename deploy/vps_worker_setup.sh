#!/usr/bin/env bash
# Setup do worker do Obra360 numa VPS Ubuntu (testado pensando em Ubuntu 22/24
# com Docker ja instalado pelo Coolify). Rodar como root, com o repo clonado
# em /opt/obra360:
#   bash /opt/obra360/deploy/vps_worker_setup.sh
set -euo pipefail
cd /opt/obra360

echo "== 1/4 pacotes do sistema (ffmpeg, python, libs) =="
apt-get update -q
apt-get install -y -q ffmpeg python3-venv python3-pip git libgl1 libglib2.0-0

echo "== 2/4 ambiente python (.venv) + dependencias =="
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -q -r deploy/requirements_worker.txt

echo "== 3/4 servico systemd (nao inicia ainda) =="
cp deploy/obra360-worker.service /etc/systemd/system/
systemctl daemon-reload

echo "== 4/4 checagens =="
FALTA=0
if [ ! -f serviceAccountKey.json ]; then echo "[FALTA] serviceAccountKey.json na raiz de /opt/obra360 (copie da sua maquina via scp)"; FALTA=1; fi
if [ ! -f .env ]; then echo "[FALTA] .env com R2_BUCKET_NAME/R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY (copie via scp)"; FALTA=1; fi
if ! command -v docker >/dev/null; then echo "[FALTA] docker nao encontrado"; FALTA=1; fi
if ! docker image inspect stella_vslam-socket >/dev/null 2>&1; then
  echo "[FALTA] imagem docker stella_vslam-socket - sem ela o worker cai no fallback de"
  echo "        odometria (bem menos preciso e SEM mapa.msg/medicao). Pra construir:"
  echo "        git clone --recursive https://github.com/stella-cv/stella_vslam.git /opt/stella_vslam"
  echo "        cd /opt/stella_vslam && docker build -t stella_vslam-socket -f Dockerfile.socket ."
  FALTA=1
fi
echo "espaco em disco (videos de vistoria podem ter dezenas de GB):"; df -h / | tail -1
if [ "$FALTA" = "0" ]; then
  echo "Tudo pronto. Ligue o worker com:  systemctl enable --now obra360-worker"
  echo "Logs ao vivo:                     journalctl -u obra360-worker -f"
else
  echo "Resolva os [FALTA] acima e rode este script de novo."
fi
