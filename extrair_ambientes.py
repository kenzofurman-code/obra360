# -*- coding: utf-8 -*-
# extrair_ambientes.py
# Extrai ambientes (nome + area m²) de um PDF vetorial de planta baixa, pra
# associar cada foto/frame da trajetoria do Obra360 ao comodo onde foi tirada
# (e futuramente estimar progresso de obra por ambiente/pavimento/obra via
# analise de IA das fotos - ver discussao com o Pedro em 2026-07-14).
#
# Metodo (validado com o Pedro via debug_ambientes.py/visualizar_ambientes.py):
#   1. Acha todo valor de area "NUMERO m²" no texto do PDF.
#   2. O nome do ambiente e' SEMPRE a(s) palavra(s) mais proxima(s) ACIMA da
#      area (nunca abaixo - cotas/anotacoes de obra como "-", "+18,52",
#      "Acab." ficam abaixo), excluindo numeros puros e codigos de porta.
#   3. O "raio de alcance" de cada ambiente e' derivado da propria area real
#      (m²): um circulo cuja AREA (pi*r²) e' igual a area do comodo -
#      raio_m = sqrt(area_m2/pi). A escala pts-por-metro reusa a MESMA
#      auto-calibracao de extrair_portas.py (raio_pdf das portas com arco vs
#      largura_m da tabela de esquadrias) - nao precisa assumir escala de
#      impressao (1:50, 1:100 etc.).
#
# Uso: python extrair_ambientes.py --pdf planta.pdf --out ambientes.json

import os
import sys
import re
import json
import argparse
import numpy as np
import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extrair_portas import extrair as extrair_portas, _parsear_esquadrias

PAT_AREA_JUNTO = re.compile(r'^(\d+,\d+)\s*m[²2]$', re.IGNORECASE)   # "11,11m²" num token so
PAT_NUM = re.compile(r'^\d+,\d+$')                                    # "11,11"
PAT_M2 = re.compile(r'^m[²2]\.?$', re.IGNORECASE)                     # "m²" separado
# "A=8,28m2" - convencao usada na planta P070 (2026-07-15): prefixo "A=" colado
# no numero, e "m2" com "2" normal em vez do simbolo "²" - nenhum dos 2
# padroes acima casava (PAT_AREA_JUNTO exige o token COMECAR com digito, sem
# prefixo "A="), entao essa planta extraia 0 ambientes. Testado contra o PDF
# real: 55 areas encontradas (0 antes).
PAT_AREA_PREFIXO = re.compile(r'^A=(\d+,\d+)\s*m[²2]\.?$', re.IGNORECASE)

PAT_NUMERICO = re.compile(r'^[+\-]?\d+([.,]\d+)?$')                  # numero puro (cota/dimensao)
PAT_CODIGO_PORTA = re.compile(r'^(P[MJUCAF]{0,2}\d+[A-Z]?)$')        # mesmo regex de extrair_portas.py
PALAVRAS_RUIDO = {'ACAB', 'OSSO', 'CONTRAP', 'FINAL', 'M2', 'M²'}


def _eh_ruido(texto):
    t = texto.upper().rstrip('.')
    return bool(PAT_NUMERICO.match(texto) or PAT_CODIGO_PORTA.match(texto)) or t in PALAVRAS_RUIDO


def _nomear_ambientes(page, raio_nome=60.0):
    """Acha valores de area (m²) e propoe o nome do ambiente juntando as
    palavras nao-ruido mais proximas ACIMA da area. Retorna lista de
    {nome, area_m2, cx, cy} em coordenadas PDF (pontos), nao normalizadas."""
    words = page.get_text("words")
    areas = []
    for i, w in enumerate(words):
        texto = w[4]
        m = PAT_AREA_JUNTO.match(texto)
        mp = PAT_AREA_PREFIXO.match(texto)
        if m:
            cx, cy = (w[0]+w[2])/2, (w[1]+w[3])/2
            areas.append((cx, cy, float(m.group(1).replace(',', '.')), {i}))
        elif mp:
            cx, cy = (w[0]+w[2])/2, (w[1]+w[3])/2
            areas.append((cx, cy, float(mp.group(1).replace(',', '.')), {i}))
        elif PAT_M2.match(texto) and i > 0 and PAT_NUM.match(words[i-1][4]):
            num_w = words[i-1]
            cx, cy = (num_w[0]+w[2])/2, (num_w[1]+w[3])/2
            areas.append((cx, cy, float(num_w[4].replace(',', '.')), {i-1, i}))

    ambientes = []
    for cx, cy, valor_m2, ignorar in areas:
        candidatos = []
        for j, w in enumerate(words):
            if j in ignorar or _eh_ruido(w[4]):
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


def extrair(pdf_path):
    """Retorna (ambientes, (W, H)). Cada ambiente: {nome, area_m2, x, y,
    raio_fis} - x,y normalizados [0,1] (mesma convencao dos vaos de porta);
    raio_fis ja em unidades FISICAS normalizadas (pdf_pontos/W) - pronto pra
    comparar direto contra waypoints alinhados em processar_vistoria.py
    (fisico = x normalizado, y normalizado * aspecto - ambos equivalem a
    coordenada_pdf/W, ver nota em alinhar_ponto)."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height

    # escala pts/metro - mesma auto-calibracao usada pras portas de correr
    # em extrair_portas.py (arco com raio_pdf conhecido vs largura_m da tabela)
    vaos_arco, n_arcs, _ = extrair_portas(pdf_path)
    esquadrias = _parsear_esquadrias(page)
    razoes = [v['raio_pdf'] / esquadrias[v['nome']]['largura_m']
              for v in vaos_arco
              if v['nome'] in esquadrias and esquadrias[v['nome']]['largura_m'] > 0]

    ambientes_brutos = _nomear_ambientes(page)

    if not razoes:
        print("[extrair_ambientes] [AVISO] nao foi possivel auto-calibrar escala pts/metro "
              "(sem porta com arco+tabela em comum) - ambientes sairao SEM raio_fis (None).")
        escala_pts_por_m = None
    else:
        escala_pts_por_m = float(np.median(razoes))

    ambientes = []
    for a in ambientes_brutos:
        raio_m = float(np.sqrt(a['area_m2'] / np.pi))
        raio_fis = (raio_m * escala_pts_por_m / W) if escala_pts_por_m else None
        ambientes.append(dict(
            nome=a['nome'], area_m2=a['area_m2'],
            x=a['cx']/W, y=a['cy']/H,
            raio_m=raio_m, raio_fis=raio_fis))

    doc.close()
    return ambientes, (W, H)


def main():
    ap = argparse.ArgumentParser(description="Extrai ambientes (nome+area m²) de PDF vetorial de planta")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default="ambientes.json")
    args = ap.parse_args()
    ambientes, (W, H) = extrair(args.pdf)
    saida = {"pagina": {"largura": W, "altura": H, "aspecto": H/W}, "ambientes": ambientes}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=1, ensure_ascii=False)
    print(f"pagina {W:.0f}x{H:.0f} | {len(ambientes)} ambiente(s) extraido(s)")
    print(f"salvo em: {args.out}")


if __name__ == "__main__":
    main()
