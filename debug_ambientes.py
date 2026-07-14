# -*- coding: utf-8 -*-
# debug_ambientes.py
# Script de DIAGNOSTICO (nao mexe em nada do pipeline) pra descobrir o formato
# dos rotulos de ambiente (ex.: "ESTAR/JANTAR" + "11,11m²" logo abaixo) no PDF
# da planta - primeiro passo pra depois associar cada foto/frame da trajetoria
# ao ambiente mais proximo.
#
# v2: confirmado num teste real com o Pedro - o nome do ambiente fica SEMPRE
# ACIMA da area (nunca abaixo), e as cotas/anotacoes de obra ("-", "+18,52",
# "Acab.", "Osso", "Contrap.") ficam quase sempre ABAIXO. Entao o filtro chave
# nao e' so distancia: e' "acima" + nao ser numero puro + nao ser codigo de
# porta (PJ5, PM2 etc.) - daí junta as palavras que sobraram (nomes de
# ambiente multi-palavra: "AREA DE SERVICO", "SAC. TEC.", "ESCADA
# PRESSURIZADA") em ordem de leitura.
#
# Uso: python debug_ambientes.py planta.pdf [--pagina 0] [--raio 60]

import fitz
import re
import argparse
import numpy as np

PAT_AREA_JUNTO = re.compile(r'^(\d+,\d+)\s*m[²2]$', re.IGNORECASE)   # "11,11m²" num token so
PAT_NUM = re.compile(r'^\d+,\d+$')                                    # "11,11"
PAT_M2 = re.compile(r'^m[²2]\.?$', re.IGNORECASE)                     # "m²" separado

PAT_NUMERICO = re.compile(r'^[+\-]?\d+([.,]\d+)?$')                  # numero puro (cota/dimensao)
PAT_CODIGO_PORTA = re.compile(r'^(P[MJUCAF]{1,2}\d+[A-Z]?)$')        # mesmo regex de extrair_portas.py
PALAVRAS_RUIDO = {'ACAB', 'OSSO', 'CONTRAP', 'FINAL', 'M2', 'M²'}


def eh_ruido(texto):
    t = texto.upper().rstrip('.')
    if PAT_NUMERICO.match(texto) or PAT_CODIGO_PORTA.match(texto):
        return True
    return t in PALAVRAS_RUIDO


def main():
    ap = argparse.ArgumentParser(description="Diagnostico: acha e propoe o nome do ambiente pra cada area (m²) no PDF.")
    ap.add_argument("pdf")
    ap.add_argument("--pagina", type=int, default=0)
    ap.add_argument("--raio", type=float, default=60.0,
                     help="Raio maximo (pt) pra considerar uma palavra parte do nome do ambiente (padrao 60).")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    page = doc[args.pagina]
    W, H = page.rect.width, page.rect.height
    words = page.get_text("words")  # (x0, y0, x1, y1, "palavra", block_no, line_no, word_no)
    print(f"Pagina {args.pagina}: {W:.0f}x{H:.0f}pt | {len(words)} palavras\n")

    areas = []  # (cx, cy, valor_m2, indices_a_ignorar)
    for i, w in enumerate(words):
        texto = w[4]
        m = PAT_AREA_JUNTO.match(texto)
        if m:
            cx, cy = (w[0]+w[2])/2, (w[1]+w[3])/2
            areas.append((cx, cy, float(m.group(1).replace(',', '.')), {i}))
        elif PAT_M2.match(texto) and i > 0 and PAT_NUM.match(words[i-1][4]):
            num_w = words[i-1]
            cx = (num_w[0]+w[2])/2
            cy = (num_w[1]+w[3])/2
            areas.append((cx, cy, float(num_w[4].replace(',', '.')), {i-1, i}))

    print(f"{len(areas)} valor(es) de area (m²) encontrado(s)\n")

    ok, falhou = 0, 0
    for cx, cy, valor_m2, ignorar in areas:
        candidatos = []
        for j, w in enumerate(words):
            if j in ignorar or eh_ruido(w[4]):
                continue
            wx, wy = (w[0]+w[2])/2, (w[1]+w[3])/2
            dist = np.hypot(wx-cx, wy-cy)
            dy = cy - wy  # positivo = a palavra esta ACIMA da area (y cresce pra baixo no PDF)
            if dy <= 3 or dist > args.raio:
                continue  # so aceita palavra ACIMA (nao numerica/nao porta), dentro do raio
            candidatos.append((dist, w[4], wx, wy, w[5], w[6]))
        candidatos.sort(key=lambda c: c[0])

        if candidatos:
            # pega a palavra mais proxima + qualquer outra dentro de +15pt da
            # distancia dela (pra juntar nomes multi-palavra tipo "SAC. TEC.")
            limite = candidatos[0][0] + 15
            grupo = [c for c in candidatos if c[0] <= limite]
            # ordena em ordem de leitura (linha, depois X) antes de juntar
            grupo.sort(key=lambda c: (c[5], c[2]))
            nome_proposto = ' '.join(c[1] for c in grupo)
            ok += 1
            print(f"{valor_m2:>6.2f}m² @ ({cx:.0f},{cy:.0f}) -> AMBIENTE: '{nome_proposto}'")
        else:
            falhou += 1
            print(f"{valor_m2:>6.2f}m² @ ({cx:.0f},{cy:.0f}) -> [SEM CANDIDATO num raio de {args.raio:.0f}pt]")

    print(f"\n{ok} ambiente(s) nomeado(s) com sucesso, {falhou} sem candidato.")
    doc.close()


if __name__ == '__main__':
    main()
