# -*- coding: utf-8 -*-
# visualizar_ambientes.py
# Script de DIAGNOSTICO/VISUALIZACAO (nao mexe em nada do pipeline real) - gera
# um PNG com um CIRCULO de "alcance" ao redor de cada ambiente detectado
# (nome + area m² - ver debug_ambientes.py), com RAIO REAL calculado a partir
# da area do proprio ambiente: um circulo cuja AREA (pi*r²) e' igual a area
# real do comodo em m² - nao e' uma proporcao arbitraria, e' a mesma area
# convertida em raio (r = sqrt(area/pi)), na escala real do desenho.
#
# A escala pts-por-metro reusa a MESMA auto-calibracao de extrair_portas.py:
# compara o raio (em pontos do PDF) das portas com arco contra a largura real
# (em metros) delas na tabela de esquadrias - nao precisa assumir a escala de
# impressao da planta (1:50, 1:100 etc.).
#
# Uso: python visualizar_ambientes.py planta.pdf [--out visual_ambientes.png] [--dpi 150]

import os
import sys
import re
import argparse
import numpy as np
import cv2
import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extrair_portas import extrair as extrair_portas, _parsear_esquadrias

PAT_AREA_JUNTO = re.compile(r'^(\d+,\d+)\s*m[²2]$', re.IGNORECASE)
PAT_NUM = re.compile(r'^\d+,\d+$')
PAT_M2 = re.compile(r'^m[²2]\.?$', re.IGNORECASE)
PAT_NUMERICO = re.compile(r'^[+\-]?\d+([.,]\d+)?$')
PAT_CODIGO_PORTA = re.compile(r'^(P[MJUCAF]{1,2}\d+[A-Z]?)$')
PALAVRAS_RUIDO = {'ACAB', 'OSSO', 'CONTRAP', 'FINAL', 'M2', 'M²'}


def eh_ruido(texto):
    t = texto.upper().rstrip('.')
    return bool(PAT_NUMERICO.match(texto) or PAT_CODIGO_PORTA.match(texto)) or t in PALAVRAS_RUIDO


def extrair_ambientes(page, raio_nome=60.0):
    """Mesma logica validada em debug_ambientes.py v2: acha valores de area
    (m²) e propoe o nome do ambiente juntando as palavras nao-ruido mais
    proximas ACIMA da area."""
    words = page.get_text("words")
    areas = []
    for i, w in enumerate(words):
        texto = w[4]
        m = PAT_AREA_JUNTO.match(texto)
        if m:
            cx, cy = (w[0]+w[2])/2, (w[1]+w[3])/2
            areas.append((cx, cy, float(m.group(1).replace(',', '.')), {i}))
        elif PAT_M2.match(texto) and i > 0 and PAT_NUM.match(words[i-1][4]):
            num_w = words[i-1]
            cx, cy = (num_w[0]+w[2])/2, (num_w[1]+w[3])/2
            areas.append((cx, cy, float(num_w[4].replace(',', '.')), {i-1, i}))

    ambientes = []
    for cx, cy, valor_m2, ignorar in areas:
        candidatos = []
        for j, w in enumerate(words):
            if j in ignorar or eh_ruido(w[4]):
                continue
            wx, wy = (w[0]+w[2])/2, (w[1]+w[3])/2
            dist = np.hypot(wx-cx, wy-cy)
            dy = cy - wy
            if dy <= 3 or dist > raio_nome:
                continue
            candidatos.append((dist, w[4], wx, wy, w[5], w[6]))
        candidatos.sort(key=lambda c: c[0])
        if not candidatos:
            continue
        limite = candidatos[0][0] + 15
        grupo = sorted([c for c in candidatos if c[0] <= limite], key=lambda c: (c[5], c[2]))
        nome = ' '.join(c[1] for c in grupo)
        ambientes.append(dict(nome=nome, area_m2=valor_m2, cx=cx, cy=cy))
    return ambientes


def rasterizar_pdf(page, dpi=150):
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR), zoom


def main():
    ap = argparse.ArgumentParser(description="Visualiza circulos de alcance dos ambientes (raio = area real).")
    ap.add_argument("pdf")
    ap.add_argument("--out", default="visual_ambientes.png")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--pagina", type=int, default=0)
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    page = doc[args.pagina]
    img, zoom = rasterizar_pdf(page, args.dpi)

    # escala pts/metro - mesma auto-calibracao de extrair_portas.py (arco vs tabela)
    vaos_arco, n_arcs, _ = extrair_portas(args.pdf)
    esquadrias = _parsear_esquadrias(page)
    razoes = [v['raio_pdf'] / esquadrias[v['nome']]['largura_m']
              for v in vaos_arco
              if v['nome'] in esquadrias and esquadrias[v['nome']]['largura_m'] > 0]
    if not razoes:
        print("[ERRO] Nao foi possivel auto-calibrar escala (sem porta com arco+tabela em comum). Abortando.")
        return
    escala_pts_por_m = float(np.median(razoes))
    print(f"Escala auto-calibrada: {escala_pts_por_m:.1f} pt/m (a partir de {len(razoes)} porta(s))")

    ambientes = extrair_ambientes(page)
    print(f"{len(ambientes)} ambiente(s) detectado(s)\n")

    cores = [
        (66, 135, 245), (66, 194, 245), (66, 245, 194), (99, 245, 66), (194, 245, 66),
        (245, 220, 66), (245, 152, 66), (245, 66, 108), (194, 66, 245), (135, 66, 245),
    ]

    for i, a in enumerate(ambientes):
        raio_m = np.sqrt(a['area_m2'] / np.pi)
        raio_pdf = raio_m * escala_pts_por_m
        cx_px, cy_px = int(round(a['cx'] * zoom)), int(round(a['cy'] * zoom))
        raio_px = int(round(raio_pdf * zoom))
        cor = cores[i % len(cores)]

        overlay = img.copy()
        cv2.circle(overlay, (cx_px, cy_px), raio_px, cor, -1, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.20, img, 0.80, 0, img)
        cv2.circle(img, (cx_px, cy_px), raio_px, cor, 2, cv2.LINE_AA)
        cv2.circle(img, (cx_px, cy_px), 3, (0, 0, 0), -1, cv2.LINE_AA)

        rotulo = f"{a['nome']} ({a['area_m2']:.1f}m², r={raio_m:.2f}m)"
        cv2.putText(img, rotulo, (cx_px - 40, cy_px - raio_px - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (20, 20, 20), 3, cv2.LINE_AA)
        cv2.putText(img, rotulo, (cx_px - 40, cy_px - raio_px - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        print(f"  {a['nome']:<25s} {a['area_m2']:>6.2f}m² -> raio={raio_m:.2f}m ({raio_px}px)")

    cv2.imwrite(args.out, img)
    print(f"\nPNG salvo em: {args.out}")
    doc.close()


if __name__ == '__main__':
    main()
