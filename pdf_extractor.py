# pdf_extractor.py
import pdfplumber
import re
import json
import math
import os

# Caminho da prancha PDF da obra
PDF_PATH = r"C:\Users\HomePC\Downloads\01 - Obras e Orcamentos\P073-ARQ-EX-010-R04-6PAV.pdf"

# Limites físicos do CropBox da página (em pontos PDF)
X_MIN, X_MAX = -1913.34, 1913.34
Y_MIN, Y_MAX = 1191.96, 3575.88
WIDTH = X_MAX - X_MIN
HEIGHT = Y_MAX - Y_MIN

def normalizar_coordenada(x_raw, y_raw):
    """Converte as coordenadas brutas do PDF para o espaço [0, 1] do canvas da planta."""
    x_norm = (x_raw - X_MIN) / WIDTH
    y_norm = (y_raw - Y_MIN) / HEIGHT
    return round(x_norm, 6), round(y_norm, 6)

def extrair_passagens(pdf_path):
    print(f"Abrindo folha de engenharia: {pdf_path}")
    
    # Regex para buscar PM (porta de madeira), PJ (porta-janela), PA (porta de alumínio), PCF (porta corta-fogo)
    pattern = re.compile(r'^(PM\d+|PJ\d+|PA\d+|PCF)$', re.IGNORECASE)
    
    passagens = []
    
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        
        print("Lendo elementos vetoriais...")
        words = page.extract_words()
        curves = page.curves
        
        print(f"Total de palavras na folha: {len(words)}")
        print(f"Total de curvas (arcos de portas, etc.): {len(curves)}")
        
        # Filtra apenas textos na planta (metade esquerda do PDF, x_norm < 0.45)
        for w in words:
            text = w['text'].strip()
            if pattern.match(text):
                x_text_raw = (w['x0'] + w['x1']) / 2
                y_text_raw = (w['top'] + w['bottom']) / 2
                
                x_text_norm, y_text_norm = normalizar_coordenada(x_text_raw, y_text_raw)
                
                # Ignora legendas e tabelas da prancha localizadas no lado direito
                if x_text_norm >= 0.45:
                    continue
                
                # Busca heurística por curvas próximas (arco que define o vão físico da porta)
                closest_curve = None
                min_dist = float('inf')
                
                # Procura curvas em um raio geométrico de 60 pontos PDF (~2 cm na folha impressa)
                for curve in curves:
                    x_curve_raw = (curve['x0'] + curve['x1']) / 2
                    y_curve_raw = (curve['top'] + curve['bottom']) / 2
                    
                    dist = math.sqrt((x_text_raw - x_curve_raw) ** 2 + (y_text_raw - y_curve_raw) ** 2)
                    if dist < min_dist and dist < 60:
                        min_dist = dist
                        closest_curve = curve
                
                # Se encontrou um arco geométrico de porta, o centro geométrico real do vão 
                # é aproximado pelas extremidades da curva (arco do CAD)
                if closest_curve:
                    x_center_raw = (closest_curve['x0'] + closest_curve['x1']) / 2
                    y_center_raw = (closest_curve['top'] + closest_curve['bottom']) / 2
                    tipo_metodo = "geometria_arco"
                else:
                    # Caso contrário, usa a coordenada aproximada do próprio texto identificador
                    x_center_raw = x_text_raw
                    y_center_raw = y_text_raw
                    tipo_metodo = "texto_referencia"
                
                x_center_norm, y_center_norm = normalizar_coordenada(x_center_raw, y_center_raw)
                
                # Cria a passagem no modelo de dados
                passagens.append({
                    "id": f"{text}_{len(passagens)+1:02d}",
                    "codigo": text,
                    "tipo": "porta_janela" if text.upper().startswith("PJ") else "porta",
                    "x_norm": x_center_norm,
                    "y_norm": y_center_norm,
                    "metodo_alinhamento": tipo_metodo,
                    "dist_referencia_pontos": round(min_dist, 2) if closest_curve else None
                })
                
    return passagens

def main():
    if not os.path.exists(PDF_PATH):
        print(f"[ERRO] Arquivo PDF não encontrado em: {PDF_PATH}")
        return
        
    passagens = extrair_passagens(PDF_PATH)
    
    print(f"\nExtração concluída: {len(passagens)} passagens identificadas na planta.")
    
    # Exibe amostra das portas extraídas
    for p in sorted(passagens, key=lambda x: x['id'])[:15]:
        print(f"ID: {p['id']:<10} Tipo: {p['tipo']:<12} Coord: ({p['x_norm']:.4f}, {p['y_norm']:.4f}) Método: {p['metodo_alinhamento']}")
        
    # Salva o arquivo final de passagens
    output_json = r"c:\Users\HomePC\.gemini\antigravity-ide\scratch\video360-obras-app\planta_passagens.json"
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(passagens, f, indent=2, ensure_ascii=False)
    
    print(f"\nGrafo de passagens salvo com sucesso em: {output_json}")

if __name__ == '__main__':
    main()
