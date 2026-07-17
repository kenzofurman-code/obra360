# -*- coding: utf-8 -*-
# calibrar_altura_camera.py
# Calibracao automatica da escala do SLAM (unidades SLAM -> metros) usando a
# altura REAL da camera acima do piso (constante fisica do rig, ex.: 2.0m
# medida com trena), em vez de depender de largura de porta/janela "de
# projeto" (que segundo o Pedro costuma divergir do que foi realmente
# construido - 2026-07-16).
#
# Ideia: o mapa.msg do stella_vslam ja tem a nuvem de pontos 3D (landmarks)
# inteira, incluindo os pontos do PISO. Ajustando um plano por RANSAC
# restrito a normais quase-verticais (pra nao confundir com parede) entre os
# landmarks mais baixos de toda a nuvem, acha-se o piso em unidades SLAM. A
# distancia perpendicular mediana da trajetoria da camera ate esse plano,
# dividida pela altura REAL conhecida do rig, da' a escala SLAM->metros - de
# forma automatica, sem clicar em porta nenhuma e sem depender de nenhuma
# medida "de projeto".
#
# Vantagem sobre calibrar_por_portas() (processar_vistoria.py/worker.py):
# nao depende de detectar cruzamento de porta (que pode falhar) nem de
# confiar que a largura da porta na planta bate com a da obra - so' depende
# da altura do rig, que e' uma constante conhecida e fixa do equipamento.
#
# Uso: python calibrar_altura_camera.py --mapa mapa.msg --altura-camera-m 2.0

import argparse
import json

import numpy as np

from medir_panorama import carregar_mapa


def ajustar_plano_horizontal_ransac(pontos, iters=2000, limiar=0.08, min_inliers=30,
                                     tolerancia_normal_graus=20.0, seed=0):
    """
    Igual ao espirito de ajustar_plano_ransac() em medir_panorama.py, mas
    RESTRITO a planos quase-horizontais (normal perto do eixo vertical) -
    necessario pra nao confundir piso com parede numa nuvem de pontos que
    tem as duas coisas. O eixo vertical (qual das 3 colunas de 'pontos' e'
    "pra cima") precisa ser descoberto ANTES de chamar isso - ver
    detectar_eixo_vertical().

    Retorna (ponto_no_plano, normal_unitaria_apontando_pra_cima, n_inliers)
    ou (None, None, None) se nao achou suporte suficiente.
    """
    n = len(pontos)
    if n < 3:
        return None, None, None
    rng = np.random.default_rng(seed)
    cos_min = np.cos(np.radians(tolerancia_normal_graus))
    vertical = np.array([0.0, 1.0, 0.0])

    melhor_inliers = None
    melhor_n = -1
    for _ in range(iters):
        idx = rng.choice(n, size=3, replace=False)
        p0, p1, p2 = pontos[idx]
        normal = np.cross(p1 - p0, p2 - p0)
        norma = np.linalg.norm(normal)
        if norma < 1e-9:
            continue
        normal = normal / norma
        if abs(normal @ vertical) < cos_min:
            continue
        dist = np.abs((pontos - p0) @ normal)
        inliers = dist < limiar
        if inliers.sum() > melhor_n:
            melhor_n = int(inliers.sum())
            melhor_inliers = inliers
    if melhor_inliers is None or melhor_n < min_inliers:
        return None, None, None
    pts_in = pontos[melhor_inliers]
    centro = pts_in.mean(axis=0)
    _, _, Vt = np.linalg.svd(pts_in - centro)
    normal_final = Vt[-1]
    normal_final = normal_final / np.linalg.norm(normal_final)
    if normal_final @ vertical < 0:
        normal_final = -normal_final
    return centro, normal_final, melhor_n


def detectar_eixo_vertical(cam_pos):
    """O eixo vertical e' o de MENOR variancia na posicao da camera (o
    operador caminha bastante em 2 eixos horizontais mas fica numa faixa
    estreita de altura o tempo todo). Retorna o indice (0=x, 1=y, 2=z)."""
    variancias = cam_pos.var(axis=0)
    return int(np.argmin(variancias))


def calibrar_por_altura_camera(caminho_mapa, altura_camera_m, limiar_plano=0.08,
                                 min_inliers=30, percentil_abaixo=15.0):
    keyframes, _, landmarks_pos = carregar_mapa(caminho_mapa)
    cam_pos = np.array([kf["pos_w"] for kf in keyframes.values()])

    eixo_vertical = detectar_eixo_vertical(cam_pos)
    nomes = ['x', 'y', 'z']
    print(f"[calibrar_altura_camera] Eixo vertical detectado: '{nomes[eixo_vertical]}' "
          f"(menor variancia na trajetoria da camera)")
    if eixo_vertical != 1:
        ordem = [i for i in range(3) if i != eixo_vertical] + [eixo_vertical]
        cam_pos = cam_pos[:, ordem]
        landmarks_pos = landmarks_pos[:, ordem]
        print("[calibrar_altura_camera] Reordenando eixos pra manter y=vertical internamente.")

    # candidatos a piso: os landmarks mais BAIXOS de TODOS (percentil dos
    # proprios landmarks, NAO da altura da camera - bug encontrado e
    # corrigido em 2026-07-16: um corte relativo a' altura da camera com
    # margem pequena pegava so' parede/rodape perto da altura do operador,
    # nao o piso de verdade, porque paredes tem MUITO mais pontos SLAM que
    # o piso (concreto liso da pouca textura pro ORB) - isso inflava a
    # escala calculada.
    corte_y = np.percentile(landmarks_pos[:, 1], percentil_abaixo)
    candidatos = landmarks_pos[landmarks_pos[:, 1] < corte_y]
    print(f"[calibrar_altura_camera] {len(candidatos)}/{len(landmarks_pos)} landmarks "
          f"abaixo do percentil {percentil_abaixo} (y<{corte_y:.3f}) - candidatos a piso")

    centro_plano, normal_plano, n_inliers = ajustar_plano_horizontal_ransac(
        candidatos, limiar=limiar_plano, min_inliers=min_inliers)
    if centro_plano is None:
        raise RuntimeError("Nao achei um plano horizontal com suporte suficiente - "
                            "tente afrouxar --limiar-plano ou --percentil-abaixo")
    print(f"[calibrar_altura_camera] Plano do piso ajustado com {n_inliers} landmarks "
          f"de suporte, normal={normal_plano}")

    distancias = (cam_pos - centro_plano) @ normal_plano
    dist_mediana = float(np.median(distancias))
    dist_media = float(np.mean(distancias))
    dist_std = float(np.std(distancias))
    print(f"[calibrar_altura_camera] Altura da camera acima do piso (unidades SLAM): "
          f"mediana={dist_mediana:.4f} media={dist_media:.4f} desvio={dist_std:.4f} "
          f"({len(distancias)} keyframes)")

    escala = altura_camera_m / dist_mediana
    return {
        "escala_slam_metros": escala,
        "altura_slam_mediana": dist_mediana,
        "altura_slam_media": dist_media,
        "altura_slam_desvio": dist_std,
        "n_landmarks_piso": n_inliers,
        "n_keyframes": len(cam_pos),
    }


def calibrar_por_altura_camera_robusto(caminho_mapa, altura_camera_m,
                                        percentis=(10.0, 15.0, 20.0, 25.0),
                                        tolerancia_escala_pct=8.0):
    """
    Roda calibrar_por_altura_camera() com varios cortes de percentil
    diferentes e SO' aceita o resultado se as escalas convergirem entre si -
    mesmo espirito do medir_ponto_robusto() (varias tentativas, so' confia
    se concordarem).
    """
    resultados = []
    for p in percentis:
        try:
            r = calibrar_por_altura_camera(caminho_mapa, altura_camera_m, percentil_abaixo=p)
            r["percentil"] = p
            resultados.append(r)
        except RuntimeError as e:
            resultados.append({"percentil": p, "erro": str(e)})

    escalas_ok = [r["escala_slam_metros"] for r in resultados if "escala_slam_metros" in r]
    if len(escalas_ok) < 2:
        return {"sucesso": False, "motivo": "Menos de 2 cortes deram resultado - sem base pra comparar.",
                "tentativas": resultados}

    escala_mediana = float(np.median(escalas_ok))
    dispersao_pct = float((max(escalas_ok) - min(escalas_ok)) / escala_mediana * 100)
    sucesso = dispersao_pct <= tolerancia_escala_pct
    return {
        "sucesso": sucesso,
        "escala_slam_metros": escala_mediana if sucesso else None,
        "dispersao_pct": dispersao_pct,
        "escalas_por_percentil": {str(r["percentil"]): r.get("escala_slam_metros") for r in resultados},
        "motivo": None if sucesso else (
            f"Escalas variaram {dispersao_pct:.1f}% entre os cortes testados (tolerancia "
            f"{tolerancia_escala_pct}%) - resultado nao confiavel, provavelmente pegando "
            f"parede/rodape em vez do piso em algum dos cortes. Confirme visualmente onde "
            f"esta' o piso nesta vistoria antes de usar esta calibracao."
        ),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Calibra escala_slam_metros usando a altura real da camera acima do piso.")
    ap.add_argument('--mapa', required=True)
    ap.add_argument('--altura-camera-m', type=float, required=True)
    ap.add_argument('--limiar-plano', type=float, default=0.08)
    ap.add_argument('--min-inliers', type=int, default=30)
    ap.add_argument('--percentil-abaixo', type=float, default=None,
                     help="Se informado, roda so' um corte (modo antigo/debug). Se omitido, "
                          "roda o modo robusto (varios cortes, so' aceita se convergirem).")
    args = ap.parse_args()

    if args.percentil_abaixo is not None:
        r = calibrar_por_altura_camera(args.mapa, args.altura_camera_m,
                                         args.limiar_plano, args.min_inliers, args.percentil_abaixo)
    else:
        r = calibrar_por_altura_camera_robusto(args.mapa, args.altura_camera_m)
    print(json.dumps(r, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
