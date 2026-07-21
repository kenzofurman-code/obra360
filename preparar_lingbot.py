# -*- coding: utf-8 -*-
# preparar_lingbot.py
# Converte um TRECHO do video 360 equiretangular do Obra360 numa sequencia de
# frames PINHOLE (camera normal) "olhando pra frente", pra alimentar o
# LingBot-Map (github.com/Robbyant/lingbot-map) - que e' treinado em camera
# normal, nao 360. Teste de 2026-07-21 (ver CLAUDE.md): avaliar se o
# LingBot-Map rastreia o corredor sem derivar onde o stella_vslam se perdeu.
#
# Reusa a MESMA geometria de recortar_perspectiva() de equirect_perspectiva.py
# (convencao (u,v)->(lon,lat) identica ao resto do pipeline). Recorte frontal
# por padrao (u=0.5 = direcao de frente da gravacao).
#
# Uso tipico (roda na maquina do Pedro, com a 1080):
#   python preparar_lingbot.py --video corredor.mp4 --out frames_lingbot \
#       --inicio 0 --duracao 30 --passo 2 --fov 90 --tamanho 640
#
# Depois, no repo do LingBot-Map:
#   python demo.py --image_folder /caminho/frames_lingbot \
#       --model_path lingbot-map-long.pt --use_sdpa
#
# Dicas de memoria (GTX 1080, 8GB): comece com um TRECHO curto (--duracao 20-40)
# e --passo 2 (metade dos frames). O modelo degrada acima de 320 views no cache
# - pra trechos longos, no demo.py use --mode windowed --keyframe_interval 2.

import argparse
import math
import os
import sys

import cv2
import numpy as np

try:
    from equirect_perspectiva import recortar_perspectiva
except ImportError:
    print("[ERRO] equirect_perspectiva.py precisa estar na mesma pasta.")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(
        description="Extrai frames pinhole frontais de um video 360 pro LingBot-Map.")
    ap.add_argument("--video", required=True, help="Video 360 equiretangular (mp4).")
    ap.add_argument("--out", required=True, help="Pasta de saida dos frames pinhole.")
    ap.add_argument("--inicio", type=float, default=0.0, help="Segundo inicial do trecho.")
    ap.add_argument("--duracao", type=float, default=30.0,
                    help="Duracao do trecho em segundos (padrao 30 - comece curto na 1080).")
    ap.add_argument("--passo", type=int, default=2,
                    help="Pega 1 a cada N frames (padrao 2 - reduz memoria/tempo).")
    ap.add_argument("--fov", type=float, default=90.0, help="FOV horizontal do recorte (graus).")
    ap.add_argument("--tamanho", type=int, default=640, help="Largura/altura do frame de saida (px).")
    ap.add_argument("--u-centro", type=float, default=0.5,
                    help="Direcao horizontal do recorte (0.5 = frente da gravacao).")
    ap.add_argument("--v-centro", type=float, default=0.5, help="Direcao vertical (0.5 = horizonte).")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"[ERRO] Nao consegui abrir {args.video}. Se for ProRes/codec exotico, "
              "reencode antes: ffmpeg -i entrada -c:v libx264 -crf 18 corredor.mp4")
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    f_ini = int(args.inicio * fps)
    f_fim = int((args.inicio + args.duracao) * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, f_ini)
    print(f"[Prep] {args.video} @ {fps:.1f}fps | frames {f_ini}..{f_fim} passo {args.passo} "
          f"| recorte {args.tamanho}px fov {args.fov} (u={args.u_centro} v={args.v_centro})")

    fidx, salvos = f_ini, 0
    while fidx < f_fim:
        ret, frame = cap.read()
        if not ret:
            break
        if (fidx - f_ini) % args.passo == 0:
            crop, _ = recortar_perspectiva(frame, args.u_centro, args.v_centro,
                                           args.fov, args.tamanho)
            cv2.imwrite(os.path.join(args.out, f"frame_{salvos:05d}.jpg"), crop,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            salvos += 1
            if salvos % 50 == 0:
                print(f"  {salvos} frames extraidos...")
        fidx += 1
    cap.release()
    print(f"[Prep] Pronto: {salvos} frames pinhole em '{args.out}'. "
          f"Rode o LingBot-Map com --image_folder {args.out}")


if __name__ == "__main__":
    main()
