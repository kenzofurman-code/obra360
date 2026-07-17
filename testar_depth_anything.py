# -*- coding: utf-8 -*-
# testar_depth_anything.py
# Teste REAL (nao so' revisao de codigo) da ideia discutida com o Pedro em
# 2026-07-16: usar Depth-Anything V2 Small como alternativa/fallback ao
# RANSAC de landmarks (medir_ponto_robusto, ver CLAUDE.md item 16) nos casos
# onde o mapa SLAM esta esparso demais e a medicao atual falha (ex.: as
# janelas do quadro_0080.jpg, que hoje dao sucesso=False com dispersao 3.36
# e 28.43).
#
# IMPORTANTE - por que este script NAO foi rodado de ponta a ponta neste
# sandbox: o ambiente de desenvolvimento usado nesta sessao bloqueia
# huggingface.co (e cdn-lfs, modelscope, gitee, sourceforge - todos os hosts
# que hospedam os pesos do modelo), entao nao consegui baixar o checkpoint
# aqui. So' pypi.org/files.pythonhosted.org e github.com (paginas HTML, nao
# os assets de release) estao liberados neste sandbox. A parte de RECORTE
# EQUIRETANGULAR -> PERSPECTIVA (equirect_perspectiva.py) FOI testada de
# verdade contra o quadro_0080.jpg real (linhas retas do teto/parede saem
# retas no recorte, sem distorcao equiretangular) - so' a parte que baixa e
# roda o modelo de profundidade em si precisa ser rodada na sua maquina, que
# tem internet normal.
#
# COMO RODAR (na sua maquina, Pedro):
#   pip install torch transformers pillow numpy opencv-python
#   python testar_depth_anything.py --foto "teste medição e resolucao/quadro_0080.jpg" \
#       --u-centro 0.5 --v-centro 0.5 --fov 100 --tamanho 1200 \
#       --ponto1 400,250 --ponto2 900,250
#   (os --ponto1/--ponto2 sao pixels DENTRO do recorte salvo, nao da foto
#   360 inteira - o script salva o recorte em disco pra voce abrir, escolher
#   os 2 pixels visualmente, e rodar de novo com as coordenadas certas -
#   mesmo fluxo de "olhar o grid e escolher o pixel" que ja usamos nos
#   testes anteriores do RANSAC, ver quadro80_janelas_grid.jpg)
#
# Checkpoint: depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf -
# CONFIRMADO 2026-07-16 rodando na maquina do Pedro (meu primeiro palpite,
# "Metric-Hypersim-Small-hf", nao existe - deu RepositoryNotFoundError; o
# nome certo na versao -hf/transformers e' "Metric-Indoor-Small", nao
# "Hypersim-Small" - "Hypersim" e' soh o nome do dataset sintetico usado
# pra treinar a variante INDOOR, nao faz parte do model id). Existe tambem
# um "Metric-Outdoor-Small-hf" (treinado em Virtual KITTI/direcao) - nao
# usar pro nosso caso (obra em construcao e' ambiente fechado).
#
# O QUE ESTE SCRIPT NAO FAZ (de proposito, primeira iteracao): nao usa
# pose_raw nem tenta combinar profundidade de 2 fotos diferentes - os 2
# pontos clicados precisam estar visiveis no MESMO recorte (mesma foto).
# Pra medicoes tipicas de vistoria (largura de porta/janela/rachadura) isso
# cobre a maioria dos casos - ver comentario "simplificacao" abaixo.

import argparse
import json

import cv2
import numpy as np

from equirect_perspectiva import recortar_perspectiva


def carregar_pipeline_profundidade(model_id):
    """Import lazy de torch/transformers - so' precisa disso se este script
    for de fato executado (mesmo espirito do import lazy de scipy em
    carregar_poses_tum e de upscale_quadros em gerar_quadros.py)."""
    from transformers import pipeline
    print(f"[testar_depth_anything] Carregando modelo {model_id} (1a vez baixa da internet)...")
    return pipeline(task="depth-estimation", model=model_id)


def estimar_profundidade(pipe, crop_bgr):
    """Roda o pipeline de profundidade no recorte e devolve um array (H, W)
    float, JA redimensionado de volta pro tamanho exato do recorte (alguns
    modelos redimensionam a entrada internamente pra um multiplo de 14/16
    pixels - preciso alinhar de volta pra indexar pelo MESMO pixel clicado)."""
    from PIL import Image
    H, W = crop_bgr.shape[:2]
    img_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    saida = pipe(pil_img)
    depth = np.array(saida["predicted_depth"] if "predicted_depth" in saida else saida["depth"])
    if depth.shape[:2] != (H, W):
        depth = cv2.resize(depth.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
    return depth


def salvar_visualizacao_profundidade(depth, caminho_saida):
    """Colormap so' pra inspecao visual (perto/longe faz sentido?) - NAO e'
    validacao numerica, so' um smoke test rapido antes de confiar nos
    numeros (mesmo espirito do 'olha o crop antes de confiar' do resto do
    projeto)."""
    norm = (depth - depth.min()) / max(1e-6, (depth.max() - depth.min()))
    color = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    cv2.imwrite(caminho_saida, color)


def retroprojetar(px, py, K, profundidade):
    """(pixel, profundidade) -> ponto 3D no espaco da PROPRIA camera do
    recorte. Sinal de X/Y nao importa pra distancia entre 2 pontos DA MESMA
    camera (so' precisa ser consistente entre os 2 pontos, o que e' - usa a
    mesma K/profundidade pros dois)."""
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    z = float(profundidade)
    x = (px - cx) / fx * z
    y = (py - cy) / fy * z
    return np.array([x, y, z])


def amostrar_profundidade(depth, px, py):
    """Bilinear simples - evita pegar exatamente um pixel de borda/ruido
    isolado no ponto clicado."""
    H, W = depth.shape
    x0, y0 = int(np.floor(px)), int(np.floor(py))
    x1, y1 = min(x0 + 1, W - 1), min(y0 + 1, H - 1)
    x0, y0 = max(0, x0), max(0, y0)
    fx, fy = px - x0, py - y0
    d00, d10 = depth[y0, x0], depth[y0, x1]
    d01, d11 = depth[y1, x0], depth[y1, x1]
    d0 = d00 * (1 - fx) + d10 * fx
    d1 = d01 * (1 - fx) + d11 * fx
    return d0 * (1 - fy) + d1 * fy


def main():
    ap = argparse.ArgumentParser(
        description="Testa Depth-Anything V2 como alternativa ao RANSAC de landmarks pra medicao.")
    ap.add_argument('--foto', required=True, help="Foto equiretangular (ex.: quadro_0080.jpg)")
    ap.add_argument('--u-centro', type=float, default=0.5)
    ap.add_argument('--v-centro', type=float, default=0.5)
    ap.add_argument('--fov', type=float, default=90.0, help="FOV horizontal do recorte, em graus")
    ap.add_argument('--tamanho', type=int, default=800)
    ap.add_argument('--modelo', default='depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf',
                     help="Model id no Hugging Face - confirmado 2026-07-16 (o palpite anterior, "
                          "'Metric-Hypersim-Small-hf', nao existe - o nome certo na versao "
                          "transformers e' 'Metric-Indoor-Small-hf')")
    ap.add_argument('--ponto1', help="px,py dentro do RECORTE (nao da foto 360 inteira). Se omitido, so' salva o recorte + depth map pra voce escolher.")
    ap.add_argument('--ponto2', help="px,py dentro do RECORTE, segundo ponto")
    ap.add_argument('--distancia-real-m', type=float, default=None,
                     help="Se voce souber a distancia real (ex.: largura de uma porta com trena), informe aqui pra comparar o erro")
    ap.add_argument('--saida-prefixo', default='depth_test')
    args = ap.parse_args()

    img = cv2.imread(args.foto)
    if img is None:
        raise SystemExit(f"Nao consegui abrir {args.foto}")

    crop, K = recortar_perspectiva(img, args.u_centro, args.v_centro, args.fov, args.tamanho)
    caminho_crop = f"{args.saida_prefixo}_recorte.jpg"
    cv2.imwrite(caminho_crop, crop)
    print(f"Recorte salvo em {caminho_crop} (abra pra escolher os 2 pixels de --ponto1/--ponto2)")

    if not args.ponto1 or not args.ponto2:
        print("Rode de novo passando --ponto1 px,py --ponto2 px,py (coordenadas do recorte acima).")
        return

    pipe = carregar_pipeline_profundidade(args.modelo)
    depth = estimar_profundidade(pipe, crop)

    caminho_depth_vis = f"{args.saida_prefixo}_depth.png"
    salvar_visualizacao_profundidade(depth, caminho_depth_vis)
    print(f"Visualizacao da profundidade salva em {caminho_depth_vis} - confira se perto/longe faz sentido antes de confiar no numero.")

    p1x, p1y = map(float, args.ponto1.split(','))
    p2x, p2y = map(float, args.ponto2.split(','))

    d1 = amostrar_profundidade(depth, p1x, p1y)
    d2 = amostrar_profundidade(depth, p2x, p2y)
    P1 = retroprojetar(p1x, p1y, K, d1)
    P2 = retroprojetar(p2x, p2y, K, d2)
    dist = float(np.linalg.norm(P1 - P2))

    print(json.dumps({
        "ponto1": {"px": p1x, "py": p1y, "profundidade": float(d1)},
        "ponto2": {"px": p2x, "py": p2y, "profundidade": float(d2)},
        "distancia_estimada": dist,
    }, indent=2, ensure_ascii=False))

    if args.distancia_real_m is not None:
        erro_pct = abs(dist - args.distancia_real_m) / args.distancia_real_m * 100
        print(f"Distancia real informada: {args.distancia_real_m}m | estimada: {dist:.4f} (unidade do checkpoint - "
              f"se for a versao metric, deveria ser metros) | erro: {erro_pct:.1f}%")


if __name__ == '__main__':
    main()
