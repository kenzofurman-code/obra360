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


def extrair(pdf_path, limite_x=None, r_min=25, r_max=130, span_min=55, cap_rotulo=90):
    doc = fitz.open(pdf_path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    if limite_x is None:
        limite_x = W  # sem filtro

    pat = re.compile(r'^(P[MJUCAF]\d+[A-Z]?)$')
    labels = [((w[0]+w[2])/2, (w[1]+w[3])/2, w[4])
              for w in page.get_text("words")
              if pat.match(w[4]) and (w[0]+w[2])/2 < limite_x]

    # inventario de arcos: polilinhas nativas por path com curvatura consistente
    arcs = []
    for dr in page.get_drawings():
        seqs, cur = [], []
        for it in dr['items']:
            if it[0] != 'l':
                if len(cur) >= 6: seqs.append(cur)
                cur = []; continue
            p0, p1 = (it[1].x, it[1].y), (it[2].x, it[2].y)
            if cur and np.hypot(cur[-1][0]-p0[0], cur[-1][1]-p0[1]) < 0.6:
                cur.append(p1)
            else:
                if len(cur) >= 6: seqs.append(cur)
                cur = [p0, p1]
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
