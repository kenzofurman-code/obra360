# pdf_extractor.py
"""
Extrai vãos de portas (PM, PJ, PA, PCF) de uma prancha PDF de obra.
Pode ser usado como script CLI ou importado por outros módulos.

Uso CLI:
    python pdf_extractor.py --pdf caminho/para/planta.pdf --out passagens.json

Uso como módulo:
    from pdf_extractor import extract_doors
    passagens = extract_doors("planta.pdf")
"""
import argparse
import json
import math
import os
import re
import sys


def _detect_bounds(page):
    """
    Detecta automaticamente os limites do CropBox da página PDF.
    Retorna (x_min, x_max, y_min, y_max).
    """
    bbox = page.bbox  # (x0, top, x1, bottom) em pdfplumber
    return bbox[0], bbox[2], bbox[1], bbox[3]


def normalizar_coordenada(x_raw, y_raw, x_min, x_max, y_min, y_max):
    """Converte coordenadas brutas do PDF para o espaço [0, 1] do canvas da planta."""
    width = x_max - x_min
    height = y_max - y_min
    x_norm = (x_raw - x_min) / width if width > 0 else 0
    y_norm = (y_raw - y_min) / height if height > 0 else 0
    return round(x_norm, 6), round(y_norm, 6)


def extract_doors(pdf_path: str) -> list:
    """
    Abre um PDF de planta baixa de obra e extrai vãos de passagem
    (portas de madeira PM, porta-janela PJ, porta de alumínio PA, porta corta-fogo PCF).

    Retorna lista de dicts com:
      { id, codigo, tipo, x_norm, y_norm, metodo_alinhamento, dist_referencia_pontos }
    """
    try:
        import pdfplumber
    except ImportError:
        print("[ERRO] pdfplumber não instalado. Execute: pip install pdfplumber")
        sys.exit(1)

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF não encontrado: {pdf_path}")

    # Regex para buscar PM, PJ, PA, PCF
    pattern = re.compile(r'^(PM\d*|PJ\d*|PA\d*|PCF\d*)$', re.IGNORECASE)

    passagens = []

    print(f"[pdf_extractor] Abrindo: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        x_min, x_max, y_min, y_max = _detect_bounds(page)

        print(f"[pdf_extractor] Bounds detectados: x=[{x_min:.1f}, {x_max:.1f}] y=[{y_min:.1f}, {y_max:.1f}]")

        words = page.extract_words()
        curves = page.curves

        print(f"[pdf_extractor] Palavras: {len(words)} | Curvas: {len(curves)}")

        for w in words:
            text = w['text'].strip()
            if not pattern.match(text):
                continue

            x_text_raw = (w['x0'] + w['x1']) / 2
            y_text_raw = (w['top'] + w['bottom']) / 2

            x_text_norm, y_text_norm = normalizar_coordenada(
                x_text_raw, y_text_raw, x_min, x_max, y_min, y_max)

            # Ignora legendas e tabelas no lado direito da prancha
            if x_text_norm >= 0.45:
                continue

            # Busca curva (arco de porta) mais próxima em raio de 60pt PDF
            closest_curve = None
            min_dist = float('inf')

            for curve in curves:
                x_curve_raw = (curve['x0'] + curve['x1']) / 2
                y_curve_raw = (curve['top'] + curve['bottom']) / 2
                dist = math.sqrt((x_text_raw - x_curve_raw) ** 2 +
                                 (y_text_raw - y_curve_raw) ** 2)
                if dist < min_dist and dist < 60:
                    min_dist = dist
                    closest_curve = curve

            if closest_curve:
                x_center_raw = (closest_curve['x0'] + closest_curve['x1']) / 2
                y_center_raw = (closest_curve['top'] + closest_curve['bottom']) / 2
                tipo_metodo = "geometria_arco"
            else:
                x_center_raw = x_text_raw
                y_center_raw = y_text_raw
                tipo_metodo = "texto_referencia"

            x_center_norm, y_center_norm = normalizar_coordenada(
                x_center_raw, y_center_raw, x_min, x_max, y_min, y_max)

            passagens.append({
                "id": f"{text}_{len(passagens)+1:02d}",
                "codigo": text,
                "tipo": "porta_janela" if text.upper().startswith("PJ") else "porta",
                "x_norm": x_center_norm,
                "y_norm": y_center_norm,
                "metodo_alinhamento": tipo_metodo,
                "dist_referencia_pontos": round(min_dist, 2) if closest_curve else None
            })

    print(f"[pdf_extractor] Extraídas: {len(passagens)} passagens")
    return passagens


def main():
    parser = argparse.ArgumentParser(
        description="Extrai vãos de portas de uma prancha PDF de obra.")
    parser.add_argument("--pdf", required=True, help="Caminho para o arquivo PDF da planta")
    parser.add_argument("--out", default="planta_passagens.json",
                        help="Arquivo JSON de saída (padrão: planta_passagens.json)")
    args = parser.parse_args()

    passagens = extract_doors(args.pdf)

    print(f"\nExtração concluída: {len(passagens)} passagens identificadas.")
    for p in sorted(passagens, key=lambda x: x['id'])[:15]:
        print(f"  {p['id']:<12} ({p['x_norm']:.4f}, {p['y_norm']:.4f})  [{p['metodo_alinhamento']}]")

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(passagens, f, indent=2, ensure_ascii=False)
    print(f"\nPassagens salvas em: {args.out}")


if __name__ == '__main__':
    main()
