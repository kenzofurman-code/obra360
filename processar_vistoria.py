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
                  path_scale: float, espelhar: bool, aspecto: float = 1.0) -> tuple:
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
    """
    dx = -wp_x if espelhar else wp_x
    dy = -wp_y
    theta = math.radians(heading_offset + 180)
    rx = dx * math.cos(theta) - dy * math.sin(theta)
    ry = dx * math.sin(theta) + dy * math.cos(theta)
    return (
        ancora1['x'] + rx * path_scale,
        ancora1['y'] + (ry * path_scale) / aspecto
    )


def desalinhar_ponto(px: float, py: float, ancora1: dict, heading_offset: float,
                     path_scale: float, espelhar: bool, aspecto: float = 1.0) -> tuple:
    """
    Inverso de alinhar_ponto: converte coordenadas da planta (0-1) de volta
    para coordenadas brutas da odometria. Ver nota de aspecto em alinhar_ponto.
    """
    rx = (px - ancora1['x']) / path_scale
    ry = ((py - ancora1['y']) * aspecto) / path_scale
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


def calibrar_por_portas(raw_waypoints: list, vaos: list, ancora1: dict,
                        heading_offset: float, path_scale: float, espelhar: bool,
                        aspecto: float = 1.0, min_portas: int = 4) -> tuple:
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

    # Ajuste final com TODAS as portas (não só a metade de fit da validação).
    escala_final, tx_final, ty_final = _ajustar_escala_translacao(fontes, alvos)
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
    print(f"\n[Pipeline] Calibrando escala/ancora automaticamente por multiplas portas "
          f"(heading/espelhar fixos)...")
    ancora1, heading_offset, path_scale, espelhar, calib_info = calibrar_por_portas(
        raw_waypoints, vaos, ancora1, heading_offset, path_scale, espelhar, aspecto)
    if calib_info.get('usado_auto'):
        print(f"  [OK] Calibracao automatica adotada: {calib_info['n_portas']} portas, "
              f"residual de validacao={calib_info['residual_val']:.4f}")
    else:
        print(f"  [AVISO] Calibracao automatica NAO adotada ({calib_info.get('motivo')}) "
              "- usando ancora/heading/escala manuais como estavam.")

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
                               path_scale, espelhar, aspecto)
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
                                   path_scale, espelhar, aspecto)
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
