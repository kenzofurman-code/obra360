# -*- coding: utf-8 -*-
# equirect_perspectiva.py
# Extrai um recorte PERSPECTIVA (pinhole, retilineo) de uma foto equiretangular
# 360 - necessario porque modelos de profundidade monocular tipo Depth-Anything
# V2 foram treinados em fotos de camera normal (pinhole), nao em equiretangular
# (que tem distorcao forte, principalmente perto dos polos). Ver discussao com
# o Pedro em 2026-07-16 sobre testar Depth-Anything V2 Small como alternativa/
# fallback ao RANSAC de landmarks (medir_ponto_robusto) nas regioes onde o
# mapa esta esparso demais (ver item 16 do CLAUDE.md).
#
# Convencao de (u, v) IDENTICA a raio_do_clique() em medir_panorama.py -
# reusa a MESMA formula, pra nao criar mais uma convencao divergente em cima
# das que ja existem (ver aviso sobre o eixo v do UV do three.js em
# PanoramaViewer.jsx, ainda nao confirmado):
#   longitude (azimute) = (u - 0.5) * 2*pi   (u=0.5 -> direcao de frente)
#   latitude  (elevacao) = (0.5 - v) * pi     (v=0 -> topo / +90 graus)
#
# Uso tipico (ver testar_depth_anything.py):
#   crop, K = recortar_perspectiva(img_equirect, u_centro=0.35, v_centro=0.45,
#                                   fov_h_graus=90, largura_saida=800)
#   ... roda um modelo de profundidade no `crop` usando os intrinsecos `K` ...

import math

import cv2
import numpy as np


def _dir_cam(lon, lat):
    """Mesma formula de raio_do_clique() em medir_panorama.py - direcao 3D
    unitaria (x, y, z) a partir de longitude/latitude, SEM aplicar rotacao
    de pose nenhuma (aqui trabalhamos so' dentro do proprio quadro, nao
    precisamos do mundo)."""
    return np.array([
        math.cos(lat) * math.sin(lon),
        math.sin(lat),
        math.cos(lat) * math.cos(lon),
    ])


def uv_para_lonlat(u, v):
    lon = (u - 0.5) * 2.0 * math.pi
    lat = (0.5 - v) * math.pi
    return lon, lat


def lonlat_para_uv(lon, lat):
    u = lon / (2.0 * math.pi) + 0.5
    v = 0.5 - lat / math.pi
    return u, v


def recortar_perspectiva(img_equirect, u_centro, v_centro, fov_h_graus=90.0,
                          largura_saida=800, altura_saida=None):
    """
    Gera um recorte PINHOLE (retilineo) da foto equiretangular, centrado na
    direcao (u_centro, v_centro) - MESMA convencao de raio_do_clique().

    Retorna (crop_bgr, K) onde K e' a matriz intrinseca 3x3 do recorte
    (fx, fy, cx, cy) - necessaria pra depois converter (pixel, profundidade)
    de volta em ponto 3D no espaco da propria camera (ver back-projecao em
    testar_depth_anything.py).

    Implementacao: pra cada pixel de SAIDA, calcula o raio 3D pinhole
    correspondente (base local forward/right/up alinhada ao centro do
    recorte), converte esse raio de volta pra (lon, lat) com a formula
    INVERSA de _dir_cam(), acha o (u, v) equiretangular correspondente e
    faz remap bilinear na imagem de entrada. E' o inverso exato do que
    dir_cam()/raio_do_clique() fazem no sentido contrario.
    """
    if altura_saida is None:
        altura_saida = largura_saida  # quadrado por padrao - suficiente pra um recorte de teste

    H, W = img_equirect.shape[:2]

    fov_h = math.radians(fov_h_graus)
    f = (largura_saida / 2.0) / math.tan(fov_h / 2.0)
    cx = largura_saida / 2.0
    cy = altura_saida / 2.0
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]], dtype=float)

    lon_c, lat_c = uv_para_lonlat(u_centro, v_centro)
    forward = _dir_cam(lon_c, lat_c)
    forward /= np.linalg.norm(forward)

    # base local (right/up) ortonormal em torno de 'forward' - degenera perto
    # dos polos (lat proximo de +-90 graus), mas pra cliques de medicao em
    # paredes/janelas/portas isso na pratica nunca acontece (a camera do
    # Insta360 fica proxima da horizontal durante a caminhada).
    mundo_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, mundo_up)
    norma_right = np.linalg.norm(right)
    if norma_right < 1e-6:
        # 'forward' quase vertical (raro em pratica) - usa um up alternativo
        mundo_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, mundo_up)
        norma_right = np.linalg.norm(right)
    right /= norma_right
    up = np.cross(right, forward)
    up /= np.linalg.norm(up)

    # grade de pixels de saida -> raio pinhole local -> raio no espaco do
    # equiretangular (combinacao linear da base right/up/forward)
    xs = np.arange(largura_saida)
    ys = np.arange(altura_saida)
    px, py = np.meshgrid(xs, ys)  # (altura_saida, largura_saida)

    x_local = (px - cx) / f
    y_local = -(py - cy) / f  # imagem cresce pra baixo; 'up' deve crescer pra cima

    # raio (nao normalizado) = x_local*right + y_local*up + 1*forward
    rays = (x_local[..., None] * right + y_local[..., None] * up + forward)
    rays /= np.linalg.norm(rays, axis=-1, keepdims=True)

    lat = np.arcsin(np.clip(rays[..., 1], -1.0, 1.0))
    lon = np.arctan2(rays[..., 0], rays[..., 2])

    u = lon / (2.0 * math.pi) + 0.5
    v = 0.5 - lat / math.pi
    u = np.mod(u, 1.0)  # wrap horizontal (longitude e' ciclica)
    v = np.clip(v, 0.0, 1.0)

    map_x = (u * W).astype(np.float32)
    map_y = (v * H).astype(np.float32)

    crop = cv2.remap(img_equirect, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_REFLECT)
    return crop, K


def pixel_crop_para_uv_equirect(px, py, K, u_centro, v_centro):
    """Inverso: dado um pixel (px, py) DENTRO do recorte perspectiva e a
    mesma (u_centro, v_centro)/K usadas pra gera-lo, devolve o (u, v)
    equiretangular original correspondente - util pra cross-checar o mesmo
    ponto contra medir_ponto_robusto() (RANSAC/landmarks) no pipeline
    existente, comparando as duas abordagens no MESMO ponto exato."""
    f = K[0, 0]
    cx, cy = K[0, 2], K[1, 2]

    lon_c, lat_c = uv_para_lonlat(u_centro, v_centro)
    forward = _dir_cam(lon_c, lat_c)
    forward /= np.linalg.norm(forward)
    mundo_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, mundo_up)
    if np.linalg.norm(right) < 1e-6:
        mundo_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, mundo_up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    up /= np.linalg.norm(up)

    x_local = (px - cx) / f
    y_local = -(py - cy) / f
    ray = x_local * right + y_local * up + forward
    ray /= np.linalg.norm(ray)

    lat = math.asin(max(-1.0, min(1.0, ray[1])))
    lon = math.atan2(ray[0], ray[2])
    return lonlat_para_uv(lon, lat)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description="Testa recortar_perspectiva() gerando um crop retilineo de uma foto equiretangular.")
    ap.add_argument('--foto', required=True)
    ap.add_argument('--u-centro', type=float, required=True)
    ap.add_argument('--v-centro', type=float, required=True)
    ap.add_argument('--fov', type=float, default=90.0)
    ap.add_argument('--tamanho', type=int, default=800)
    ap.add_argument('--saida', default='recorte_perspectiva.jpg')
    args = ap.parse_args()

    img = cv2.imread(args.foto)
    if img is None:
        raise SystemExit(f"Nao consegui abrir {args.foto}")
    crop, K = recortar_perspectiva(img, args.u_centro, args.v_centro, args.fov, args.tamanho)
    cv2.imwrite(args.saida, crop)
    print(f"Recorte salvo em {args.saida} - K=\n{K}")
