# -*- coding: utf-8 -*-
# medir_panorama.py
# Ferramenta de medicao de distancia real (em metros) entre 2 pontos clicados
# num panorama gerado pelo Obra360, usando a nuvem de landmarks 3D do
# stella_vslam (mapa.msg) como referencia geometrica. Feature nova do backlog
# do handoff (item 3): "2 cliques, 1 imagem".
#
# Pipeline por clique: pixel equiretangular (u,v) do keyframe -> raio 3D no
# espaco do mundo -> landmarks proximos ao raio -> ajuste de plano local
# (RANSAC) -> intersecao raio x plano -> ponto 3D. Distancia final = norma da
# diferenca entre os 2 pontos, multiplicada pela escala SLAM->metros.
#
# ATENCAO - feature nova, AINDA NAO validada em campo:
#   - A convencao de eixo/sinal usada em raio_do_clique() (pixel -> direcao
#     equiretangular) e' a convencao padrao, mas precisa ser confirmada na
#     pratica: clique no centro exato de um quadro e confira que o raio
#     aponta para a direcao de caminhada daquele keyframe (mesma checagem de
#     espelhamento que os scripts de trajetoria ja fazem). NAO confiar no
#     numero em producao antes desse teste.
#   - Nao existe escala SLAM->metros pronta. Ela e' derivada calibrando com
#     uma porta PM (80cm de projeto) - ver --calibrar-largura abaixo. Guarde
#     o resultado (escala_mapa.json) e reuse nas medicoes seguintes do MESMO
#     mapa (a escala e' por mapa, nao e' universal).
#   - O fallback por curva epipolar (para regioes com poucos landmarks perto
#     do clique) esta descrito no handoff mas NAO implementado aqui - ver
#     medir_por_epipolar_fallback().
#
# Requisitos: pip install msgpack scipy numpy
#
# Uso - calibrar escala clicando nos 2 lados de uma porta PM conhecida (80cm):
#   python medir_panorama.py --mapa mapa.msg \
#       --ponto1 120,0.53,0.61 --ponto2 120,0.47,0.60 \
#       --calibrar-largura 0.80 --escala-out escala_mapa.json
#
# Uso - medir usando escala ja calibrada:
#   python medir_panorama.py --mapa mapa.msg \
#       --ponto1 340,0.40,0.55 --ponto2 340,0.60,0.55 \
#       --escala-in escala_mapa.json
#
# Uso - nivel simples (altura ate o piso a partir da altura do bastao, sem
# mapa nem landmarks - so trigonometria):
#   python medir_panorama.py --piso --altura-bastao 1.20 --elevacao-graus -18.4

import argparse
import json
import math
import os
import sys

import numpy as np

try:
    import msgpack
except ImportError:
    print("[ERRO] Pacote 'msgpack' nao instalado. Execute: pip install msgpack")
    raise

try:
    from scipy.spatial.transform import Rotation as Rot
except ImportError:
    print("[ERRO] Pacote 'scipy' nao instalado. Execute: pip install scipy")
    raise


# ─── Carregamento do mapa (mapa.msg do stella_vslam) ────────────────────────

def carregar_mapa(caminho_msg):
    """
    Le o mapa.msg (formato msgpack do stella_vslam) e retorna:
      keyframes: dict id -> {'ts': float, 'pos_w': (3,), 'rot_wc': (3,3)}
      landmarks_ids: lista de ids (mesma ordem de landmarks_pos)
      landmarks_pos: array (N, 3) com a posicao mundo de cada landmark

    Formula camera->mundo (confirmada em discussao oficial do stella_vslam,
    github.com/stella-cv/stella_vslam/discussions/614):
      rot_wc = rot_cw.T
      pos_w  = -rot_wc @ trans_cw
    rot_cw e' salva como quaternion [x, y, z, w] (formato scipy).
    """
    with open(caminho_msg, "rb") as f:
        dados = msgpack.unpackb(f.read(), raw=False, strict_map_key=False)

    keyframes_raw = dados.get("keyframes") or dados.get("keyfrms") or {}
    landmarks_raw = dados.get("landmarks") or {}

    if not keyframes_raw or not landmarks_raw:
        print(f"[AVISO] Chaves de nivel superior encontradas no mapa: {list(dados.keys())}")
        print("[AVISO] Esperava 'keyframes'/'keyfrms' e 'landmarks' - confira o formato do arquivo.")

    keyframes = {}
    for kf_id, kf in keyframes_raw.items():
        trans_cw = np.array(kf["trans_cw"], dtype=float).reshape(3)
        rot_cw = Rot.from_quat(kf["rot_cw"]).as_matrix()
        rot_wc = rot_cw.T
        pos_w = -rot_wc @ trans_cw
        keyframes[int(kf_id)] = {
            "ts": float(kf.get("ts", 0.0)),
            "pos_w": pos_w,
            "rot_wc": rot_wc,
        }

    landmarks_ids = []
    landmarks_pos = []
    for lm_id, lm in landmarks_raw.items():
        landmarks_ids.append(int(lm_id))
        landmarks_pos.append(lm["pos_w"])

    landmarks_pos = np.array(landmarks_pos, dtype=float) if landmarks_pos else np.zeros((0, 3))
    print(f"[Mapa] {len(keyframes)} keyframes, {len(landmarks_pos)} landmarks carregados de {caminho_msg}")
    return keyframes, landmarks_ids, landmarks_pos


# ─── Clique (u, v) -> raio 3D no mundo ──────────────────────────────────────

def raio_do_clique(keyframe, u, v):
    """
    Converte um clique normalizado (u, v em [0,1], origem no canto superior
    esquerdo do quadro equiretangular) num raio 3D partindo da posicao do
    keyframe.

    Convencao padrao equiretangular (CONFIRMAR em campo antes de usar em
    producao - ver aviso no topo do arquivo):
      longitude (azimute) = (u - 0.5) * 2*pi   (u=0.5 -> direcao de frente)
      latitude  (elevacao) = (0.5 - v) * pi     (v=0 -> topo / +90 graus)
    """
    lon = (u - 0.5) * 2.0 * math.pi
    lat = (0.5 - v) * math.pi
    dir_cam = np.array([
        math.cos(lat) * math.sin(lon),
        math.sin(lat),
        math.cos(lat) * math.cos(lon),
    ])
    dir_cam /= np.linalg.norm(dir_cam)
    dir_mundo = keyframe["rot_wc"] @ dir_cam
    dir_mundo /= np.linalg.norm(dir_mundo)
    return keyframe["pos_w"].copy(), dir_mundo


# ─── Landmarks proximos ao raio + plano local (RANSAC) ──────────────────────

def landmarks_proximos_ao_raio(origem, direcao, landmarks_pos, t_max=15.0, dist_max=0.5, k_max=60):
    """
    Filtra landmarks que: (a) estao a frente do raio (0 < t < t_max) e
    (b) tem distancia perpendicular ao raio menor que dist_max. Retorna os
    ate k_max landmarks mais proximos da reta (nao do keyframe).
    """
    if len(landmarks_pos) == 0:
        return np.zeros((0, 3))
    v = landmarks_pos - origem
    t = v @ direcao
    mask_frente = (t > 0.05) & (t < t_max)
    if not np.any(mask_frente):
        return np.zeros((0, 3))
    proj = np.outer(t[mask_frente], direcao)
    perp = v[mask_frente] - proj
    dist_perp = np.linalg.norm(perp, axis=1)
    mask_perto = dist_perp < dist_max
    candidatos = landmarks_pos[mask_frente][mask_perto]
    dist_perp = dist_perp[mask_perto]
    if len(candidatos) > k_max:
        idx = np.argsort(dist_perp)[:k_max]
        candidatos = candidatos[idx]
    return candidatos


def ajustar_plano_ransac(pontos, iters=300, limiar=0.02, min_inliers=8, seed=0):
    """
    RANSAC simples: amostra 3 pontos, calcula o plano, conta inliers
    (distancia < limiar), fica com o melhor. Refina o plano final por
    minimos quadrados (SVD) usando so os inliers.
    Retorna (ponto_no_plano, normal_unitaria) ou (None, None) se nao houver
    suporte suficiente - nesse caso o chamador deve cair no fallback
    epipolar (ver medir_por_epipolar_fallback, ainda nao implementado).
    """
    n = len(pontos)
    if n < 3:
        return None, None
    rng = np.random.default_rng(seed)
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
        dist = np.abs((pontos - p0) @ normal)
        inliers = dist < limiar
        if inliers.sum() > melhor_n:
            melhor_n = int(inliers.sum())
            melhor_inliers = inliers
    if melhor_inliers is None or melhor_n < min_inliers:
        return None, None
    pts_in = pontos[melhor_inliers]
    centro = pts_in.mean(axis=0)
    _, _, Vt = np.linalg.svd(pts_in - centro)
    normal_final = Vt[-1]
    return centro, normal_final / np.linalg.norm(normal_final)


def intersectar_raio_plano(origem, direcao, plano_ponto, plano_normal):
    """Intersecao raio x plano. Retorna None se paralelo ou o plano fica atras da origem."""
    denom = direcao @ plano_normal
    if abs(denom) < 1e-9:
        return None
    t = (plano_ponto - origem) @ plano_normal / denom
    if t < 0:
        return None
    return origem + t * direcao


def medir_ponto_robusto(keyframe, u, v, landmarks_pos,
                         combos=((3.0, 0.3), (5.0, 0.4), (8.0, 0.5), (10.0, 0.6),
                                 (12.0, 0.8), (15.0, 0.5)),
                         k_max=60, limiar=0.02, min_inliers=8,
                         tolerancia_consistencia=0.15):
    """
    Achado 2026-07-16 (Pedro, 1o teste com dados reais): a mesma medicao
    (mesmo clique) dava respostas bem diferentes so' variando t_max/dist_max
    - sinal de que o RANSAC as vezes acha um plano ERRADO (o mais numeroso
    dentro do raio de busca escolhido, nao necessariamente o correto), nao
    que a geometria do clique estivesse errada. Antes disso, a funcao aceitava
    cegamente a primeira tentativa que "desse certo" (>= min_inliers).

    Este wrapper roda ajustar_plano_ransac() varias vezes, com combinacoes
    DIFERENTES de (t_max, dist_max), e so' aceita um resultado se os pontos
    3D encontrados em cada tentativa CONVERGIREM entre si (distancia ao
    centroide < tolerancia_consistencia, unidades SLAM). Se as tentativas
    discordarem muito, retorna sucesso=False com o motivo, em vez de
    devolver um numero que parece valido mas pode estar errado.

    Retorna dict:
      sucesso (bool), ponto3d (centroide das tentativas concordantes, ou
      None), confianca ('alta'/'baixa'/None), dispersao (maior distancia ao
      centroide, unidades SLAM), tentativas (lista de {t_max, dist_max,
      ponto3d, n_landmarks} - ponto3d None se essa tentativa especifica
      falhou), motivo (str, so' quando sucesso=False).
    """
    origem, direcao = raio_do_clique(keyframe, u, v)
    tentativas = []
    pontos_validos = []
    for t_max, dist_max in combos:
        candidatos = landmarks_proximos_ao_raio(
            origem, direcao, landmarks_pos, t_max=t_max, dist_max=dist_max, k_max=k_max)
        plano_ponto, plano_normal = ajustar_plano_ransac(
            candidatos, limiar=limiar, min_inliers=min_inliers)
        ponto3d = None
        if plano_ponto is not None:
            ponto3d = intersectar_raio_plano(origem, direcao, plano_ponto, plano_normal)
        tentativas.append(dict(t_max=t_max, dist_max=dist_max, ponto3d=ponto3d,
                                n_landmarks=len(candidatos)))
        if ponto3d is not None:
            pontos_validos.append(ponto3d)

    if len(pontos_validos) < 2:
        return dict(sucesso=False, ponto3d=None, confianca=None, dispersao=None,
                     tentativas=tentativas,
                     motivo=f"So' {len(pontos_validos)} de {len(combos)} combinacoes de busca "
                            "encontraram um plano local - sem base pra checar consistencia "
                            "(precisa de pelo menos 2 tentativas bem-sucedidas). Provavelmente "
                            "poucos landmarks perto desse clique - tente um ponto com mais "
                            "textura visivel (quina, objeto, marca).")

    pontos_validos_arr = np.array(pontos_validos)
    centro = pontos_validos_arr.mean(axis=0)
    dispersao = float(np.linalg.norm(pontos_validos_arr - centro, axis=1).max())

    if dispersao > tolerancia_consistencia:
        return dict(sucesso=False, ponto3d=None, confianca=None, dispersao=dispersao,
                     tentativas=tentativas,
                     motivo=f"As {len(pontos_validos)} tentativas bem-sucedidas NAO convergem "
                            f"entre si (dispersao maxima {dispersao:.3f} unid. SLAM > tolerancia "
                            f"{tolerancia_consistencia}) - sinal de planos DIFERENTES sendo "
                            "encontrados conforme o raio de busca muda, nao um resultado "
                            "confiavel. Tente um ponto com mais textura visivel proxima.")

    confianca = 'alta' if dispersao < tolerancia_consistencia / 3 else 'baixa'
    return dict(sucesso=True, ponto3d=centro, confianca=confianca, dispersao=dispersao,
                 tentativas=tentativas, motivo=None)


def medir_por_epipolar_fallback(*args, **kwargs):
    """
    Fallback para quando a regiao clicada nao tem landmarks suficientes perto
    do raio (parede lisa / pouca textura). Ideia (NAO implementada ainda):
    localizar o mesmo ponto no keyframe equiretangular vizinho por matching
    de features + restricao de linha epipolar entre os 2 keyframes, depois
    triangular normalmente. Ver backlog item 3 do handoff.
    """
    raise NotImplementedError(
        "Fallback por curva epipolar ainda nao implementado. "
        "Poucos landmarks perto do clique - tente clicar mais perto de uma "
        "quina, moveis ou textura visivel, ou aumente --dist-max/--k-max/--t-max."
    )


# ─── Pipeline de medicao de 1 clique ────────────────────────────────────────

def medir_ponto_clique(keyframes, landmarks_pos, kf_id, u, v, **kw):
    if kf_id not in keyframes:
        raise ValueError(f"Keyframe {kf_id} nao encontrado no mapa.")
    origem, direcao = raio_do_clique(keyframes[kf_id], u, v)
    candidatos = landmarks_proximos_ao_raio(
        origem, direcao, landmarks_pos,
        t_max=kw.get("t_max", 15.0),
        dist_max=kw.get("dist_max", 0.5),
        k_max=kw.get("k_max", 60),
    )
    plano_ponto, plano_normal = ajustar_plano_ransac(
        candidatos, limiar=kw.get("limiar", 0.02), min_inliers=kw.get("min_inliers", 8))
    if plano_ponto is None:
        medir_por_epipolar_fallback()  # sempre levanta NotImplementedError por enquanto
    ponto3d = intersectar_raio_plano(origem, direcao, plano_ponto, plano_normal)
    if ponto3d is None:
        raise RuntimeError("Raio nao intersecta o plano local (paralelo ou atras da camera).")
    return ponto3d, len(candidatos)


# ─── Nivel simples: piso pela altura do bastao (sem mapa/landmarks) ─────────

def medir_piso_por_altura_bastao(altura_bastao, elevacao_graus):
    """
    d = h / tan(theta): distancia horizontal ate o ponto do piso mirado pelo
    clique, dado que a camera esta a `altura_bastao` metros do chao e o raio
    aponta `elevacao_graus` graus ABAIXO da horizontal (numero negativo).
    Nao usa o mapa/landmarks - so trigonometria, util quando o mapa nao esta
    disponivel ou o piso ali nao tem landmarks suficientes.
    """
    theta = math.radians(abs(elevacao_graus))
    if theta < 1e-6:
        raise ValueError("Elevacao muito proxima de 0 - raio quase horizontal, nao cruza o piso.")
    return altura_bastao / math.tan(theta)


# ─── Escala SLAM -> metros (calibracao via porta PM = 80cm) ─────────────────

def calibrar_escala(dist_slam, largura_real_m):
    if dist_slam < 1e-9:
        raise ValueError("Distancia medida (unidades SLAM) e' zero - clique invalido.")
    return largura_real_m / dist_slam


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_clique(s):
    try:
        kf_id, u, v = s.split(",")
        return int(kf_id), float(u), float(v)
    except Exception:
        raise argparse.ArgumentTypeError("Use o formato kf_id,u,v  (ex.: 120,0.53,0.61)")


def main():
    ap = argparse.ArgumentParser(
        description="Ferramenta de medicao de distancia em panoramas (Obra360) via landmarks do stella_vslam.")
    ap.add_argument("--mapa", default=None, help="Caminho do mapa.msg (obrigatorio, exceto em --piso).")
    ap.add_argument("--ponto1", type=parse_clique, help="kf_id,u,v do primeiro clique.")
    ap.add_argument("--ponto2", type=parse_clique, help="kf_id,u,v do segundo clique.")
    ap.add_argument("--calibrar-largura", type=float, default=None,
                     help="Se definido, trata a medicao como calibracao: calcula a escala "
                          "SLAM->metros assumindo que ponto1/ponto2 sao os 2 lados de uma "
                          "porta com essa largura real em metros (ex.: 0.80 para porta PM).")
    ap.add_argument("--escala-out", default="escala_mapa.json", help="Onde salvar a escala calibrada.")
    ap.add_argument("--escala-in", default=None, help="JSON de escala ja calibrada (gerado por --escala-out).")
    ap.add_argument("--t-max", type=float, default=15.0, help="Distancia maxima do raio considerada (unid. SLAM).")
    ap.add_argument("--dist-max", type=float, default=0.5, help="Raio de busca perpendicular ao redor do raio.")
    ap.add_argument("--k-max", type=int, default=60, help="Maximo de landmarks usados no ajuste do plano.")
    ap.add_argument("--limiar-plano", type=float, default=0.02, help="Tolerancia do RANSAC (unid. SLAM).")
    ap.add_argument("--robusto", action="store_true",
                     help="Roda o RANSAC com varias combinacoes de t_max/dist_max e so' aceita "
                          "o resultado se convergirem entre si (ver medir_ponto_robusto) - mais "
                          "lento, mas evita aceitar cegamente um plano errado que 'deu certo' "
                          "por acaso com um raio de busca especifico. Recomendado; o modo antigo "
                          "(sem --robusto) fica disponivel pra nao quebrar uso existente.")
    ap.add_argument("--tolerancia-consistencia", type=float, default=0.15,
                     help="Dispersao maxima aceitavel entre tentativas no modo --robusto (unid. SLAM).")
    # Modo simples, sem mapa/landmarks:
    ap.add_argument("--piso", action="store_true", help="Modo simples: altura do piso por altura de bastao (sem mapa).")
    ap.add_argument("--altura-bastao", type=float, default=None, help="Altura da camera acima do chao (metros).")
    ap.add_argument("--elevacao-graus", type=float, default=None,
                     help="Elevacao do raio clicado (graus, negativo = abaixo da horizontal).")
    args = ap.parse_args()

    if args.piso:
        if args.altura_bastao is None or args.elevacao_graus is None:
            print("[ERRO] --piso precisa de --altura-bastao e --elevacao-graus.")
            sys.exit(1)
        d = medir_piso_por_altura_bastao(args.altura_bastao, args.elevacao_graus)
        print(f"[Piso] Distancia horizontal ate o ponto mirado: {d:.3f} m")
        return

    if not args.mapa or not args.ponto1 or not args.ponto2:
        print("[ERRO] Informe --mapa, --ponto1 e --ponto2 (ou use --piso para o modo simples).")
        sys.exit(1)

    keyframes, _, landmarks_pos = carregar_mapa(args.mapa)

    kf1, u1, v1 = args.ponto1
    kf2, u2, v2 = args.ponto2

    if args.robusto:
        if kf1 not in keyframes or kf2 not in keyframes:
            print(f"[ERRO] Keyframe {kf1 if kf1 not in keyframes else kf2} nao encontrado no mapa.")
            sys.exit(1)
        r1 = medir_ponto_robusto(keyframes[kf1], u1, v1, landmarks_pos,
                                  k_max=args.k_max, limiar=args.limiar_plano,
                                  tolerancia_consistencia=args.tolerancia_consistencia)
        r2 = medir_ponto_robusto(keyframes[kf2], u2, v2, landmarks_pos,
                                  k_max=args.k_max, limiar=args.limiar_plano,
                                  tolerancia_consistencia=args.tolerancia_consistencia)
        for label, r in [("ponto1", r1), ("ponto2", r2)]:
            ok_falhas = sum(1 for t in r["tentativas"] if t["ponto3d"] is not None)
            print(f"[Robusto] {label}: {'OK' if r['sucesso'] else 'FALHOU'} "
                  f"({ok_falhas}/{len(r['tentativas'])} tentativas encontraram plano"
                  + (f", dispersao={r['dispersao']:.3f}" if r['dispersao'] is not None else "")
                  + (f", confianca={r['confianca']}" if r['confianca'] else "") + ")")
            if not r["sucesso"]:
                print(f"    Motivo: {r['motivo']}")
        if not (r1["sucesso"] and r2["sucesso"]):
            print("\n[ERRO] Nao foi possivel medir com confianca - ver motivos acima. "
                  "Sem --robusto o script daria um numero mesmo assim (pode estar errado).")
            sys.exit(1)
        p1, p2 = r1["ponto3d"], r2["ponto3d"]
        n1 = n2 = None
    else:
        kw = dict(t_max=args.t_max, dist_max=args.dist_max, k_max=args.k_max, limiar=args.limiar_plano)
        p1, n1 = medir_ponto_clique(keyframes, landmarks_pos, kf1, u1, v1, **kw)
        p2, n2 = medir_ponto_clique(keyframes, landmarks_pos, kf2, u2, v2, **kw)

    dist_slam = float(np.linalg.norm(p1 - p2))
    suporte = f"{n1} / {n2} landmarks" if n1 is not None else "ver [Robusto] acima"
    print(f"[Medicao] Distancia bruta (unidades SLAM): {dist_slam:.5f}  (suporte: {suporte})")

    if args.calibrar_largura is not None:
        escala = calibrar_escala(dist_slam, args.calibrar_largura)
        with open(args.escala_out, "w", encoding="utf-8") as f:
            json.dump({
                "escala_slam_para_metros": escala,
                "mapa": os.path.abspath(args.mapa),
                "calibrado_com_largura_m": args.calibrar_largura,
            }, f, indent=2)
        print(f"[Calibracao] Escala SLAM->metros = {escala:.6f}  (salva em {args.escala_out})")
        print(f"[Calibracao] Confirmacao: {dist_slam:.5f} * {escala:.6f} = {dist_slam * escala:.3f} m "
              f"(esperado: {args.calibrar_largura:.3f} m)")
        return

    if args.escala_in:
        with open(args.escala_in, "r", encoding="utf-8") as f:
            escala = json.load(f)["escala_slam_para_metros"]
        print(f"[Medicao] Distancia real: {dist_slam * escala:.3f} m  (escala de {args.escala_in})")
    else:
        print("[AVISO] Nenhuma escala fornecida (--escala-in) - a distancia acima esta em "
              "unidades SLAM, nao em metros.")


if __name__ == "__main__":
    main()
