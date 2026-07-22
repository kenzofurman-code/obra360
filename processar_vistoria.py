#!/usr/bin/env python3
# processar_vistoria.py
"""
Pipeline automatizado de processamento de vistorias Obra360.

Uso:
    python processar_vistoria.py --id <vistoria_id>
    python processar_vistoria.py --id <vistoria_id> --video caminho/para/video.mp4
    python processar_vistoria.py --id <vistoria_id> --skip-trajectory (usa waypoints já no Firebase)

Pré-requisitos:
    pip install firebase-admin requests opencv-python numpy pymupdf
    Coloque o arquivo serviceAccountKey.json na pasta do projeto.
"""
import argparse
import json
import math
import os
import sys
import tempfile
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, 'serviceAccountKey.json')
STORAGE_BUCKET = 'obras360-c474d.firebasestorage.app'


# ─── Helpers ────────────────────────────────────────────────────────────────

def alinhar_ponto(wp_x: float, wp_y: float, ancora1: dict, heading_offset: float,
                  path_scale: float, espelhar: bool, aspecto: float = 1.0,
                  escala_x: float = 1.0, escala_y: float = 1.0) -> tuple:
    """
    Converte coordenadas brutas da odometria (espaço do vídeo) para coordenadas
    normalizadas da planta (0-1), usando a MESMA transformacão que o site React.

    Replica exatamente a função `alinharPonto` do Visita.jsx.

    aspecto = altura/largura da página do PDF da planta (ver extrair_portas.py, campo "pagina").
    NECESSÁRIO porque o espaço normalizado [0,1]x[0,1] é anisotrópico quando a
    página não é quadrada - rotacionar/escalar sem essa correção distorce a
    trajetória (achata um eixo, alarga o outro). A rotação/escala acontece em
    espaço físico (isotrópico, "unidades de largura"); só a saída em y é
    dividida por aspecto pra voltar ao normalizado.

    escala_x/escala_y (2026-07-22): multiplicadores INDEPENDENTES por eixo, em
    torno da âncora A (default 1.0 = sem efeito). Corrigem esticão residual de
    proporção que o path_scale isotrópico + aspecto não pegam (ajustados
    automaticamente por ajustar_escala_eixos() contra as portas, e/ou na mão
    pelos sliders escala_x/escala_y do site). Mesmos campos no Visita.jsx.
    """
    dx = -wp_x if espelhar else wp_x
    dy = -wp_y
    theta = math.radians(heading_offset + 180)
    rx = dx * math.cos(theta) - dy * math.sin(theta)
    ry = dx * math.sin(theta) + dy * math.cos(theta)
    return (
        ancora1['x'] + rx * path_scale * escala_x,
        ancora1['y'] + (ry * path_scale * escala_y) / aspecto
    )


def desalinhar_ponto(px: float, py: float, ancora1: dict, heading_offset: float,
                     path_scale: float, espelhar: bool, aspecto: float = 1.0,
                     escala_x: float = 1.0, escala_y: float = 1.0) -> tuple:
    """
    Inverso de alinhar_ponto: converte coordenadas da planta (0-1) de volta
    para coordenadas brutas da odometria. Ver nota de aspecto em alinhar_ponto.
    """
    rx = (px - ancora1['x']) / (path_scale * escala_x)
    ry = ((py - ancora1['y']) * aspecto) / (path_scale * escala_y)
    theta = math.radians(heading_offset + 180)
    # Rotacão inversa (transposta de R)
    dx = rx * math.cos(theta) + ry * math.sin(theta)
    dy = -rx * math.sin(theta) + ry * math.cos(theta)
    wp_x = -dx if espelhar else dx
    wp_y = -dy
    return (wp_x, wp_y)


def _interp_raw_em(raw_waypoints: list, t: float) -> tuple:
    """Interpola x,y da trajetória BRUTA (não alinhada) num instante t."""
    ts = [w['t'] for w in raw_waypoints]
    xs = [w['x'] for w in raw_waypoints]
    ys = [w['y'] for w in raw_waypoints]
    return float(np.interp(t, ts, xs)), float(np.interp(t, ts, ys))


def _seg_intersect(p1, p2, q1, q2):
    """Interseção de segmento p1-p2 com q1-q2 (retorna s em [0,1] ao longo de
    p1-p2, ou None se não cruzam). Mesma geometria de slam_to_obra360.py::
    door_quality_and_correction, reimplementada aqui pra não precisar alterar
    aquele arquivo."""
    d1, d2 = p2 - p1, q2 - q1
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    s = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    u = ((q1[0] - p1[0]) * d1[1] - (q1[1] - p1[1]) * d1[0]) / den
    return s if (0 <= s <= 1 and 0 <= u <= 1) else None


def vaos_em_fisico(vaos: list, aspecto: float) -> list:
    """Pré-escala hinge/extremos de cada vão pro espaço FÍSICO (y * aspecto) -
    faz isso uma vez só em vez de repetir a cada chamada de detectar_cruzamentos_vaos."""
    A = np.array([1.0, aspecto])
    return [dict(v, hinge=(np.array(v['hinge']) * A).tolist(),
                extremos=[(np.array(e) * A).tolist() for e in v['extremos']])
            for v in vaos]


def detectar_cruzamentos_vaos(pontos_fisicos: list, vaos_fisico: list) -> list:
    """
    Detecta em quais instantes a trajetória (já alinhada E em coordenadas
    FÍSICAS - ver nota de aspecto em alinhar_ponto) cruza geometricamente o
    arco de abertura de cada vão (hinge -> extremo, esticado 30% pras pontas).
    Mesma técnica de slam_to_obra360.py::door_quality_and_correction (não
    alterada aqui - só reimplementada), retornando as correspondências em vez
    de aplicar a correção direto, pra alimentar o ajuste global (Umeyama) em
    calibrar_por_portas.

    Por que geometria de cruzamento em vez de "ponto mais próximo do centro
    da porta": o prédio pode ter várias portas do MESMO modelo (PM1, PM2...
    são código de tamanho/tipo, não de porta única) próximas entre si - exigir
    uma passagem de verdade pelo vão é bem mais seletivo do que só
    proximidade, evitando grudar na porta errada quando o alinhamento ainda
    está impreciso (era exatamente esse o problema num teste real de 2026-07-13).

    pontos_fisicos: [{'t','x','y'}, ...] já alinhados e com y multiplicado por
    aspecto. vaos_fisico: saída de vaos_em_fisico().

    Retorna [{'t', 'centro', 'dist', 'nome'}, ...] - um por vão cruzado (o
    cruzamento de menor residual, se o vão tiver mais de um extremo/arco).
    """
    X = np.array([[p['x'], p['y']] for p in pontos_fisicos])
    ts_local = np.array([p['t'] for p in pontos_fisicos])

    cruzamentos = []
    for v in vaos_fisico:
        h = np.array(v['hinge'])
        best = None
        for e in map(np.array, v['extremos']):
            centro = (h + e) / 2
            largura = np.linalg.norm(e - h)
            dd = np.hypot(X[:, 0] - centro[0], X[:, 1] - centro[1])
            for j in np.where(dd < 0.03 + largura)[0]:
                if j + 1 >= len(X):
                    continue
                ext = 0.3 * (e - h)
                s = _seg_intersect(X[j], X[j + 1], h - ext, e + ext)
                if s is not None:
                    ptraj = X[j] + s * (X[j + 1] - X[j])
                    r = centro - ptraj
                    tc = ts_local[j] + s * (ts_local[min(j + 1, len(ts_local) - 1)] - ts_local[j])
                    cand = (float(np.linalg.norm(r)), float(tc), centro, v.get('nome', '?'))
                    if best is None or cand[0] < best[0]:
                        best = cand
        if best:
            cruzamentos.append({'dist': best[0], 't': best[1], 'centro': best[2], 'nome': best[3]})
    return cruzamentos


def _bbox_diag(P):
    P = np.asarray(P, dtype=float)
    return float(np.hypot(*(P.max(0) - P.min(0)))) if len(P) else 0.0


def _centros_vaos(vaos_fis: list) -> np.ndarray:
    """Um ponto representativo por vao (centro da abertura, espaco FISICO) -
    usado so' pela busca global grosseira (proximidade). O casamento fino
    continua usando a geometria de arco completa (detectar_cruzamentos_vaos)."""
    centros = []
    for v in vaos_fis:
        h = np.array(v['hinge'], dtype=float)
        exts = [np.array(e, dtype=float) for e in v['extremos']]
        centros.append((h + np.mean(exts, axis=0)) / 2.0 if exts else h)
    return np.array(centros)


def _rot_mirror_raw(R: np.ndarray, heading_deg: float, espelhar: bool) -> np.ndarray:
    """Aplica MESMA convencao de alinhar_ponto/_rot_fixo (theta=heading+180,
    espelha x, y sempre negado) a toda a trajetoria bruta - SEM escala nem
    translacao (essas entram depois na busca)."""
    th = math.radians(heading_deg + 180)
    c, s = math.cos(th), math.sin(th)
    dx = (-R[:, 0] if espelhar else R[:, 0])
    dy = -R[:, 1]
    return np.column_stack([dx * c - dy * s, dx * s + dy * c])


def _score_proximidade(P: np.ndarray, centros: np.ndarray, thr: float, icp: int = 4):
    """Quantas portas ficam a menos de `thr` do ponto de trajetoria mais
    proximo, otimizando SO a translacao por ICP (heading/escala ja aplicados
    em P). Retorna (n_casadas, residual_medio, translacao)."""
    t = centros.mean(0) - P.mean(0)  # seed por centroide
    matched, resid = 0, 1e9
    for _ in range(icp):
        A = P + t
        dist = np.sqrt(((centros[:, None, :] - A[None, :, :]) ** 2).sum(-1))
        dmin = dist.min(1)
        jmin = dist.argmin(1)
        m = dmin < thr
        if int(m.sum()) < 3:
            break
        t = t + (centros[m] - A[jmin[m]]).mean(0)  # so translacao, portas casadas
        matched, resid = int(m.sum()), float(dmin[m].mean())
    return matched, resid, t


def calibrar_auto_por_portas(raw_waypoints: list, vaos: list, aspecto: float = 1.0,
                             min_portas: int = 6, thr_frac: float = 0.045):
    """
    Calibracao AUTOMATICA sem chute (roadmap 4.3 - "zero-clique"). Ideia do
    Pedro (2026-07-18): em vez de refinar a partir de um heading/escala
    configurado na mao, faz uma BUSCA GLOBAL de (heading, escala, espelhar)
    contra as portas do PDF e acha a combinacao que melhor encaixa.

    Por que existe, alem de calibrar_por_portas: aquela funcao e' um
    REFINAMENTO - depende de detectar_cruzamentos_vaos, que exige a trajetoria
    JA cruzando geometricamente os arcos das portas. Se o heading do chute
    estiver errado (ex.: a vistoria de 2026-07-17 precisou de heading bem
    diferente do default 90), nenhum cruzamento e' detectado e ela desiste,
    forcando o inspetor a acertar heading/escala na mao antes. Esta funcao
    remove esse passo manual: nao assume heading nenhum, varre 0-360.

    Estrategia (validada em dados reais - VID_021 recupera heading=90/
    escala=0.46/espelhar=True do zero, batendo o gabarito humano a 2.7%):
      1. Busca grosseira: heading (passo 6 graus) x escala (faixa centrada na
         razao de bounding-box trajetoria/portas) x espelhar, pontuada por
         PROXIMIDADE das portas (quantas casam < thr), translacao por ICP.
      2. Pega os melhores "basins" distintos (headings separados).
      3. Refino local de heading/escala (passo 1 grau) em cada basin.
      4. DESEMPATE por cruzamento GEOMETRICO real (detectar_cruzamentos_vaos):
         a proximidade de centros sozinha tem ambiguidade de espelho (um
         predio simetrico casa quase igual no reflexo) - a passagem real pelo
         arco da porta e' direcional e desfaz o empate. Vence quem tem MAIS
         cruzamentos reais (desempate: menor residual).

    Retorna (ancora1, heading, path_scale, espelhar, info) no MESMO formato de
    calibrar_por_portas, ou None se nao houver portas suficientes pra busca
    global (chamador cai no fluxo antigo). NAO aplica o gate final de holdout -
    isso fica com calibrar_por_portas, que o chamador roda em seguida usando
    este resultado como chute (agora um chute BOM, no basin certo).
    """
    vaos_fis = vaos_em_fisico(vaos, aspecto)
    centros = _centros_vaos(vaos_fis)
    if len(centros) < min_portas:
        return None

    R = np.array([[w['x'], w['y']] for w in raw_waypoints], dtype=float)
    if len(R) < 3:
        return None

    thr = thr_frac * _bbox_diag(centros)
    sc0 = _bbox_diag(centros) / max(_bbox_diag(R), 1e-9)
    escalas = np.linspace(0.35 * sc0, 2.2 * sc0, 28)

    # 1. busca grosseira
    cands = []
    for esp in (False, True):
        bases = {h: _rot_mirror_raw(R, h, esp) for h in range(0, 360, 6)}
        for h, base in bases.items():
            for sc in escalas:
                m, resid, t = _score_proximidade(base * sc, centros, thr)
                if m >= 3:
                    cands.append((m, -resid, float(h), float(sc), esp, tuple(t)))
    if not cands:
        return None
    cands.sort(key=lambda z: z[:2], reverse=True)

    # 2. basins distintos (heading separado por >15 graus dentro do mesmo espelhar)
    top = []
    for c in cands:
        dist_basin = lambda p: abs(((c[2] - p[2] + 180) % 360) - 180) < 15 and c[4] == p[4]
        if not any(dist_basin(p) for p in top):
            top.append(c)
        if len(top) >= 5:
            break

    ts = np.array([w['t'] for w in raw_waypoints], dtype=float)

    def _pontos_fis(h, esp, sc, t):
        P = _rot_mirror_raw(R, h, esp) * sc + np.array(t)
        return [{'t': float(ts[k]), 'x': float(P[k, 0]), 'y': float(P[k, 1])}
                for k in range(len(P))]

    # 3+4. refino local + desempate por cruzamento real
    melhor = None
    candidatos_finais = []
    for c in top:
        best_local = c
        for h in np.arange(c[2] - 6, c[2] + 6.01, 1.0):
            base = _rot_mirror_raw(R, h, c[4])
            for sc in np.linspace(c[3] * 0.85, c[3] * 1.15, 9):
                m, resid, t = _score_proximidade(base * sc, centros, thr)
                if (m, -resid) > best_local[:2]:
                    best_local = (m, -resid, float(h), float(sc), c[4], tuple(t))
        m2, negr2, h2, sc2, esp2, t2 = best_local
        cruz = detectar_cruzamentos_vaos(_pontos_fis(h2, esp2, sc2, t2), vaos_fis)
        resid_cruz = float(np.median([cc['dist'] for cc in cruz])) if cruz else 1e9
        chave = (len(cruz), m2, -resid_cruz)
        candidatos_finais.append((h2, esp2, len(cruz)))
        if melhor is None or chave > melhor['chave']:
            melhor = dict(chave=chave, heading=h2, escala=sc2, espelhar=esp2,
                          tx=t2[0], ty=t2[1], n_cruz=len(cruz),
                          n_prox=m2, resid_prox=-negr2)

    if melhor is None or melhor['n_cruz'] < min_portas:
        return None

    # GATE DE AMBIGUIDADE (2026-07-21, achado do Pedro numa vistoria real com
    # deriva de SLAM): numa trajetoria ENTORTADA pela deriva, nao existe um
    # heading unico correto - varios headings BEM DIFERENTES cruzam quantidades
    # parecidas de portas (confirmado: 0/40/140/240 cruzando 20-24 cada). Nesse
    # caso a busca "vence" num heading errado com aparencia de confianca
    # (residual baixo, muitas portas) e ATROPELA o heading manual certo. Se ha
    # mais de um basin distinto (>30 graus) com >=70% dos cruzamentos do melhor,
    # a calibracao e' AMBIGUA -> devolve None pra manter o manual do usuario, em
    # vez de adotar um palpite errado com falsa confianca. Causa raiz e' a
    # deriva do SLAM (trajetoria limpa trava o heading de forma unica); enquanto
    # isso, e' mais seguro nao sobrescrever o manual do que confiar cegamente.
    melhor_ncruz = melhor['n_cruz']
    concorrentes = []
    for h_c, esp_c, n_c in candidatos_finais:
        if n_c >= 0.7 * melhor_ncruz and all(
                abs(((h_c - hj + 180) % 360) - 180) > 30 for hj, _ in concorrentes):
            concorrentes.append((h_c, esp_c))
    if len(concorrentes) >= 2:
        return {'ambiguo': True,
                'motivo': f'{len(concorrentes)} headings distintos empatam '
                          f'({[round(h) for h, _ in concorrentes]}) - trajetoria '
                          'provavelmente com deriva; mantendo calibracao manual',
                'headings_empatados': [round(h) for h, _ in concorrentes]}

    ancora1 = {'x': float(melhor['tx']), 'y': float(melhor['ty']) / aspecto}
    info = {'origem': 'busca_global', 'n_cruz': melhor['n_cruz'],
            'n_prox': melhor['n_prox'], 'resid_prox': round(melhor['resid_prox'], 4)}
    return ancora1, float(melhor['heading']), float(melhor['escala']), bool(melhor['espelhar']), info


def calibrar_por_portas(raw_waypoints: list, vaos: list, ancora1: dict,
                        heading_offset: float, path_scale: float, espelhar: bool,
                        aspecto: float = 1.0, min_portas: int = 4,
                        raio_ancora: float = 0.12) -> tuple:
    """
    Recalibra (ancora1, path_scale) usando as portas detectadas no PDF como
    pontos de referência MÚLTIPLOS, em vez de confiar só na âncora única
    configurada manualmente no site.

    MUDANÇA (2026-07-15, decisão do Pedro após 3 vistorias reais processadas):
    heading_offset e espelhar NÃO são mais recalibrados/testados aqui - ficam
    FIXOS no que for passado (default 90°/True em criarVisita(), src/lib/visitas.js).
    Antes, essa função tentava as duas variantes de espelhamento (True/False) e
    também deixava o heading flutuar livremente pro que o ajuste (Umeyama)
    encontrasse - mas heading/espelhar são propriedade da CÂMERA/convenção de
    gravação (deveriam ser as mesmas em todo vídeo do mesmo equipamento), não
    algo que devesse variar vídeo a vídeo. O único valor que realmente é
    arbitrário por vídeo é a ESCALA (SLAM monocular não tem referência de
    escala absoluta - por isso ela sozinha continua sendo ajustada aqui,
    variando ~2-3% nos testes reais do Pedro). Deixar heading/espelhar livres
    também corria o risco de o ajuste escolher uma combinação com residual
    baixo mas fisicamente errada (o prédio pode ter simetrias que produzem
    mais de um encaixe plausível nas portas).

    Só substitui os valores manuais (ancora/escala) se a validação (ajusta com
    metade das portas, mede erro na outra metade) mostrar que o ajuste
    automático é realmente bom - senão mantém o que estava configurado.

    IMPORTANTE (aprendido num teste real em 2026-07-13): a correspondência
    porta<->trajetória usa detectar_cruzamentos_vaos() - exige uma passagem
    GEOMÉTRICA pelo arco de abertura da porta (não só "ponto mais próximo do
    centro"), então é bem mais resistente a grudar na porta errada mesmo com
    modelos repetidos no prédio (PM1/PM2/PM3 = código de tamanho/tipo, não de
    porta única). Mesmo assim é um REFINAMENTO a partir do chute
    (ancora1/path_scale) atual, não um solver totalmente livre de chute - o
    refinamento iterativo abaixo ajuda a convergir quando o chute já está na
    ordem de grandeza certa.

    Retorna (ancora1, heading_offset, path_scale, espelhar, info) - heading_offset
    e espelhar saem IDÊNTICOS ao que entrou (nunca recalibrados); info é um
    dict com detalhes pra log/depuração (usado_auto, n_portas, residual_val, motivo).
    """
    vaos_fis = vaos_em_fisico(vaos, aspecto)
    theta = math.radians(heading_offset + 180)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def _rot_fixo(wp_x, wp_y):
        """Mirror + rotacao com heading/espelhar FIXOS (ver motivo no
        docstring acima) - so' resta ajustar escala+ancora (translacao) por
        minimos quadrados contra os cruzamentos de porta."""
        dx = -wp_x if espelhar else wp_x
        dy = -wp_y
        rx = dx * cos_t - dy * sin_t
        ry = dx * sin_t + dy * cos_t
        return rx, ry

    def _fontes_alvos(cruzamentos):
        fontes, alvos = [], []
        for c in cruzamentos:
            rx0, ry0 = _interp_raw_em(raw_waypoints, c['t'])
            u, v = _rot_fixo(rx0, ry0)
            fontes.append([u, v])
            alvos.append(list(c['centro']))
        return np.array(fontes), np.array(alvos)

    def _ajustar_escala_translacao(fontes, alvos):
        """Ajuste de minimos quadrados so' de escala isotropica + translacao
        (heading/espelhar ja fixos em _rot_fixo, entao NAO e' o Umeyama geral
        - esse so' giraria/espelharia de novo). Equivalente a um Procrustes
        com rotacao travada: escala = <fontes centradas, alvos centrados> /
        <fontes centradas, fontes centradas>; translacao = media dos alvos -
        escala * media das fontes."""
        media_f = fontes.mean(axis=0)
        media_a = alvos.mean(axis=0)
        cf = fontes - media_f
        ca = alvos - media_a
        denom = float((cf * cf).sum())
        escala = float((cf * ca).sum()) / denom if denom > 1e-9 else path_scale
        tx, ty = media_a - escala * media_f
        return escala, float(tx), float(ty)

    def _cruzamentos_com(escala_i, tx_i, ty_i):
        pontos_fis = []
        for wp in raw_waypoints:
            u, v = _rot_fixo(wp['x'], wp['y'])
            pontos_fis.append({'t': wp['t'], 'x': tx_i + u * escala_i, 'y': ty_i + v * escala_i})
        return detectar_cruzamentos_vaos(pontos_fis, vaos_fis)

    # TRAVA DE ANCORA (2026-07-21, ideia do Pedro): o refino nao pode arrastar a
    # ancora pra LONGE do ponto A que o usuario marcou. Sem isso, o ajuste de
    # translacao LIVRE escorrega a trajetoria pra uma posicao que casa portas
    # por acaso (e absorve erro - foi a "ancora andar sozinha" que o Pedro
    # relatou). Prende (tx,ty) a um raio maximo (unid. de planta) do A original
    # e, se precisou clampar, reajusta SO a escala com a ancora fixa.
    A0x, A0y = ancora1['x'], ancora1['y'] * aspecto  # ancora original (espaco fisico)

    def _clamp_ancora(escala, tx, ty, fontes, alvos):
        dx = tx - A0x
        dy_plan = (ty - A0y) / aspecto if aspecto > 1e-9 else (ty - A0y)
        dist = math.hypot(dx, dy_plan)
        if dist <= raio_ancora:
            return escala, tx, ty
        f = raio_ancora / dist
        tx_c = A0x + dx * f
        ty_c = A0y + dy_plan * f * aspecto
        # reajusta so a escala com a translacao presa (minimos quadrados 1D)
        t_c = np.array([tx_c, ty_c])
        num = float((fontes * (alvos - t_c)).sum())
        den = float((fontes * fontes).sum())
        escala_c = num / den if den > 1e-9 else escala
        return escala_c, float(tx_c), float(ty_c)

    # Refinamento iterativo: comeca do chute atual (ancora1/path_scale),
    # detecta os cruzamentos geometricos, reajusta (so' escala+translacao) e
    # repete com o ajuste da rodada anterior - um alinhamento melhor pode
    # revelar cruzamentos reais que nao apareciam antes.
    escala_i = path_scale
    tx_i, ty_i = ancora1['x'], ancora1['y'] * aspecto
    cruzamentos = None
    for _ in range(3):
        novos = _cruzamentos_com(escala_i, tx_i, ty_i)
        if len(novos) < min_portas:
            break  # fica com o resultado da rodada anterior
        cruzamentos = novos
        fontes, alvos = _fontes_alvos(cruzamentos)
        escala_i, tx_i, ty_i = _ajustar_escala_translacao(fontes, alvos)
        escala_i, tx_i, ty_i = _clamp_ancora(escala_i, tx_i, ty_i, fontes, alvos)

    if cruzamentos is None or len(cruzamentos) < min_portas:
        return ancora1, heading_offset, path_scale, espelhar, {
            'usado_auto': False,
            'motivo': f'menos de {min_portas} portas detectadas - mantendo calibração manual',
        }

    # Validação por holdout: ajusta (escala+translacao) só com metade das
    # portas finais, mede o erro na outra metade - gate contra confiar num
    # ajuste coincidente/ruidoso quando há poucas portas.
    fontes, alvos = _fontes_alvos(cruzamentos)
    rng = np.random.default_rng(0)
    idx = rng.permutation(len(fontes))
    corte = max(2, len(idx) // 2)
    i_fit, i_val = idx[:corte], idx[corte:]
    if len(i_val) == 0:
        i_val = i_fit
    escala_fit, tx_fit, ty_fit = _ajustar_escala_translacao(fontes[i_fit], alvos[i_fit])
    escala_fit, tx_fit, ty_fit = _clamp_ancora(escala_fit, tx_fit, ty_fit, fontes[i_fit], alvos[i_fit])
    pred_val = np.array([tx_fit, ty_fit]) + escala_fit * fontes[i_val]
    residual_val = float(np.linalg.norm(pred_val - alvos[i_val], axis=1).mean())

    # Só adota o ajuste automático se o residual de validação for baixo o
    # bastante pra confiar (5% da planta) - senão pode ser só ruído/poucas
    # portas que bateram por acaso, e a calibração manual fica valendo.
    LIMIAR_RESIDUAL = 0.05
    if residual_val > LIMIAR_RESIDUAL:
        return ancora1, heading_offset, path_scale, espelhar, {
            'usado_auto': False, 'n_portas': len(cruzamentos),
            'residual_val': residual_val,
            'motivo': 'residual de validação alto demais - mantendo calibração manual',
        }

    # Ajuste final com TODAS as portas (não só a metade de fit da validação),
    # ainda com a ancora presa ao raio maximo do A original.
    escala_final, tx_final, ty_final = _ajustar_escala_translacao(fontes, alvos)
    escala_final, tx_final, ty_final = _clamp_ancora(escala_final, tx_final, ty_final, fontes, alvos)
    ancora1_fit = {'x': float(tx_final), 'y': float(ty_final) / aspecto}

    return ancora1_fit, heading_offset, escala_final, espelhar, {
        'usado_auto': True, 'n_portas': len(cruzamentos),
        'residual_val': residual_val,
    }


def estabilizar_paradas(raw_waypoints: list, dur_min: float = 3.0,
                        percentil: float = 40.0, fator: float = 0.15) -> list:
    """
    Congela num único ponto os trechos onde a pessoa ficou parada (ex.:
    posicionando a câmera no início do vídeo, antes de começar a andar), em
    vez de confiar na posição bruta do SLAM durante esse tempo.

    Por que: SLAM monocular estima mal translação/escala quando há pouco ou
    nenhum movimento real (linha de base curta demais pra triangular direito)
    - um trecho parado pode sair com deriva/jitter na trajetória BRUTA mesmo
    sem movimento nenhum de verdade. Isso confunde a amostragem por distância
    do gerar_quadros.py (a distância acumulada inclui esse jitter falso) e
    desencontra o quadro/panorama da posição real logo no início do percurso -
    relatado num teste real em 2026-07-13 (ficou parado ajustando a câmera no
    início, mas a trajetória já aparecia andando).

    Reaproveita a MESMA detecção de "pausa" por velocidade que já existe em
    gerar_quadros.py::alvos_por_distancia (limiar = percentil da velocidade
    positiva * fator) - só que aqui, em vez de só marcar a pausa pra ganhar um
    quadro extra, ela SUBSTITUI a posição (x,y) de todo o trecho parado pela
    média do trecho. Os timestamps `t` não são tocados, então o vídeo e os
    panoramas continuam sincronizados - só a posição espúria é corrigida.

    dur_min: só congela paradas de pelo menos esse tanto de segundos (evita
    achatar movimento real só porque desacelerou brevemente, ex. virando uma
    esquina apertada).
    """
    if len(raw_waypoints) < 3:
        return raw_waypoints
    ts = np.array([w['t'] for w in raw_waypoints], float)
    xs = np.array([w['x'] for w in raw_waypoints], float)
    ys = np.array([w['y'] for w in raw_waypoints], float)

    passos = np.hypot(np.diff(xs), np.diff(ys))
    dt = np.diff(ts)
    vel = passos / np.maximum(dt, 1e-9)
    positivas = vel[vel > 0]
    if len(positivas) == 0:
        return raw_waypoints
    limiar = max(np.percentile(positivas, percentil) * fator, 1e-9)
    parado = vel < limiar

    xs2, ys2 = xs.copy(), ys.copy()
    n_paradas = 0
    i = 0
    while i < len(parado):
        if parado[i]:
            j = i
            while j + 1 < len(parado) and parado[j + 1]:
                j += 1
            # trecho parado cobre os waypoints i..j+1 (inclusive)
            dur = ts[j + 1] - ts[i]
            if dur >= dur_min:
                mx = xs[i:j + 2].mean()
                my = ys[i:j + 2].mean()
                xs2[i:j + 2] = mx
                ys2[i:j + 2] = my
                n_paradas += 1
            i = j + 1
        else:
            i += 1

    if n_paradas:
        print(f"[Trajetoria] {n_paradas} parada(s) estabilizada(s) - posição "
              "travada nesses trechos (sem distância falsa por deriva do SLAM parado).")

    return [{**wp, 'x': float(xs2[k]), 'y': float(ys2[k])}
            for k, wp in enumerate(raw_waypoints)]


def run_trajectory(video_path: str, output_json: str, rate: float = 0.5):
    """Executa process_trajectory.py e salva JSON de trajetória bruta."""
    from process_trajectory import extract_trajectory
    print(f"\n[Pipeline] Etapa 1/3: Extraindo odometria visual do vídeo...")
    waypoints = extract_trajectory(video_path, sample_rate=rate)
    if not waypoints:
        raise RuntimeError("Falha ao extrair trajetória do vídeo.")
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(waypoints, f)
    print(f"[Pipeline] Trajetória bruta: {len(waypoints)} waypoints -> {output_json}")
    return waypoints


def run_pdf_extractor(pdf_path: str, output_json: str):
    """
    Executa extrair_portas.py e salva o JSON de vãos. Retorna (vaos, aspecto).

    Substituiu pdf_extractor.py (texto + curva mais próxima) nesta etapa em
    2026-07-13: o casamento por PROXIMIDADE DE PONTO é fácil de grudar na
    porta errada quando o prédio tem várias portas do mesmo modelo repetidas
    (PM1/PM2/PM3 são código de tamanho/tipo, não de porta única - confirmado
    num teste real onde o auto-fit convergiu de volta pro chute errado).
    extrair_portas.py detecta o ARCO de abertura de verdade (ajusta um círculo
    às polilinhas do PDF vetorial, valida raio/curvatura/ângulo de abertura) e
    guarda hinge (dobradiça) + extremos do arco - isso permite exigir uma
    passagem GEOMETRICA pelo vão (ver detectar_cruzamentos_vaos), não só
    proximidade. Mesma técnica/arquivo usado no teste de referência que bateu
    1.3% de erro vs gabarito (slam_to_obra360.py::door_quality_and_correction).
    """
    from extrair_portas import extrair
    print(f"\n[Pipeline] Etapa 2/3: Extraindo vãos de portas do PDF (geometria de arco)...")
    vaos, n_arcs, (W, H) = extrair(pdf_path)
    if not vaos:
        raise RuntimeError("Nenhuma porta com arco de abertura detectado no PDF.")
    aspecto = (H / W) if W > 0 else 1.0
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump({"pagina": {"largura": W, "altura": H, "aspecto": aspecto}, "vaos": vaos}, f)
    print(f"[Pipeline] Vãos extraídos: {len(vaos)} portas (de {n_arcs} arcos únicos) -> "
          f"{output_json} (aspecto da página: {aspecto:.4f})")
    return vaos, aspecto


def run_ambientes_extractor(pdf_path: str, output_json: str):
    """Executa extrair_ambientes.py e salva o JSON de ambientes. Retorna a
    lista de ambientes (pode vir vazia se o PDF nao tiver rotulos de area
    reconheciveis - nesse caso a associacao de ambiente por waypoint
    simplesmente nao acontece, sem quebrar o resto do pipeline)."""
    from extrair_ambientes import extrair as extrair_ambientes
    print(f"\n[Pipeline] Extraindo ambientes (nome + area m²) do PDF...")
    ambientes, (W, H) = extrair_ambientes(pdf_path)
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump({"pagina": {"largura": W, "altura": H}, "ambientes": ambientes}, f, ensure_ascii=False)
    print(f"[Pipeline] Ambientes extraídos: {len(ambientes)} -> {output_json}")
    return ambientes


def associar_ambientes(pontos_planta: list, ambientes: list, aspecto: float = 1.0) -> list:
    """Associa cada ponto (ja alinhado/normalizado, com 'x','y') ao ambiente
    mais ESPECIFICO (menor area_m2) cujo circulo de alcance o contem.

    Por que "menor area primeiro": os circulos de alcance dos ambientes
    (raio derivado da propria area - ver extrair_ambientes.py) SE SOBREPOEM
    quando um comodo pequeno (ex.: duto de 0.4m²) fica dentro do alcance de
    um comodo grande vizinho (ex.: sala de 30m²) - nesse caso o ponto deve
    ficar marcado com o ambiente mais especifico (o duto), nao o mais geral
    (a sala). Confirmado com o Pedro em 2026-07-14.

    Ambientes sem 'raio_fis' (escala nao calibravel nesse PDF) sao ignorados.
    Pontos fora do alcance de qualquer ambiente ficam sem o campo 'ambiente'."""
    validos = [a for a in ambientes if a.get('raio_fis') is not None]
    ordenados = sorted(validos, key=lambda a: a['area_m2'])  # menor primeiro = prioridade

    resultado = []
    for pt in pontos_planta:
        x_fis, y_fis = pt['x'], pt['y'] * aspecto
        achado = None
        for amb in ordenados:
            ax_fis, ay_fis = amb['x'], amb['y'] * aspecto
            dist = ((x_fis - ax_fis) ** 2 + (y_fis - ay_fis) ** 2) ** 0.5
            if dist <= amb['raio_fis']:
                achado = amb
                break
        extra = {'ambiente': achado['nome'], 'ambiente_area_m2': achado['area_m2']} if achado else {}
        resultado.append({**pt, **extra})
    return resultado


def buscar_heading_por_portas(raw_waypoints, vaos, ancora_manual, path_scale, espelhar,
                              aspecto, passo=10, min_portas=6, raio_ancora=0.12):
    """Busca AUTOMATICA de heading + escala minimizando o DESVIO das portas, com
    a ANCORA PRESA ao ponto A que o usuario marcou (ideia do Pedro 2026-07-21).

    Pra cada heading (passo em graus), roda calibrar_por_portas - que prende a
    ancora ao raio de A e refina a escala - e le quantas portas cruzam e o
    residual. Score: entre os headings que cruzam MUITAS portas (>= 70%% do
    maximo encontrado - senao um heading que pega 3 portas por acaso ganharia
    so' por ter desvio baixo), escolhe o de MENOR desvio.

    Gate de ambiguidade: se >= 2 headings DISTINTOS (>30 graus) empatam no
    desvio (dentro de 20%%), devolve {'ambiguo':True} - numa trajetoria com
    deriva varios headings encaixam parecido e nao da pra decidir; ai o
    chamador mantem a bussola manual.

    Retorna dict {heading, anc, sc, n, resid}, ou {'ambiguo':True,...}, ou None
    (poucas portas)."""
    cands = []
    for h in range(0, 360, passo):
        anc, hh, sc, esp, info = calibrar_por_portas(
            raw_waypoints, vaos, ancora_manual, float(h), path_scale, espelhar,
            aspecto, min_portas=min_portas, raio_ancora=raio_ancora)
        if info.get('usado_auto') and info.get('n_portas', 0) >= min_portas:
            cands.append(dict(heading=float(h), anc=anc, sc=float(sc),
                              n=int(info['n_portas']), resid=float(info['residual_val'])))
    if not cands:
        return None
    max_n = max(c['n'] for c in cands)
    bons = [c for c in cands if c['n'] >= 0.7 * max_n]
    bons.sort(key=lambda c: c['resid'])
    melhor = bons[0]
    for c in bons:
        if (abs(((c['heading'] - melhor['heading'] + 180) % 360) - 180) > 30
                and c['resid'] <= melhor['resid'] * 1.2):
            return {'ambiguo': True,
                    'motivo': (f"headings {round(melhor['heading'])} e {round(c['heading'])} "
                               f"empatam no desvio ({melhor['resid']:.4f} vs {c['resid']:.4f}) "
                               "- trajetoria provavelmente com deriva"),
                    'headings_empatados': sorted({round(melhor['heading']), round(c['heading'])})}
    return melhor


def ajustar_escala_eixos(raw_waypoints: list, vaos: list, ancora1: dict,
                         heading_offset: float, path_scale: float, espelhar: bool,
                         aspecto: float = 1.0, min_portas: int = 4,
                         limite: tuple = (0.5, 2.0)) -> tuple:
    """
    Ajuste fino ANISOTRÓPICO (2026-07-22, pedido do Pedro): acha multiplicadores
    INDEPENDENTES de X e Y (escala_x, escala_y) que minimizam o desvio das
    portas, DEPOIS que heading/espelhar/path_scale/âncora já foram calibrados.

    Modelado como uma escala em torno da ÂNCORA A (que NÃO se move - respeita a
    trava de âncora): para cada porta cruzada, o vetor (ponto_projetado - A) é
    esticado por escala_x em x e escala_y em y pra bater no centro da porta.
    Como heading está fixo, isso é um mínimos quadrados 1D por eixo (fecha em
    forma fechada, sem iterar):

        escala_x = Σ (dpx·ddx) / Σ (dpx²)      dpx = proj_x - A_x ; ddx = porta_x - A_x
        escala_y = Σ (dpy·ddy) / Σ (dpy²)      (idem em y, tudo normalizado)

    Por que isotrópico (path_scale sozinho) não bastava: se o aspecto do PDF não
    bate 100% com a imagem, sobra um esticão num sentido só - o Pedro confirmou
    isso ajustando o X na mão e vendo "MUITO melhor". Aqui isso vira automático.

    Retorna (escala_x, escala_y). Se houver menos de min_portas cruzamentos,
    devolve (1.0, 1.0) - sem efeito, superset estrito (não regride nada).
    limite: clamp de segurança por eixo (evita esticão absurdo por ruído).
    """
    aligned, aligned_fis = [], []
    for wp in raw_waypoints:
        px, py = alinhar_ponto(wp['x'], wp['y'], ancora1, heading_offset,
                               path_scale, espelhar, aspecto)
        aligned.append({'t': wp['t'], 'x': px, 'y': py})
        aligned_fis.append({'t': wp['t'], 'x': px, 'y': py * aspecto})

    cruz = sorted(detectar_cruzamentos_vaos(aligned_fis, vaos_em_fisico(vaos, aspecto)),
                  key=lambda c: c['t'])
    filt = []
    for c in cruz:
        if not filt or (c['t'] - filt[-1]['t']) > 4.0:
            filt.append(c)
    if len(filt) < min_portas:
        return 1.0, 1.0

    ax, ay = ancora1['x'], ancora1['y']  # âncora em espaço normalizado
    numx = denx = numy = deny = 0.0
    for c in filt:
        pt = min(aligned, key=lambda p: abs(p['t'] - c['t']))
        dpx = pt['x'] - ax
        dpy = pt['y'] - ay
        ddx = c['centro'][0] - ax
        ddy = (c['centro'][1] / aspecto) - ay
        numx += dpx * ddx; denx += dpx * dpx
        numy += dpy * ddy; deny += dpy * dpy

    ex = numx / denx if denx > 1e-9 else 1.0
    ey = numy / deny if deny > 1e-9 else 1.0
    lo, hi = limite
    ex = min(hi, max(lo, ex))
    ey = min(hi, max(lo, ey))
    return float(ex), float(ey)


def run_map_matching(raw_waypoints: list, vaos: list,
                     ancora1: dict, heading_offset: float,
                     path_scale: float, espelhar: bool,
                     aspecto: float = 1.0, ambientes: list = None) -> list:
    """
    Etapa 3/3: Alinha a trajetória na planta usando âncoras do Firebase,
    detecta cruzamentos geométricos com os vãos de porta (extrair_portas.py)
    e aplica correções por Map Matching. Remove dependência do gabarito manual.

    aspecto = altura/largura da página (ver extrair_portas.py) - sem isso a
    distância até as portas (e a trajetória toda) fica distorcida em páginas
    não-quadradas (ver nota em alinhar_ponto).

    Retorna (waypoints_corrigidos, calibracao) - calibracao e' um dict com os
    valores de ancora1/heading_offset/path_scale/espelhar REALMENTE usados
    (podem ter sido recalibrados automaticamente por calibrar_por_portas, ver
    ali) e 'info' com detalhes pra log. O chamador (worker.py/main() aqui
    mesmo) deve gravar esses valores de volta no Firestore, ja' que o site usa
    os MESMOS campos pra re-alinhar a trajetoria na tela.
    """
    # Calibracao AUTOMATICA global primeiro (roadmap 4.3 "zero-clique", ideia do
    # Pedro 2026-07-18): busca heading+escala+espelhar do zero contra as portas,
    # sem depender do chute manual estar perto. Se achar um alinhamento com
    # portas suficientes (cruzamentos reais), usa como chute do refino; senao
    # mantem o heading/escala manuais (comportamento antigo).
    # Calibracao AUTOMATICA (2026-07-21, direcao do Pedro): busca o HEADING e a
    # ESCALA que dao o MENOR DESVIO das portas, com a ANCORA PRESA a um raio do
    # ponto A que o usuario marcou (buscar_heading_por_portas). Nao depende de
    # chute de heading. Se a busca ficar ambigua (varios headings empatam no
    # desvio - tipico de trajetoria com deriva), mantem a bussola manual e so'
    # refina escala+ancora, avisando pra verificar no site.
    auto = buscar_heading_por_portas(raw_waypoints, vaos, ancora1, path_scale,
                                     espelhar, aspecto, raio_ancora=0.12)
    if isinstance(auto, dict) and auto.get('ambiguo'):
        print(f"\n[Pipeline] Busca de heading INCONCLUSIVA ({auto['motivo']}) - "
              f"MANTENDO a bussola manual (heading={heading_offset}, espelhar={espelhar}) "
              "e refinando so' escala+ancora presa. VERIFIQUE no site.")
        ancora1, heading_offset, path_scale, espelhar, calib_info = calibrar_por_portas(
            raw_waypoints, vaos, ancora1, heading_offset, path_scale, espelhar, aspecto)
    elif auto is not None:
        heading_offset = auto['heading']
        path_scale = auto['sc']
        ancora1 = auto['anc']
        print(f"\n[Pipeline] Calibracao AUTOMATICA por portas: heading={heading_offset:.0f} "
              f"escala={path_scale:.4f} espelhar={espelhar} | {auto['n']} portas, "
              f"desvio={auto['resid']:.4f} (menor entre os headings testados).")
        calib_info = {'usado_auto': True, 'n_portas': auto['n'], 'residual_val': auto['resid']}
    else:
        print(f"\n[Pipeline] Poucas portas detectadas - usando calibracao manual como esta.")
        calib_info = {'usado_auto': False, 'motivo': 'poucas portas'}

    # Ajuste fino ANISOTROPICO (2026-07-22): depois de heading/escala/ancora,
    # acha os multiplicadores independentes de X e Y que minimizam o desvio das
    # portas (escala em torno da ancora fixa - nao a move). Corrige esticao de
    # proporcao num sentido so' que o path_scale isotropico deixa passar.
    escala_x, escala_y = ajustar_escala_eixos(raw_waypoints, vaos, ancora1,
                                              heading_offset, path_scale, espelhar, aspecto)
    if abs(escala_x - 1.0) > 1e-4 or abs(escala_y - 1.0) > 1e-4:
        print(f"[Pipeline] Escala por eixo (anisotropica): X={escala_x:.4f} Y={escala_y:.4f} "
              "(1.0 = sem correcao).")

    print(f"\n[Pipeline] Etapa 3/3: Map Matching com âncoras do Firebase...")
    print(f"  Âncora A: {ancora1}")
    print(f"  Heading Offset: {heading_offset}°")
    print(f"  Path Scale: {path_scale}")
    print(f"  Espelhar: {espelhar}")
    print(f"  Aspecto da página: {aspecto:.4f}")

    # Projeta trajetória bruta para espaço da planta (normalizado) e também
    # pra espaço FÍSICO (y*aspecto) - a deteccao de cruzamento geometrico
    # precisa do fisico (ver nota de aspecto em alinhar_ponto).
    aligned = []
    aligned_fis = []
    for wp in raw_waypoints:
        px, py = alinhar_ponto(wp['x'], wp['y'], ancora1, heading_offset,
                               path_scale, espelhar, aspecto, escala_x, escala_y)
        aligned.append({'t': wp['t'], 'x': px, 'y': py})
        aligned_fis.append({'t': wp['t'], 'x': px, 'y': py * aspecto})

    # Detecta cruzamentos GEOMÉTRICOS com os vãos de porta (passagem real pelo
    # arco, não só proximidade - ver detectar_cruzamentos_vaos) e remove
    # duplicatas temporais (intervalo mínimo de 4s).
    cruzamentos = detectar_cruzamentos_vaos(aligned_fis, vaos_em_fisico(vaos, aspecto))
    cruzamentos = sorted(cruzamentos, key=lambda c: c['t'])
    filtradas = []
    for c in cruzamentos:
        if not filtradas or (c['t'] - filtradas[-1]['t']) > 4.0:
            filtradas.append(c)

    print(f"  Portas detectadas: {len(filtradas)}")
    for c in filtradas:
        print(f"  t={c['t']:.1f}s | {c['nome']} | dist={c['dist']*100:.2f}%")

    # Cria vetores de correção para snap nas portas (centro do vão de volta
    # pro espaço normalizado, dividindo y por aspecto)
    correcoes = []
    for c in filtradas:
        pt_al = min(aligned, key=lambda pt: abs(pt['t'] - c['t']))
        cx = c['centro'][0] - pt_al['x']
        cy = (c['centro'][1] / aspecto) - pt_al['y']
        correcoes.append((c['t'], cx, cy))

    # Aplica interpolacão linear das correções em toda a trajetória
    corrigida_planta = []
    for pt in aligned:
        t = pt['t']
        if len(correcoes) == 0:
            cx, cy = 0.0, 0.0
        elif t <= correcoes[0][0]:
            cx, cy = correcoes[0][1], correcoes[0][2]
        elif t >= correcoes[-1][0]:
            cx, cy = correcoes[-1][1], correcoes[-1][2]
        else:
            for i in range(len(correcoes) - 1):
                t0, cx0, cy0 = correcoes[i]
                t1, cx1, cy1 = correcoes[i+1]
                if t0 <= t <= t1:
                    w = (t - t0) / (t1 - t0)
                    cx = (1-w)*cx0 + w*cx1
                    cy = (1-w)*cy0 + w*cy1
                    break
        evento = 'caminho'
        extra = {}
        for c in filtradas:
            if abs(pt['t'] - c['t']) < 0.1:
                evento = 'passagem'
                extra = {'passagem_id': c['nome'], 'codigo': c['nome']}
        corrigida_planta.append({'t': pt['t'], 'x': round(pt['x'] + cx, 5),
                                  'y': round(pt['y'] + cy, 5), 'evento': evento, **extra})

    # Associa cada ponto ao ambiente mais especifico (menor area) cujo circulo
    # de alcance o contem - so' roda se ambientes foi passado (opcional, nao
    # quebra chamadores antigos que ainda nao usam essa feature).
    if ambientes:
        n_antes = len(ambientes)
        corrigida_planta = associar_ambientes(corrigida_planta, ambientes, aspecto)
        n_com_ambiente = sum(1 for pt in corrigida_planta if pt.get('ambiente'))
        print(f"  Ambientes: {n_com_ambiente}/{len(corrigida_planta)} pontos associados "
              f"(de {n_antes} ambiente(s) detectado(s) no PDF)")

    # Converte de volta para espaço bruto da odometria para compatibilidade com o site
    corrigida_raw = []
    for pt in corrigida_planta:
        wx, wy = desalinhar_ponto(pt['x'], pt['y'], ancora1, heading_offset,
                                   path_scale, espelhar, aspecto, escala_x, escala_y)
        corrigida_raw.append({
            't': pt['t'],
            'x': round(wx, 5),
            'y': round(wy, 5),
            'label': '',
            'observacao': '',
            'evento': pt.get('evento', 'caminho'),
            **{k: v for k, v in pt.items() if k in ('passagem_id', 'codigo', 'ambiente', 'ambiente_area_m2')}
        })

    print(f"  Trajetória corrigida: {len(corrigida_raw)} pontos")
    calibracao = {
        'ancora1': ancora1, 'heading_offset': heading_offset,
        'path_scale': path_scale, 'espelhar_caminho': espelhar,
        'escala_x': escala_x, 'escala_y': escala_y,
        'info': calib_info,
    }
    return corrigida_raw, calibracao


# ─── Main ───────────────────────────────────────────────────────────────────

def subir_json_r2(dados_dict, chave):
    """Sobe um JSON (ex.: trajetoria corrigida) pro R2 - mesmo helper de
    worker.py::subir_json_r2, ver comentario la sobre o motivo (limite de 1MB
    por documento do Firestore). Retorna True se subiu, False se R2 nao
    estiver configurado no .env (chamador decide o fallback)."""
    bucket = os.environ.get('R2_BUCKET_NAME')
    account = os.environ.get('R2_ACCOUNT_ID')
    key = os.environ.get('R2_ACCESS_KEY_ID')
    secret = os.environ.get('R2_SECRET_ACCESS_KEY')
    if not (bucket and account and key and secret):
        return False
    import boto3
    s3 = boto3.client('s3', aws_access_key_id=key, aws_secret_access_key=secret,
                       endpoint_url=f'https://{account}.r2.cloudflarestorage.com',
                       region_name='auto')
    corpo = json.dumps(dados_dict).encode('utf-8')
    s3.put_object(Bucket=bucket, Key=chave, Body=corpo, ContentType='application/json')
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Pipeline automatizado Obra360: vídeo 360 + planta PDF → trajetória corrigida no Firebase.')
    parser.add_argument('--id', required=True, help='ID da vistoria no Firestore')
    parser.add_argument('--video', default=None,
        help='Caminho local do vídeo MP4 (opcional; se omitido, tenta baixar da URL da vistoria)')
    parser.add_argument('--skip-trajectory', action='store_true',
        help='Pula etapa de odometria e usa os waypoints já salvos no Firebase')
    parser.add_argument('--rate', type=float, default=0.5,
        help='Taxa de amostragem EKF em segundos (padrão: 0.5)')
    args = parser.parse_args()

    # Verifica credenciais Firebase
    if not os.path.exists(SERVICE_ACCOUNT):
        print(f"[ERRO] Arquivo de credenciais não encontrado: {SERVICE_ACCOUNT}")
        print("Gere em: Firebase Console → Configurações → Contas de serviço → Gerar nova chave privada")
        sys.exit(1)

    # Inicializa Firebase
    import firebase_client
    firebase_client.init(SERVICE_ACCOUNT, STORAGE_BUCKET)

    # Lê dados da vistoria
    print(f"\n[Pipeline] Buscando vistoria '{args.id}'...")
    visita = firebase_client.get_visita(args.id)

    ancora1 = visita.get('ancora1')
    if not ancora1:
        print("[ERRO] Âncora A não definida na vistoria. Configure-a no site primeiro.")
        sys.exit(1)

    heading_offset = visita.get('heading_offset', 0)
    path_scale = visita.get('path_scale', 0.15)
    # padrao False - ver mesmo comentario/motivo em worker.py::processar_visita
    espelhar = visita.get('espelhar_caminho', False)
    planta_url = visita.get('planta_url')

    print(f"  Pavimento: {visita.get('pavimento', '?')}")
    print(f"  Âncora A: {ancora1}")
    print(f"  Heading Offset: {heading_offset}°")
    print(f"  Path Scale: {path_scale}")
    print(f"  Espelhar: {espelhar}")
    print(f"  Planta URL: {'sim' if planta_url else 'NÃO DEFINIDA'}")

    tmp_dir = tempfile.mkdtemp(prefix='obra360_')

    # ── Etapa 0: Obter video e waypoints brutos ──────────────────────────────
    if args.skip_trajectory:
        print("\n[Pipeline] Etapa 1/3: Usando waypoints já existentes no Firebase (--skip-trajectory).")
        raw_waypoints = visita.get('waypoints', [])
        if not raw_waypoints:
            print("[ERRO] Nenhum waypoint encontrado na vistoria. Remova --skip-trajectory.")
            sys.exit(1)
        print(f"  {len(raw_waypoints)} waypoints carregados do Firestore.")
    else:
        # Obtém vídeo
        video_path = args.video
        if not video_path:
            video_url = visita.get('hls_url', '')
            # Tenta construir URL de download direto (Cloudflare Stream não suporta download,
            # portanto o usuário deve passar --video explicitamente por enquanto)
            print("[AVISO] URL de download direto do vídeo não disponível automaticamente.")
            print("  Cloudflare Stream usa HLS (streaming apenas). Forneça o arquivo local com:")
            print(f"  python processar_vistoria.py --id {args.id} --video <caminho_do_video.mp4>")
            sys.exit(1)

        if not os.path.exists(video_path):
            print(f"[ERRO] Arquivo de vídeo não encontrado: {video_path}")
            sys.exit(1)

        raw_json = os.path.join(tmp_dir, 'trajetoria_bruta.json')
        raw_waypoints = run_trajectory(video_path, raw_json, rate=args.rate)

    # Congela trechos parados (ver nota completa em estabilizar_paradas) - mesma
    # correcao aplicada no worker.py.
    raw_waypoints = estabilizar_paradas(raw_waypoints)

    # ── Etapa 2: Extrair portas do PDF ──────────────────────────────────────
    if not planta_url:
        print("[ERRO] A vistoria não tem planta PDF cadastrada. Faça o upload no site.")
        sys.exit(1)

    # Verifica se é PDF
    is_pdf = planta_url.lower().split('?')[0].endswith('.pdf')
    if not is_pdf:
        print("[AVISO] A planta da vistoria não é um PDF (pode ser imagem).")
        print("  Map Matching com imagem ainda não suportado. Usando trajetória sem correção.")
        # Salva trajetória bruta sem map matching
        waypoints_final = [{
            't': wp['t'], 'x': round(wp['x'], 5), 'y': round(wp['y'], 5),
            'label': '', 'observacao': '', 'evento': 'caminho'
        } for wp in raw_waypoints]
        firebase_client.salvar_waypoints(args.id, waypoints_final, status='processado')
        print(f"\n✅ Concluído (sem map matching): {len(waypoints_final)} waypoints salvos.")
        return

    pdf_path = firebase_client.baixar_pdf(planta_url)
    vaos_json = os.path.join(tmp_dir, 'vaos.json')
    vaos, aspecto = run_pdf_extractor(pdf_path, vaos_json)
    ambientes_json = os.path.join(tmp_dir, 'ambientes.json')
    ambientes = run_ambientes_extractor(pdf_path, ambientes_json)
    os.unlink(pdf_path)  # Limpa PDF temporário

    # ── Etapa 3: Map Matching ────────────────────────────────────────────────
    waypoints_corrigidos, calibracao = run_map_matching(
        raw_waypoints, vaos, ancora1, heading_offset,
        path_scale, espelhar, aspecto=aspecto, ambientes=ambientes
    )

    # ── Salva resultado no Firebase ──────────────────────────────────────────
    # Grava tambem a calibracao (pode ter sido recalibrada automaticamente por
    # multiplas portas - ver calibrar_por_portas) pra o site re-exibir com os
    # mesmos valores, e o inventario de ambientes (nome+area, mesmo sem foto
    # associada) pra uso futuro de progresso por comodo/pavimento/obra.
    dados_finais = {
        'status': 'processado',
        'planta_aspecto': aspecto,
        'ancora1': calibracao['ancora1'],
        'heading_offset': calibracao['heading_offset'],
        'path_scale': calibracao['path_scale'],
        'espelhar_caminho': calibracao['espelhar_caminho'],
        'ambientes': ambientes,
        # Selo de qualidade: se a calibracao automatica por portas (so' escala/
        # ancora, heading/espelhar fixos - ver calibrar_por_portas) foi adotada
        # ou nao nesse processamento, e
        # com que confianca (n_portas/residual_val) - o site usa isso pra
        # mostrar um badge (ver Visita.jsx).
        'selo_qualidade': calibracao['info'],
    }
    # 'waypoints' NAO vai mais direto no documento - trajetorias longas (SLAM)
    # passam facil de 1MB, limite RIGIDO por documento do Firestore (ver
    # mesmo comentario/motivo em worker.py::processar_visita, confirmado numa
    # vistoria real 2026-07-14 com 16515 poses = doc de 1.057.381 bytes,
    # recusado pelo Firestore). Sobe pro R2 (mesmo bucket dos panoramas) e
    # grava so' a URL; Visita.jsx busca via fetch() quando 'waypoints_url'
    # existir, com fallback pro campo 'waypoints' inline (vistorias antigas/curtas).
    r2_public_url = os.environ.get('R2_PUBLIC_URL')
    waypoints_key = f"{args.id}/waypoints_corrigidos.json"
    if r2_public_url and subir_json_r2(waypoints_corrigidos, waypoints_key):
        dados_finais['waypoints_url'] = f"{r2_public_url}/{waypoints_key}"
    else:
        tamanho_estimado = len(json.dumps(waypoints_corrigidos).encode('utf-8'))
        if tamanho_estimado < 700_000:
            dados_finais['waypoints'] = waypoints_corrigidos
        else:
            print(f"[ERRO] Trajetoria tem ~{tamanho_estimado} bytes - nao caberia com folga "
                  "no limite de 1MB por documento do Firestore, e R2_BUCKET_NAME/R2_PUBLIC_URL "
                  "nao estao configuradas no .env pra subir como arquivo separado.")
            sys.exit(1)
    firebase_client.atualizar_campos(args.id, dados_finais)

    print(f"\n[OK] Pipeline concluido com sucesso!")
    print(f"   Vistoria ID: {args.id}")
    print(f"   Waypoints salvos: {len(waypoints_corrigidos)}")
    print(f"   Recarregue a pagina no site para ver a trajetoria corrigida.")


if __name__ == '__main__':
    main()
