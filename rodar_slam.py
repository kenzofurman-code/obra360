# -*- coding: utf-8 -*-
# rodar_slam.py
# Orquestra o stella_vslam (via Docker, imagem "stella_vslam-socket" - ver
# OBRA360_SLAM_HANDOFF.md) para extrair a trajetoria de alta precisao (1.3%
# de erro validado) de um video 360 equiretangular, em vez da odometria leve
# (process_trajectory.py) que o worker.py usa hoje como fallback.
#
# O que este script faz:
#   1. Detecta resolucao/fps do video (cv2) e reduz para --max-lado (padrao
#      1920) via ffmpeg se for maior - resolucoes de camera 360 (5.7K, 4K)
#      degradam o tracking do stella_vslam; a comunidade recomenda 1080p-4K.
#   2. Gera um config YAML equiretangular (baseado no config validado pela
#      comunidade para Insta360 One X2 5760x2880 - mesma familia da X3 do
#      Pedro: https://github.com/stella-cv/stella_vslam/discussions/158),
#      ajustado pra resolucao final (pos-reducao) e fps do video.
#   3. Garante o vocabulario ORB (orb_vocab.fbow) localmente, baixando de
#      https://github.com/stella-cv/FBoW_orb_vocab/raw/main/orb_vocab.fbow
#      se nao existir ainda (cache ao lado deste script).
#   4. Roda o container Docker (--entrypoint bash e' OBRIGATORIO - a imagem
#      stella_vslam-socket tem ENTRYPOINT /bin/bash, ver handoff) chamando
#      run_video_slam com --viewer none --eval-log-dir (gera frame_trajectory.txt
#      em formato TUM) e, se --manter-mapa, --map-db-out (mapa.msg).
#   5. Copia frame_trajectory.txt -> --out e mapa.msg -> --mapa-out (se pedido).
#
# IMPORTANTE (regra operacional do handoff): rodar SEMPRE com --manter-mapa
# no worker.py - o mapa.msg alimenta a ferramenta de medicao (medir_panorama.py)
# e o mapa persistente (Fase 4 do roadmap).
#
# Pre-requisito: Docker Desktop instalado com a imagem construida a partir do
# Dockerfile.socket do repo stella-cv/stella_vslam:
#   git clone --recursive https://github.com/stella-cv/stella_vslam.git
#   cd stella_vslam && docker build -t stella_vslam-socket -f Dockerfile.socket .
# Se voce deu outro nome/tag a imagem, passe --docker-image <nome>.
#
# Uso (chamado pelo worker.py, mas roda sozinho tambem):
#   python rodar_slam.py --video v.mp4 --out frame_trajectory.txt \
#       --manter-mapa --mapa-out mapa.msg
#
# NAO TESTADO AINDA neste ambiente (sem Docker aqui) - validar rodando de
# verdade na maquina do Pedro (que tem Docker Desktop + imagem stella_vslam
# ja construidos) antes de confiar no resultado em producao.

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

try:
    import cv2
except ImportError:
    print("Erro: instale as dependencias: pip install opencv-python")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VOCAB_URL = "https://github.com/stella-cv/FBoW_orb_vocab/raw/main/orb_vocab.fbow"
VOCAB_CACHE = os.path.join(SCRIPT_DIR, "orb_vocab.fbow")

# Config equiretangular baseado no validado pela comunidade para Insta360 One
# X2 5760x2880 (mesma familia/resolucao da X3) - ver discussao #158 do
# stella-cv/stella_vslam. cols/rows/fps sao substituidos pelos valores reais
# (pos-reducao) do video na hora de gerar o arquivo.
CONFIG_TEMPLATE = """\
# Gerado automaticamente por rodar_slam.py - equiretangular (Insta360-like)
Camera:
  name: "Obra360 equirectangular"
  setup: "monocular"
  model: "equirectangular"
  fps: {fps:.3f}
  cols: {cols}
  rows: {rows}
  color_order: "RGB"

Tracking:
  max_num_keypoints: 5000

Feature:
  scale_factor: 1.2
  num_levels: 8
  ini_fast_threshold: 20
  min_fast_threshold: 7
  mask_rectangles:
    - [0.0, 1.0, 0.0, 0.1]
    - [0.0, 1.0, 0.84, 1.0]
    - [0.0, 0.2, 0.7, 1.0]
    - [0.8, 1.0, 0.7, 1.0]

Mapping:
  baseline_dist_thr_ratio: 0.02
"""


def probe_video(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Nao consegui abrir o video: {path}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    return w, h, fps


def reduzir_video(video_path, work_dir, max_lado, fps):
    """Reduz o video para --max-lado de largura (mantendo aspecto) via
    ffmpeg, se for maior que isso. Resolucoes de camera 360 (5.7K, 4K)
    degradam o tracking do stella_vslam - reduzir para 1080p-1920 melhora
    a estabilidade da trajetoria (ver discussao #158 do stella_vslam)."""
    w, h, _ = probe_video(video_path)
    if w <= max_lado:
        return video_path, w, h
    nova_h = int(round(h * (max_lado / w) / 2)) * 2  # par, exigido por muitos codecs
    saida = os.path.join(work_dir, "video_reduzido.mp4")
    print(f"[SLAM] Reduzindo video {w}x{h} -> {max_lado}x{nova_h} (ffmpeg)...")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf",
           f"scale={max_lado}:{nova_h}", "-c:v", "libx264", "-crf", "18",
           "-preset", "veryfast", "-an", saida]
    subprocess.run(cmd, check=True, capture_output=True)
    return saida, max_lado, nova_h


def garantir_vocab(vocab_path):
    if os.path.exists(vocab_path):
        return vocab_path
    print(f"[SLAM] Baixando vocabulario ORB (uma vez so) de {VOCAB_URL} ...")
    urllib.request.urlretrieve(VOCAB_URL, vocab_path)
    print(f"[SLAM] Vocabulario salvo em {vocab_path}")
    return vocab_path


def gerar_config(work_dir, cols, rows, fps):
    cfg_path = os.path.join(work_dir, "equirectangular.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(CONFIG_TEMPLATE.format(fps=fps, cols=cols, rows=rows))
    return cfg_path


def rodar_docker(docker_image, work_dir, video_nome, vocab_nome, cfg_nome,
                  manter_mapa, mapa_nome):
    eval_dir = "/data/eval"
    # run_video_slam nao esta no PATH da imagem - o binario fica em
    # /stella_vslam_examples/build/ (e' onde o shell da imagem abre por padrao,
    # ver "Running on Docker" da doc do stella_vslam). Entra la antes de chamar.
    cmd_interno = (
        "cd /stella_vslam_examples/build && "
        f"./run_video_slam -v /data/{vocab_nome} -c /data/{cfg_nome} "
        f"-m /data/{video_nome} --frame-skip 1 --no-sleep --viewer none "
        f"--eval-log-dir {eval_dir}"
    )
    if manter_mapa:
        cmd_interno += f" --map-db-out /data/{mapa_nome}"

    docker_cmd = [
        "docker", "run", "--rm",
        "--volume", f"{work_dir}:/data",
        "--entrypoint", "bash",  # OBRIGATORIO - imagem tem ENTRYPOINT /bin/bash
        docker_image, "-c", cmd_interno,
    ]
    print(f"[SLAM] Rodando container: {' '.join(docker_cmd)}")
    subprocess.run(docker_cmd, check=True)


def main():
    ap = argparse.ArgumentParser(
        description="Orquestra o stella_vslam via Docker para extrair trajetoria de alta precisao.")
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True, help="Caminho de saida do frame_trajectory.txt (TUM)")
    ap.add_argument("--manter-mapa", action="store_true",
                    help="Gera tambem o mapa.msg (REGRA OPERACIONAL do handoff: sempre usar).")
    ap.add_argument("--mapa-out", default=None, help="Caminho de saida do mapa.msg")
    ap.add_argument("--docker-image", default="stella_vslam-socket",
                    help="Nome/tag da imagem Docker do stella_vslam (padrao: stella_vslam-socket)")
    ap.add_argument("--max-lado", type=int, default=1920,
                    help="Largura maxima do video antes de rodar o SLAM (padrao 1920)")
    ap.add_argument("--vocab", default=VOCAB_CACHE,
                    help="Caminho do orb_vocab.fbow (baixa automaticamente se nao existir)")
    args = ap.parse_args()

    if args.manter_mapa and not args.mapa_out:
        print("[ERRO] --manter-mapa requer --mapa-out <caminho>.")
        sys.exit(1)

    if not os.path.exists(args.video):
        print(f"[ERRO] Video nao encontrado: {args.video}")
        sys.exit(1)

    if shutil.which("docker") is None:
        print("[ERRO] Docker nao encontrado no PATH. Instale o Docker Desktop e garanta "
              "que a imagem stella_vslam-socket foi construida (ver instrucoes no topo deste arquivo).")
        sys.exit(1)

    vocab_path = garantir_vocab(args.vocab)

    work_dir = tempfile.mkdtemp(prefix="rodar_slam_")
    try:
        _, _, fps_original = probe_video(args.video)
        video_final, cols, rows = reduzir_video(args.video, work_dir, args.max_lado, fps_original)

        video_nome = "video_slam.mp4"
        shutil.copy(video_final, os.path.join(work_dir, video_nome))
        vocab_nome = "orb_vocab.fbow"
        shutil.copy(vocab_path, os.path.join(work_dir, vocab_nome))

        cfg_path = gerar_config(work_dir, cols, rows, fps_original)
        cfg_nome = os.path.basename(cfg_path)

        mapa_nome = "mapa.msg"
        os.makedirs(os.path.join(work_dir, "eval"), exist_ok=True)

        rodar_docker(args.docker_image, work_dir, video_nome, vocab_nome,
                     cfg_nome, args.manter_mapa, mapa_nome)

        traj_gerada = os.path.join(work_dir, "eval", "frame_trajectory.txt")
        if not os.path.exists(traj_gerada):
            print("[ERRO] stella_vslam rodou mas nao gerou frame_trajectory.txt "
                  "em eval/ - confira o log do container acima (tracking pode ter falhado).")
            sys.exit(1)
        shutil.copy(traj_gerada, args.out)
        print(f"[SLAM] Trajetoria salva em: {args.out}")

        if args.manter_mapa:
            mapa_gerado = os.path.join(work_dir, mapa_nome)
            if os.path.exists(mapa_gerado):
                shutil.copy(mapa_gerado, args.mapa_out)
                print(f"[SLAM] Mapa salvo em: {args.mapa_out}")
            else:
                print("[AVISO] --manter-mapa pedido mas mapa.msg nao foi gerado pelo container.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
