# -*- coding: utf-8 -*-
# extrair_portas.py
# Extrai portas (rotulos P*/PM*/PJ*/PU*/PC*/PA*/PF* + arcos de abertura) de um
# PDF vetorial de planta baixa, gerando um JSON de "vaos" para validar/corrigir
# trajetorias do Obra360.
#
# Uso: python extrair_portas.py --pdf planta.pdf --out portas.json [--limite-x 2100]
# Coordenadas de saida normalizadas pela pagina (mesmo referencial do Obra360).

import fitz, re, json, argparse
import numpy as np


def fit_circle(pts):
    A = np.c_[2*pts[:, 0], 2*pts[:, 1], np.ones(len(pts))]
    b = (pts**2).sum(1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, c = sol
    r = np.sqrt(max(c + cx*cx + cy*cy, 1e-9))
    resid = np.abs(np.hypot(pts[:, 0]-cx, pts[:, 1]-cy) - r)
    return cx, cy, r, resid.max()


def _bezier_pts(p0, p1, p2, p3, n=8):
    """Amostra n pontos ao longo de uma curva de Bezier cubica (item 'c' do
    PyMuPDF). Necessario porque algumas plantas (ex.: P070, validado em
    2026-07-14) desenham o arco de abertura da porta como 1-2 curvas de
    Bezier nativas em vez de uma polilinha de segmentos retos ('l') - o
    detector antigo so olhava pra 'l' e achava ZERO arcos nessas plantas.
    n=8 garante que uma unica curva ja atinja o minimo de 6 pontos exigido
    mais abaixo pra tentar o fit de circulo (uma curva sozinha ja cobre um
    trecho grande do arco - testado com r~45-51pts e residuo~0.01 no P070)."""
    ts = np.linspace(0, 1, n)
    xs = (1-ts)**3*p0[0] + 3*(1-ts)**2*ts*p1[0] + 3*(1-ts)*ts**2*p2[0] + ts**3*p3[0]
    ys = (1-ts)**3*p0[1] + 3*(1-ts)**2*ts*p1[1] + 3*(1-ts)*ts**2*p2[1] + ts**3*p3[1]
    return list(zip(xs.tolist(), ys.tolist()))


# ─── Portas de correr/sacada (sem arco de abertura) via tabela de esquadrias ──
# Testado e validado com o Pedro em 2026-07-14 (script visualizar_pj_sinteticas.py):
# portas tipo CORRER (ex.: PJ - sacada) nao tem arco pra detectar geometricamente
# (elas deslizam, nao giram). Em vez disso: le a largura real (metros) da tabela
# de esquadrias da propria planta, acha a orientacao da parede mais proxima do
# rotulo no desenho, e monta um vao RETO (hinge->extremo unico) com esse
# tamanho, na mesma escala do PDF - MESMO SCHEMA das portas com arco, entao o
# resto do pipeline (calibrar_por_portas, detectar_cruzamentos_vaos em
# processar_vistoria.py) usa sem nenhuma mudanca.
PAT_NUM_ESQUADRIA = re.compile(r'^\d+(,\d+)?$')
TIPOS_CORRER = {'CORRER'}
TIPOS_ESQUADRIA_VALIDOS = {'ABRIR', 'CORRER', 'CAMARÃO', 'CAMARAO', 'FIXO', 'BASCULANTE', 'PIVOTANTE'}


def _parsear_esquadrias(page, tol=3.0, busca_tipo_max=6, busca_qtd_max=6):
    """Agrupa palavras por Y (linha visual da tabela - cada celula pode ser um
    bloco de texto diferente, mas todas ficam na mesma altura) e casa
    CODIGO,largura,altura,...,TIPO olhando so pra frente do codigo (ignora
    ruido de cotas do desenho que caiam na mesma altura, antes do codigo).

    NAO assume mais posicao fixa pro TIPO logo apos largura/altura: a coluna
    PEITORIL entre altura e tipo tem largura variavel de tokens - as vezes um
    numero ("15", ex.: P55 nesta planta), as vezes um traco literal "-"
    (portas de madeira/aco sem peitoril, ex.: P80/P90A na planta P070,
    2026-07-14) e as vezes nem aparece como token. Antes o codigo exigia
    tokens[i+3].isdigit() pra aceitar a linha - isso quebrava TODA a leitura
    de P80/P90A (peitoril "-" nao e' digito), fazendo a calibracao de escala
    das portas de correr falhar sem nenhum codigo em comum pra calibrar.
    Agora procura o TIPO dentro de uma janela pequena a frente (o primeiro
    token que bate com TIPOS_ESQUADRIA_VALIDOS), em vez de exigir posicao
    fixa. QTDE (ultima coluna da tabela, nao mais logo apos largura/altura)
    e' opcional/melhor-esforco - so' usado por validacao (ex.:
    visualizar_pj_sinteticas.py), nao pela calibracao de escala em si."""
    words = page.get_text("words")
    linhas = {}
    for w in words:
        y = round(w[1] / tol) * tol
        linhas.setdefault(y, []).append(w)

    esquadrias = {}
    for y in sorted(linhas):
        tokens = [w[4] for w in sorted(linhas[y], key=lambda w: w[0])]
        for i, tok in enumerate(tokens):
            if tok in esquadrias or i + 2 >= len(tokens):
                continue
            largura_s, altura_s = tokens[i+1], tokens[i+2]
            if not (PAT_NUM_ESQUADRIA.match(largura_s) and PAT_NUM_ESQUADRIA.match(altura_s)):
                continue
            tipo, qtd_s = None, None
            fim_busca = min(i+3+busca_tipo_max, len(tokens))
            for j in range(i+3, fim_busca):
                if tokens[j].upper() in TIPOS_ESQUADRIA_VALIDOS:
                    tipo = tokens[j].upper()
                    for k in range(j+1, min(j+1+busca_qtd_max, len(tokens))):
                        if tokens[k].isdigit():
                            qtd_s = tokens[k]
                            break
                    break
            if tipo is None:
                continue
            largura_m = float(largura_s.replace(',', '.'))
            altura_m = float(altura_s.replace(',', '.'))
            # Algumas plantas escrevem largura/altura em CENTIMETROS, sem
            # virgula decimal (ex.: planta P070, 2026-07-15: "80"/"90" pra
            # P80/P90A) em vez do formato em METROS com virgula ja suportado
            # ("0,80"). PAT_NUM_ESQUADRIA aceita os dois formatos (mesmo regex),
            # entao "80" virava largura_m=80.0 (80 METROS!) em vez de 0.80 -
            # nenhuma porta/janela real tem 10+ metros de largura, entao
            # qualquer valor acima disso e' quase certamente centimetro sem
            # virgula. Isso nao mudava a deteccao geometrica de porta em si
            # (o vao sintetico de correr cancela esse fator internamente -
            # largura_pdf = largura_m_errado * escala_errada da certo do
            # mesmo jeito), mas quebrava escala_pts_por_m usado pelos AMBIENTES
            # (extrair_ambientes.py), que comparam contra area_m2 real (sem
            # esse mesmo cancelamento) - resultado: 0 pontos associados a
            # ambiente nenhum, mesmo com 54 ambientes corretamente detectados
            # (raio_fis saia ~100x menor que o real). Superset estrito: so'
            # divide por 100 quando o valor bruto e' implausivel em metros;
            # plantas que ja escrevem em metros com virgula ("0,80") nao mudam.
            if largura_m > 10:
                largura_m /= 100.0
            if altura_m > 10:
                altura_m /= 100.0
            esquadrias[tok] = dict(
                largura_m=largura_m, altura_m=altura_m,
                quantidade=(int(qtd_s) if qtd_s is not None else None), tipo=tipo)
    return esquadrias


def _segmentos_retos(page, limite_x):
    """Segmentos de linha reta, DEDUPLICADOS (o PDF as vezes desenha a mesma
    linha 2x - traco duplo, hachura sobreposta etc.)."""
    vistos = set()
    segs = []
    for dr in page.get_drawings():
        for it in dr['items']:
            if it[0] != 'l':
                continue
            p0, p1 = (it[1].x, it[1].y), (it[2].x, it[2].y)
            if max(p0[0], p1[0]) > limite_x:
                continue
            comprimento = np.hypot(p1[0]-p0[0], p1[1]-p0[1])
            if comprimento < 5:
                continue
            chave = tuple(sorted([(round(p0[0]), round(p0[1])), (round(p1[0]), round(p1[1]))]))
            if chave in vistos:
                continue
            vistos.add(chave)
            segs.append(dict(p0=p0, p1=p1, comprimento=comprimento))
    return segs


def _achar_angulo_parede(lx, ly, segs, raio=150.0, separacao_min=3.0, separacao_max=40.0, dif_ang_max=8.0):
    """Acha o par de linhas quase-paralelas mais proximo do rotulo (2 faces da
    parede). separacao_min exige 2 tracos DIFERENTES (nao a mesma linha
    duplicada). Retorna (angulo_graus, ok)."""
    candidatos = []
    for s in segs:
        mx, my = (s['p0'][0]+s['p1'][0])/2, (s['p0'][1]+s['p1'][1])/2
        dist = np.hypot(mx-lx, my-ly)
        if dist > raio:
            continue
        ang = np.degrees(np.arctan2(s['p1'][1]-s['p0'][1], s['p1'][0]-s['p0'][0])) % 180
        candidatos.append(dict(s, dist=dist, ang=ang, mx=mx, my=my))
    candidatos.sort(key=lambda c: c['dist'])

    melhor = None
    for i in range(len(candidatos)):
        for j in range(i+1, len(candidatos)):
            a, b = candidatos[i], candidatos[j]
            dif_ang = min(abs(a['ang']-b['ang']), 180-abs(a['ang']-b['ang']))
            if dif_ang > dif_ang_max:
                continue
            sep = np.hypot(a['mx']-b['mx'], a['my']-b['my'])
            if sep < separacao_min or sep > separacao_max:
                continue
            if melhor is None or sep < melhor[3]:
                melhor = (a, b, dif_ang, sep)
    if melhor is None:
        return None, False
    return melhor[0]['ang'], True


def extrair_vaos_correr(page, vaos_arco, labels, W, H, limite_x):
    """Monta vaos SINTETICOS (retos) pras portas tipo CORRER da tabela de
    esquadrias, que nao tem arco de abertura. Levanta excecao se nao conseguir
    calibrar escala (chamador deve capturar e seguir so com os arcos)."""
    esquadrias = _parsear_esquadrias(page)

    # auto-calibracao pts-por-metro: portas com arco JA TEM raio_pdf (pontos) -
    # cruzando com a largura_m (metros) da tabela pro MESMO codigo, a razao da
    # a escala do desenho sem precisar assumir escala de impressao (1:50 etc.)
    razoes = [v['raio_pdf'] / esquadrias[v['nome']]['largura_m']
              for v in vaos_arco
              if v['nome'] in esquadrias and esquadrias[v['nome']]['largura_m'] > 0]
    if not razoes:
        raise RuntimeError("nenhum codigo com arco E tabela de esquadrias em comum - sem escala pra calibrar")
    escala_pts_por_m = float(np.median(razoes))

    # filtro de legenda: 3+ codigos DIFERENTES na mesma coluna X = icone de
    # legenda da tabela, nao instancia real de porta no desenho
    grupos_x = {}
    for lx, ly, nome in labels:
        gx = round(lx / 5) * 5
        grupos_x.setdefault(gx, set()).add(nome)
    xs_legenda = {gx for gx, nomes in grupos_x.items() if len(nomes) >= 3}

    segs = _segmentos_retos(page, limite_x)
    codigos_correr = {c for c, d in esquadrias.items() if d['tipo'] in TIPOS_CORRER}

    novos = []
    for lx, ly, nome in labels:
        if nome not in codigos_correr or round(lx / 5) * 5 in xs_legenda:
            continue
        ang, ok = _achar_angulo_parede(lx, ly, segs)
        if not ok:
            continue
        largura_pdf = esquadrias[nome]['largura_m'] * escala_pts_por_m
        rad = np.radians(ang)
        dx, dy = np.cos(rad), np.sin(rad)
        p0 = (lx - dx*largura_pdf/2, ly - dy*largura_pdf/2)
        p1 = (lx + dx*largura_pdf/2, ly + dy*largura_pdf/2)
        novos.append(dict(
            nome=nome,
            hinge=[p0[0]/W, p0[1]/H],
            extremos=[[p1[0]/W, p1[1]/H]],
            raio_pdf=largura_pdf/2))
    return novos


def extrair(pdf_path, limite_x=None, r_min=25, r_max=130, span_min=55, cap_rotulo=90):
    doc = fitz.open(pdf_path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    if limite_x is None:
        limite_x = W  # sem filtro

    # {0,2} (nao {1,2}): cobre tambem codigos SEM letra apos o "P" (ex.: P55,
    # P80, P90A - convencao usada na planta P070, validado em 2026-07-14),
    # alem dos ja suportados PM1/PJ3/PCF1 etc. (com 1-2 letras do conjunto
    # [MJUCAF]). E' um superset estritamente mais permissivo do regex antigo -
    # nao quebra nenhum codigo que ja casava - e o risco de falso-positivo
    # (um "P"+numero que nao seja porta) e' contido pela etapa de pareamento
    # rotulo<->arco logo abaixo: um rotulo so vira "vao" se houver um arco
    # DETECTADO geometricamente por perto (cap_rotulo), entao rotulo espurio
    # sem arco vizinho simplesmente nao gera porta nenhuma.
    pat = re.compile(r'^(P[MJUCAF]{0,2}\d+[A-Z]?)$')
    labels = [((w[0]+w[2])/2, (w[1]+w[3])/2, w[4])
              for w in page.get_text("words")
              if pat.match(w[4]) and (w[0]+w[2])/2 < limite_x]

    # inventario de arcos: polilinhas nativas ('l') OU curvas de Bezier ('c')
    # por path, com curvatura consistente. Algumas plantas desenham o arco de
    # abertura como segmentos retos (polilinha aproximando o circulo), outras
    # como 1-2 curvas de Bezier nativas (ver _bezier_pts acima) - trata os
    # dois tipos de item de forma uniforme aqui, encadeando por proximidade
    # de ponta igual ja se fazia so com 'l', pra reaproveitar o mesmo
    # fit_circle/checagem de span logo abaixo pros dois casos.
    arcs = []
    for dr in page.get_drawings():
        seqs, cur = [], []
        for it in dr['items']:
            if it[0] == 'l':
                p0, p1 = (it[1].x, it[1].y), (it[2].x, it[2].y)
                novos_pts = [p0, p1]
            elif it[0] == 'c':
                p0 = (it[1].x, it[1].y)
                p1c, p2c = (it[2].x, it[2].y), (it[3].x, it[3].y)
                p3 = (it[4].x, it[4].y)
                novos_pts = _bezier_pts(p0, p1c, p2c, p3)
            else:
                if len(cur) >= 6: seqs.append(cur)
                cur = []; continue
            p0 = novos_pts[0]
            if cur and np.hypot(cur[-1][0]-p0[0], cur[-1][1]-p0[1]) < 0.6:
                cur.extend(novos_pts[1:])
            else:
                if len(cur) >= 6: seqs.append(cur)
                cur = list(novos_pts)
        if len(cur) >= 6: seqs.append(cur)
        for seq in seqs:
            pts = np.array(seq)
            if pts[:, 0].max() > limite_x: continue
            v = np.diff(pts, axis=0)
            ln = np.hypot(v[:, 0], v[:, 1])
            if ln.max() > 30 or ln.sum() < 20: continue
            cross = v[:-1, 0]*v[1:, 1] - v[:-1, 1]*v[1:, 0]
            nz = cross[np.abs(cross) > 1e-7]
            if len(nz) < 4 or not (np.all(nz > 0) or np.all(nz < 0)): continue
            cx, cy, r, res = fit_circle(pts)
            if not (r_min < r < r_max) or res > 2.0: continue
            a0 = np.arctan2(pts[0, 1]-cy, pts[0, 0]-cx)
            a1 = np.arctan2(pts[-1, 1]-cy, pts[-1, 0]-cx)
            span = np.degrees(abs((a1-a0+np.pi) % (2*np.pi) - np.pi))
            if span < span_min: continue
            arcs.append(dict(cx=cx, cy=cy, r=r, span=span,
                             e0=pts[0].tolist(), e1=pts[-1].tolist()))

    uniq = []
    for a in arcs:
        if not any(np.hypot(a['cx']-u['cx'], a['cy']-u['cy']) < 8
                   and abs(a['r']-u['r']) < 6 for u in uniq):
            uniq.append(a)

    # associacao rotulo<->arco (gulosa com teto)
    pares = []
    if labels and uniq:
        dist = np.array([[np.hypot(l[0]-a['cx'], l[1]-a['cy']) for a in uniq]
                         for l in labels])
        for _ in range(min(len(labels), len(uniq))):
            i, j = np.unravel_index(np.argmin(dist), dist.shape)
            if dist[i, j] > cap_rotulo: break
            pares.append((labels[i], uniq[j]))
            dist[i, :] = 1e9; dist[:, j] = 1e9

    vaos = []
    for (lx, ly, name), a in pares:
        h = [a['cx'], a['cy']]
        vaos.append(dict(
            nome=name,
            hinge=[h[0]/W, h[1]/H],
            extremos=[[a['e0'][0]/W, a['e0'][1]/H], [a['e1'][0]/W, a['e1'][1]/H]],
            raio_pdf=a['r']))

    # Portas de correr/sacada (tipo CORRER na tabela de esquadrias, ex.: PJ) -
    # nao tem arco de abertura pra detectar (deslizam, nao giram) - ver
    # extrair_vaos_correr acima. Falha aberta: se a tabela de esquadrias nao
    # existir/nao casar nesse PDF, so segue com os arcos (comportamento antigo).
    try:
        vaos_correr = extrair_vaos_correr(page, vaos, labels, W, H, limite_x)
        vaos.extend(vaos_correr)
    except Exception as e:
        print(f"[extrair_portas] [AVISO] deteccao de portas de correr (tabela de esquadrias) "
              f"nao disponivel neste PDF ({e}) - seguindo so com portas de arco.")

    return vaos, len(uniq), (W, H)


def main():
    ap = argparse.ArgumentParser(description="Extrai portas de PDF vetorial de planta")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default="portas.json")
    ap.add_argument("--limite-x", type=float, default=None,
                    help="ignora conteudo a direita deste x em pts (carimbo/tabelas)")
    args = ap.parse_args()
    vaos, n_arcs, (W, H) = extrair(args.pdf, args.limite_x)
    saida = {"pagina": {"largura": W, "altura": H, "aspecto": H / W}, "vaos": vaos}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=1)
    print(f"pagina {W:.0f}x{H:.0f} | arcos unicos: {n_arcs} | portas associadas: {len(vaos)}")
    print(f"salvo em: {args.out}")


if __name__ == "__main__":
    main()
