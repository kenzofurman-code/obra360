# -*- coding: utf-8 -*-
# super_resolucao.py
# Super-resolucao multi-frame SOB DEMANDA: dado um clique numa foto do tour
# (um "quadro" ja extraido por gerar_quadros.py), reprojeta o recorte daquele
# ponto em TODOS os outros quadros da MESMA vistoria onde ele e' visivel,
# alinha sub-pixel e funde numa versao em resolucao mais alta do que
# qualquer quadro individual.
#
# Ideia original do Pedro (2026-07-12, ver OBRA360_ROADMAP.md Fase 4 item 7).
#
# REDESENHO (2026-07-15, mesmo dia, apos pergunta do Pedro): a 1a versao
# deste arquivo reprojetava no VIDEO BRUTO original (via frame_trajectory.txt
# + ffmpeg seek), pra aproveitar a alta densidade de frames do video (um
# inspetor gera dezenas de frames levemente deslocados passando por qualquer
# ponto). Pedro perguntou "porque eu preciso do video se eu já tenho os
# frames e é nos frames que temos a posição exata?" - a resposta honesta foi
# que os QUADROS (fotos do tour) sao ESPARSOS (1 por parada, amostragem por
# distancia percorrida - ver gerar_quadros.py), entao raramente tem mais de
# 1-2 observacoes do MESMO ponto. Mas evitar depender do video bruto elimina
# de vez o problema de "precisa guardar 46.8GB por vistoria pra sempre" -
# entao a troca faz sentido: aceitar um ganho menor (e dependente da
# geometria de cada vistoria - corredores com varias paradas seguidas dao
# bastante diversidade de angulo; ambientes vistos de uma unica parada nao
# dao NADA pra fundir) em troca de nao precisar do video bruto NUNCA MAIS
# depois do processamento.
#
# O que isso exigiu (ver gerar_quadros.py e worker.py, mesmo commit):
#   - gerar_quadros.py ganhou --traj-completa (opcional): le
#     frame_trajectory.txt (pose POR FRAME do SLAM, tem rotacao - ao
#     contrario do waypoints_corrigidos.json 2D usado pra amostrar as
#     paradas) e anexa pose_raw (posicao+quaternion BRUTOS do SLAM, mesma
#     escala/eixo de mapa.msg) a cada quadro do manifest.json, casando pelo
#     frame EXATO que foi escolhido (mais nitido da janela), nao o alvo
#     original da amostragem.
#   - worker.py passa frame_trajectory.txt (traj_tum) pra gerar_quadros.py
#     sempre que o SLAM rodou com sucesso.
#   - Retrocompatibilidade: vistorias antigas (sem --traj-completa na epoca,
#     ou sem SLAM - fallback de odometria leve) simplesmente nao tem
#     pose_raw nos quadros -> super-resolucao fica indisponivel pra elas,
#     sem quebrar o resto (a manifest.json inteira continua igual).
#
# mapa.msg AINDA e' necessario, mas so' pelos LANDMARKS (nuvem de pontos 3D
# do SLAM) - pra achar o ponto 3D clicado via RANSAC de plano local (mesma
# geometria de medir_panorama.py, reaproveitada sem duplicar). As poses dos
# KEYFRAMES de mapa.msg nao sao mais usadas aqui - a pose de origem do raio
# agora vem do proprio quadro clicado (pose_raw do manifest).
#
# ATENCAO - feature EXPLORATORIA, ainda NAO validada em campo (so' testada
# com dados sinteticos neste ambiente - sem Docker/stella_vslam disponivel
# aqui):
#   - Ganho esperado ~2-4x NOS PONTOS QUE TEM 2+ quadros vendo-os de angulos
#     diferentes - NAO "zoom infinito", e nem todo ponto vai ter esse
#     material disponivel (ver aviso de escassez acima). Pontos vistos por
#     1 so' quadro simplesmente nao tem o que fundir.
#   - Reusa a MESMA geometria de medir_panorama.py (raio_do_clique,
#     landmarks, plano local por RANSAC) pra achar o ponto 3D clicado.
#
# Requisitos: pip install msgpack scipy opencv-python numpy
#
# Uso (id do quadro clicado = campo "id" do manifest.json, u/v = clique
# normalizado 0..1 na propria foto do quadro):
#   python super_resolucao.py --quadros pasta_quadros/ --mapa mapa.msg \
#       --clique 12,0.481,0.552 --saida superres.png

import argparse
import json
import math
import os
import time

import cv2
import numpy as np

try:
    from scipy.spatial.transform import Rotation as Rot
except ImportError:
    print("[ERRO] Pacote 'scipy' nao instalado. Execute: pip install scipy")
    raise

from medir_panorama import (
    carregar_mapa,
    raio_do_clique,
    landmarks_proximos_ao_raio,
    ajustar_plano_ransac,
    intersectar_raio_plano,
    medir_ponto_robusto,
    medir_por_reprojecao,
    pose_no_frame_do_mapa,
)


def poses_no_frame_do_mapa(quadros_por_id, keyframes):
    """FIX 2026-07-17 (item 21 do CLAUDE.md): pose_raw (frame_trajectory)
    esta num referencial DIFERENTE do mapa.msg (~180 graus + translacao).
    Esta funcao devolve {quadro_id: pose} com as poses interpoladas dos
    KEYFRAMES DO PROPRIO MAPA (via campo 't' de cada quadro) - o referencial
    certo pra combinar com os landmarks. Quadros em trechos sem cobertura de
    keyframes ficam de fora do dict (sem pose confiavel)."""
    poses = {}
    for qid, q in quadros_por_id.items():
        if q.get("t") is None:
            continue
        pose = pose_no_frame_do_mapa(keyframes, float(q["t"]))
        if pose is not None:
            poses[qid] = pose
    return poses


# ─── Manifest (quadros do tour, com pose_raw opcional por quadro) ───────────

def carregar_manifest(pasta_quadros):
    """Le manifest.json da pasta de quadros (mesmo arquivo que Visita.jsx
    consome). Retorna (doc, quadros_por_id)."""
    caminho = os.path.join(pasta_quadros, "manifest.json")
    with open(caminho, "r", encoding="utf-8") as f:
        doc = json.load(f)
    quadros_por_id = {q["id"]: q for q in doc.get("quadros", [])}
    return doc, quadros_por_id


def quadro_para_pose(quadro):
    """Converte o pose_raw salvo no manifest (pos_w + quat_wc) pra um dict
    {pos_w, rot_wc} compativel com raio_do_clique()/reprojetar_ponto() -
    MESMO formato usado pelos keyframes de medir_panorama.py::carregar_mapa,
    so' que a fonte aqui e' o proprio quadro (gerar_quadros.py), nao
    mapa.msg. Retorna None se o quadro nao tiver pose_raw (vistoria antiga,
    ou processada sem SLAM/--traj-completa - super-resolucao indisponivel
    pra esse quadro especifico)."""
    pr = quadro.get("pose_raw")
    if not pr:
        return None
    pos_w = np.array(pr["pos_w"], dtype=float)
    rot_wc = Rot.from_quat(pr["quat_wc"]).as_matrix()
    return dict(pos_w=pos_w, rot_wc=rot_wc)


# ─── Reprojecao: ponto 3D -> (u, v) num quadro qualquer (inverso do clique) ─

def reprojetar_ponto(pos_w, rot_wc, ponto3d):
    """
    Inverso de medir_panorama.py::raio_do_clique(): dado um ponto 3D do mundo
    e uma pose (pos_w, rot_wc), retorna (u, v, dist) - a coordenada
    equiretangular ONDE aquele ponto aparece nessa pose, e a distancia
    camera->ponto (usada depois pra filtrar quadros longe/perto demais).

    Convencao inversa da direta (ver raio_do_clique):
      dir_cam = [cos(lat)sin(lon), sin(lat), cos(lat)cos(lon)]
      => lat = asin(dir_cam[1]) ; lon = atan2(dir_cam[0], dir_cam[2])
      => u = lon/(2pi) + 0.5 ; v = 0.5 - lat/pi
    Retorna None so' por seguranca numerica (ponto exatamente na posicao da
    camera).
    """
    delta = ponto3d - pos_w
    dist = float(np.linalg.norm(delta))
    if dist < 1e-6:
        return None
    dir_cam = rot_wc.T @ (delta / dist)
    lat = math.asin(max(-1.0, min(1.0, float(dir_cam[1]))))
    lon = math.atan2(float(dir_cam[0]), float(dir_cam[2]))
    u = (lon / (2.0 * math.pi) + 0.5) % 1.0
    v = 0.5 - lat / math.pi
    return u, v, dist


# ─── Selecao de quadros candidatos pra fusao ────────────────────────────────

def selecionar_quadros_candidatos(quadros_por_id, ponto3d, quadro_clicado_id,
                                   dist_min=0.3, dist_max=8.0,
                                   intervalo_min_s=0.0, max_frames=12,
                                   poses_por_id=None):
    """
    Reprojeta o ponto 3D em CADA quadro que tenha pose_raw, filtra por
    distancia camera->ponto plausivel e prioriza os mais PROXIMOS (melhor
    resolucao efetiva por pixel). O quadro clicado e' SEMPRE incluido como
    ancora (referencia do alinhamento em alinhar_e_fundir), mesmo que nao
    seja o mais proximo.

    intervalo_min_s default 0.0 (praticamente desligado): ao contrario da
    1a versao (que usava o video bruto - dezenas de frames quase identicos
    por segundo, precisava forcar espacamento), aqui os quadros JA SAO
    esparsos por natureza (1 por parada) - normalmente nao ha' risco de
    "gastar o orcamento com quadros quase identicos". Mantido como parametro
    (nao removido) pra casos de vistorias com paradas muito proximas.

    Retorna lista de dicts {id, arquivo, u, v, dist}, com o quadro-ancora
    sempre em 1o lugar.
    """
    def _pose(qid, quadro):
        # poses_por_id (frame do MAPA, fix item 21) tem prioridade; fallback
        # legado pose_raw so' se o dict nao for passado.
        if poses_por_id is not None:
            return poses_por_id.get(qid)
        return quadro_para_pose(quadro)

    candidatos = []
    for qid, quadro in quadros_por_id.items():
        pose = _pose(qid, quadro)
        if pose is None:
            continue
        r = reprojetar_ponto(pose["pos_w"], pose["rot_wc"], ponto3d)
        if r is None:
            continue
        u, v, dist = r
        if dist_min <= dist <= dist_max:
            candidatos.append(dict(id=qid, arquivo=quadro["arquivo"],
                                    t=quadro.get("t", 0.0), u=u, v=v, dist=dist))
    if not candidatos:
        return []

    ancora = next((c for c in candidatos if c["id"] == quadro_clicado_id), None)
    if ancora is None:
        # o proprio quadro clicado nao tem pose_raw ou nao passou no filtro de
        # distancia (raro - o ponto foi calculado a partir DELE) - inclui
        # mesmo assim, sem o filtro de distancia, pra garantir que a ancora
        # do alinhamento exista.
        quadro_click = quadros_por_id.get(quadro_clicado_id)
        pose_click = _pose(quadro_clicado_id, quadro_click) if quadro_click else None
        if pose_click is not None:
            r = reprojetar_ponto(pose_click["pos_w"], pose_click["rot_wc"], ponto3d)
            if r is not None:
                u, v, dist = r
                ancora = dict(id=quadro_clicado_id, arquivo=quadro_click["arquivo"],
                              t=quadro_click.get("t", 0.0), u=u, v=v, dist=dist)

    candidatos_ordenados = sorted(candidatos, key=lambda c: c["dist"])
    escolhidos = [ancora] if ancora is not None else []
    for c in candidatos_ordenados:
        if ancora is not None and c["id"] == ancora["id"]:
            continue
        if all(abs(c["t"] - e["t"]) >= intervalo_min_s for e in escolhidos):
            escolhidos.append(c)
        if len(escolhidos) >= max_frames:
            break

    # ancora sempre em 1o lugar - alinhar_e_fundir() usa o primeiro da lista
    # como referencia do ECC.
    if ancora is not None:
        escolhidos.sort(key=lambda c: 0 if c["id"] == ancora["id"] else 1)
    return escolhidos


# ─── Extracao de recorte a partir da FOTO do quadro (nao do video) ──────────

def extrair_recorte(pasta_quadros, arquivo, u, v, raio_px):
    """
    Extrai um recorte quadrado (2*raio_px de lado) centrado em (u,v) da
    propria imagem do quadro (JPEG/WebP ja gerado por gerar_quadros.py) -
    NAO precisa mais abrir o video bruto (ver redesenho no topo do arquivo).

    Padding horizontal por wrap-around: o panorama "da a volta" em u=0/1
    (mesma costura leste-oeste de um mapa-mundi) - concatena uma faixa da
    borda oposta antes de recortar, pra um recorte que cruze essa costura
    nao perder pixels de um dos lados.
    """
    caminho = os.path.join(pasta_quadros, arquivo)
    frame = cv2.imread(caminho, cv2.IMREAD_COLOR)
    if frame is None:
        print(f"[SuperRes] Nao consegui ler {caminho}")
        return None
    H, W = frame.shape[:2]
    pad = raio_px + 2
    frame_pad = np.hstack([frame[:, -pad:], frame, frame[:, :pad]])
    cx = int(round(u * W)) + pad
    cy = int(round(max(0.0, min(1.0, v)) * H))
    y0, y1 = max(0, cy - raio_px), min(H, cy + raio_px)
    x0, x1 = cx - raio_px, cx + raio_px
    recorte = frame_pad[y0:y1, x0:x1]
    if recorte.size == 0:
        return None
    return recorte.copy()


# ─── Alinhamento sub-pixel + fusao ponderada por nitidez ────────────────────

def _nitidez(img_bgr):
    cinza = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return max(float(cv2.Laplacian(cinza, cv2.CV_64F).var()), 1.0)


def alinhar_e_fundir(recortes, fator_upscale=3):
    """
    Alinha sub-pixel os recortes contra o primeiro (ancora, ja' upscalado)
    via ECC (cv2.findTransformECC, MOTION_TRANSLATION - permite deslocamento
    FRACIONARIO de pixel) e funde por MEDIA ponderada pela nitidez de cada
    recorte alinhado (Laplaciano - mesma metrica que gerar_quadros.py ja usa
    pra escolher o frame mais nitido de cada parada). Recortes que falham no
    alinhamento (pouca textura/sobreposicao insuficiente) sao descartados da
    fusao, nao da lista de recortes originais.
    Retorna (imagem_fundida, n_frames_usados_na_fusao).
    """
    if not recortes:
        raise ValueError("Nenhum recorte pra fundir.")
    ancora = recortes[0]
    h, w = ancora.shape[:2]
    if h == 0 or w == 0:
        raise ValueError("Recorte-ancora vazio (0px) - clique muito perto da borda do quadro?")
    Hg, Wg = h * fator_upscale, w * fator_upscale

    ancora_up = cv2.resize(ancora, (Wg, Hg), interpolation=cv2.INTER_CUBIC)
    ancora_cinza = cv2.cvtColor(ancora_up, cv2.COLOR_BGR2GRAY).astype(np.float32)

    acumulado = ancora_up.astype(np.float64) * _nitidez(ancora_up)
    peso_total = _nitidez(ancora_up)
    usados = 1

    criterios = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-4)
    for recorte in recortes[1:]:
        if recorte.shape[:2] != (h, w):
            recorte = cv2.resize(recorte, (w, h), interpolation=cv2.INTER_AREA)
        recorte_up = cv2.resize(recorte, (Wg, Hg), interpolation=cv2.INTER_CUBIC)
        cinza = cv2.cvtColor(recorte_up, cv2.COLOR_BGR2GRAY).astype(np.float32)
        warp = np.eye(2, 3, dtype=np.float32)
        try:
            _, warp = cv2.findTransformECC(ancora_cinza, cinza, warp,
                                            cv2.MOTION_TRANSLATION, criterios)
        except cv2.error:
            continue  # alinhamento falhou nesse quadro - descarta so' ele
        alinhado = cv2.warpAffine(recorte_up, warp, (Wg, Hg),
                                   flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
        peso = _nitidez(alinhado)
        acumulado += alinhado.astype(np.float64) * peso
        peso_total += peso
        usados += 1

    fundido = np.clip(acumulado / max(peso_total, 1e-6), 0, 255).astype(np.uint8)
    return fundido, usados


# ─── Pipeline completo (1 clique -> imagem fundida) ─────────────────────────

def super_resolver(pasta_quadros, mapa_path, clique, raio_px=64,
                    fator_upscale=3, raio_camera_min=0.3, raio_camera_max=8.0,
                    intervalo_min_s=0.0, max_frames=12, t_max=15.0,
                    dist_max_landmark=0.5, k_max=60, limiar_plano=0.02,
                    min_inliers=8, tolerancia_consistencia=0.15):
    quadro_id, u0, v0 = clique
    doc, quadros_por_id = carregar_manifest(pasta_quadros)
    if quadro_id not in quadros_por_id:
        raise ValueError(f"Quadro {quadro_id} nao encontrado no manifest.json "
                          f"({len(quadros_por_id)} quadros disponiveis).")
    quadro_clicado = quadros_por_id[quadro_id]

    # FIX 2026-07-17 (item 21): poses no frame do MAPA (keyframes do proprio
    # mapa.msg via 't'), nao mais pose_raw (frame da trajetoria, referencial
    # diferente do mapa).
    keyframes, _, landmarks_pos = carregar_mapa(mapa_path)
    poses_por_id = poses_no_frame_do_mapa(quadros_por_id, keyframes)
    pose_clicada = poses_por_id.get(quadro_id)
    if pose_clicada is None:
        raise RuntimeError(
            f"Quadro {quadro_id} sem pose no frame do mapa - ou a vistoria foi "
            "processada sem SLAM (sem keyframes), ou o instante t desse quadro "
            "esta num trecho sem cobertura de keyframes do mapa.msg.")

    # 1. Clique -> ponto 3D (MESMA geometria de medir_panorama.py, so' que a
    # pose de origem do raio agora vem do PROPRIO quadro, nao de um keyframe
    # de mapa.msg).
    #
    # Achado 2026-07-16 (Pedro, 1o teste real de medir_panorama.py): o
    # RANSAC de plano local as vezes "acha" um plano ERRADO dependendo do
    # raio de busca (t_max/dist_max) escolhido - o mesmo clique pode dar
    # pontos 3D bem diferentes conforme esses parametros mudam, silenciosamente,
    # sem erro nenhum. Isso motivou medir_ponto_robusto() (ver medir_panorama.py):
    # roda o RANSAC com varias combinacoes e so' aceita se convergirem entre
    # si. super_resolucao.py tinha a MESMA vulnerabilidade (usava o RANSAC
    # single-shot direto) - trocado aqui pra usar medir_ponto_robusto tambem,
    # em vez de arriscar fundir quadros ancorados num ponto 3D errado.
    resultado = medir_por_reprojecao(pose_clicada, u0, v0, landmarks_pos)
    if not resultado["sucesso"]:
        raise RuntimeError(
            "Nao foi possivel achar um ponto 3D confiavel pra esse clique: "
            f"{resultado['motivo']} (mesma checagem de consistencia do "
            "medir_panorama.py --robusto). Tente clicar mais perto de uma "
            "quina, movel ou textura visivel.")
    ponto3d = resultado["ponto3d"]
    print(f"[SuperRes] Ponto 3D estimado: {np.round(ponto3d, 4).tolist()} "
          f"(confianca={resultado['confianca']}, dispersao={resultado['dispersao']:.4f}, "
          f"{resultado['n_landmarks']} landmarks reprojetados perto do clique)")

    # 2. Selecao de quadros candidatos (reusa as fotos ja extraidas - sem
    # video, sem ffmpeg seek)
    escolhidos = selecionar_quadros_candidatos(
        quadros_por_id, ponto3d, quadro_id, dist_min=raio_camera_min,
        dist_max=raio_camera_max, intervalo_min_s=intervalo_min_s,
        max_frames=max_frames, poses_por_id=poses_por_id)
    if len(escolhidos) < 2:
        print("[AVISO] Menos de 2 quadros candidatos validos - esta vistoria/ponto "
              "nao tem material suficiente pra fusao real (o ponto so' foi visto "
              "de perto por 1 parada). O resultado equivale a so' upscalar a foto "
              "clicada, sem ganho real de super-resolucao.")

    n_com_pose = sum(1 for q in quadros_por_id.values() if q.get("pose_raw"))
    print(f"[SuperRes] {len(escolhidos)} quadro(s) candidato(s) selecionado(s) "
          f"(de {n_com_pose}/{len(quadros_por_id)} quadros com pose_raw nesta vistoria):")
    for c in escolhidos:
        print(f"  quadro={c['id']:4d}  t={c['t']:7.1f}s  uv=({c['u']:.3f},{c['v']:.3f})  "
              f"dist_camera={c['dist']:.2f}")

    # 3. Extrai o recorte de cada quadro candidato (das proprias fotos, nao do video)
    recortes = []
    for c in escolhidos:
        r = extrair_recorte(pasta_quadros, c["arquivo"], c["u"], c["v"], raio_px)
        if r is not None and r.size > 0:
            recortes.append(r)
    if not recortes:
        raise RuntimeError("Nenhum recorte extraido com sucesso das fotos dos quadros.")

    # 4. Alinha sub-pixel e funde
    fundido, usados = alinhar_e_fundir(recortes, fator_upscale=fator_upscale)
    print(f"[SuperRes] Fusao concluida: {usados}/{len(recortes)} quadros alinhados com "
          f"sucesso | resolucao final: {fundido.shape[1]}x{fundido.shape[0]} "
          f"({fator_upscale}x o recorte original de {2*raio_px}x{2*raio_px}px)")

    # Comparacao lado a lado (upscale simples do clique original vs. fundido) -
    # nao prometer "zoom infinito" sem prova visual do ganho real.
    ancora_upscale = cv2.resize(recortes[0], (fundido.shape[1], fundido.shape[0]),
                                interpolation=cv2.INTER_CUBIC)
    comparacao = np.hstack([ancora_upscale, fundido])
    return fundido, comparacao, len(recortes), usados


def parse_clique(s):
    try:
        quadro_id, u, v = s.split(",")
        return int(quadro_id), float(u), float(v)
    except Exception:
        raise argparse.ArgumentTypeError(
            "Use o formato quadro_id,u,v  (ex.: 12,0.53,0.61) - quadro_id e' o "
            "campo 'id' do manifest.json, u/v e' o clique normalizado 0..1 na foto.")


def main():
    ap = argparse.ArgumentParser(
        description="Super-resolucao multi-frame SOB DEMANDA a partir de um clique "
                     "numa foto do tour (Obra360) - reprojeta nos OUTROS quadros da "
                     "mesma vistoria, sem depender do video bruto.")
    ap.add_argument("--quadros", required=True,
                    help="Pasta com as fotos do tour + manifest.json (saida do gerar_quadros.py).")
    ap.add_argument("--mapa", required=True, help="mapa.msg do stella_vslam (landmarks).")
    ap.add_argument("--clique", type=parse_clique, required=True,
                    help="quadro_id,u,v do ponto clicado (id do manifest.json).")
    ap.add_argument("--saida", default="superres.png")
    ap.add_argument("--comparacao-saida", default="superres_comparacao.png",
                    help="Lado a lado: upscale simples (esquerda) vs. fundido (direita).")
    ap.add_argument("--raio-px", type=int, default=64, help="Meia-largura do recorte, em pixels da foto.")
    ap.add_argument("--upscale", type=int, default=3, help="Fator de ampliacao antes da fusao.")
    ap.add_argument("--raio-camera-min", type=float, default=0.3,
                    help="Distancia MINIMA camera-ponto (unid. SLAM) pra aceitar um quadro candidato.")
    ap.add_argument("--raio-camera-max", type=float, default=8.0,
                    help="Distancia MAXIMA camera-ponto (unid. SLAM) pra aceitar um quadro candidato.")
    ap.add_argument("--intervalo-min", type=float, default=0.0,
                    help="Espacamento minimo (s) entre quadros candidatos escolhidos (normalmente "
                         "desnecessario - quadros ja sao esparsos por natureza).")
    ap.add_argument("--max-frames", type=int, default=12, help="Maximo de quadros usados na fusao.")
    ap.add_argument("--t-max", type=float, default=15.0,
                    help="(nao usado mais na busca do ponto 3D - medir_ponto_robusto() testa "
                         "varias combinacoes de t_max/dist_max internamente, ver medir_panorama.py. "
                         "Mantido so' pra nao quebrar chamadas existentes.)")
    ap.add_argument("--dist-max-landmark", type=float, default=0.5,
                    help="(idem --t-max - nao usado mais, ver medir_ponto_robusto.)")
    ap.add_argument("--k-max", type=int, default=60, help="Maximo de landmarks usados no ajuste do plano local.")
    ap.add_argument("--limiar-plano", type=float, default=0.02, help="Tolerancia do RANSAC do plano local (unid. SLAM).")
    ap.add_argument("--min-inliers", type=int, default=8, help="Minimo de landmarks inliers pra aceitar o plano local.")
    ap.add_argument("--tolerancia-consistencia", type=float, default=0.15,
                    help="Dispersao maxima aceitavel entre as combinacoes de busca do RANSAC "
                         "(medir_ponto_robusto) - unid. SLAM. Se ultrapassar, aborta em vez de "
                         "arriscar fundir num ponto 3D errado.")
    args = ap.parse_args()

    t0 = time.time()
    fundido, comparacao, n_recortes, n_usados = super_resolver(
        args.quadros, args.mapa, args.clique,
        raio_px=args.raio_px, fator_upscale=args.upscale,
        raio_camera_min=args.raio_camera_min, raio_camera_max=args.raio_camera_max,
        intervalo_min_s=args.intervalo_min, max_frames=args.max_frames,
        t_max=args.t_max, dist_max_landmark=args.dist_max_landmark,
        k_max=args.k_max, limiar_plano=args.limiar_plano, min_inliers=args.min_inliers,
        tolerancia_consistencia=args.tolerancia_consistencia)

    cv2.imwrite(args.saida, fundido)
    cv2.imwrite(args.comparacao_saida, comparacao)
    print(f"[SuperRes] Salvo: {args.saida} (so' o resultado) e {args.comparacao_saida} "
          f"(comparacao lado a lado: upscale simples vs. fundido)")
    print(f"[TIMING] super_resolucao.py TOTAL: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
