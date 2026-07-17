# -*- coding: utf-8 -*-
# upscale_quadros.py
# Upscale opcional dos quadros (fotos do tour) por bicubic ou ESPCN - discussao
# com o Pedro em 2026-07-16 sobre usar EDSR/TensorFlow pra "aumentar a
# densidade de todos os frames no backend".
#
# CONCLUSAO DO TESTE REAL (ver CLAUDE.md pra detalhe completo): comparamos
# bicubic (sem rede), ESPCN, LapSRN e EDSR contra o pixel REAL de fotos do
# P070 (reduzindo 4x e reconstruindo, comparando via PSNR/SSIM contra o
# gabarito verdadeiro - nao contra "parece nitido"):
#   - Nenhuma rede recuperou detalhe real medivel em cenas escuras/baixo
#     contraste (tipico de obra em construcao) - bicubic as vezes ganhou.
#   - EDSR custa ~10min/foto em CPU (extrapolado de testes reais 50/100/150px)
#     - inviavel pra ~1000+ fotos por vistoria sem GPU.
#   - LapSRN estourou memoria rodando uma foto inteira de uma vez (precisou
#     tiling manual) - mais pesado que ESPCN sem ganho adicional.
#   - ESPCN foi o unico que sobrou como opcao pratica nesse teste (~0.75s/foto
#     5760x2880 em CPU, modelo de 100KB) - MAS aquele teste alimentava o
#     modelo com uma entrada JA' REDUZIDA (1/4 da foto, pra depois comparar
#     reconstrucao x tamanho original). O uso real aqui e' DIFERENTE: pegar a
#     foto NATIVA (ja' de alta resolucao) e ampliar ainda mais - ou seja, a
#     entrada pro modelo e' a foto inteira, nao uma versao reduzida. Testando
#     isso directamente (--upscale-metodo espcn numa foto nativa 5760x2880),
#     o ESPCN tambem estourou memoria (tentativa de alocar ~4.2GB) - por isso
#     upscale_imagem() abaixo faz TILING automatico (mesma tecnica usada no
#     teste do LapSRN) quando a imagem de entrada e' grande, evitando o pico
#     de memoria de rodar a foto inteira de uma vez.
#     TEMPO REAL com tiling (foto nativa 5760x2880, testado nesta sessao):
#     escala 2x ~8.3s/foto, escala 4x ~12s/foto (numa maquina de 2 vCPU) -
#     BEM mais lento que os 0.75s do teste anterior, porque agora a entrada e'
#     a foto inteira (nao 1/4 dela). Pra uma vistoria com ~1000-1100 fotos,
#     isso soma ~2.3-3.7 HORAS extras so' de upscale - Pedro decidiu testar
#     mesmo assim num video real pra validar na pratica (ver --upscale-metodo
#     no worker.py). Bicubic continua praticamente instantaneo (~0.07s/foto)
#     em qualquer escala, por nao ser rede neural.
#
# Por isso este modulo so' oferece bicubic e ESPCN (EDSR/LapSRN ficaram de
# fora por nao compensarem o custo - ver CLAUDE.md se quiser reconsiderar).
# Nenhum dos dois RECUPERA detalhe que a camera nao capturou - e' upscale
# cosmetico (deixa o zoom no viewer 360 menos pixelado), NAO uma ferramenta
# de medicao. super_resolucao.py (multi-frame real, baseado em pose) continua
# sendo o caminho certo pra quando o detalhe precisa ser confiavel.
#
# Uso standalone (roda numa pasta de quadros JA GERADA, sem reprocessar o
# video inteiro):
#   python upscale_quadros.py --quadros quadros_video/ --metodo espcn --escala 2
#
# Uso integrado: gerar_quadros.py e worker.py tem --upscale-metodo/--upscale-escala
# que chamam upscale_imagem() por quadro, ANTES de gravar o arquivo (upscale
# unico, sem reescrever/reenviar pro R2 duas vezes).

import argparse
import json
import math
import os
import time

import cv2
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELOS_SR_DIR = os.path.join(SCRIPT_DIR, "modelos_sr")
METODOS_VALIDOS = ("bicubic", "espcn")
ESCALAS_ESPCN_DISPONIVEIS = (2, 3, 4)  # modelos_sr/ESPCN_x{2,3,4}.pb

# Limite de pixels de SAIDA por bloco, achado empiricamente (ver aviso no
# topo do arquivo): rodar ESPCN numa entrada de 1440x720 (x4 -> saida
# 5760x2880 = 16.6M px) usou pouca RAM e foi rapido; rodar direto numa
# entrada de 5760x2880 (x2 -> saida 11520x5760 = 66.4M px) estourou ~4.2GB
# e OOMou numa maquina de 3.8GB. 12M fica com boa margem abaixo do caso
# que funcionou bem (16.6M) - se a saida de um quadro passar disso, faz
# tiling automatico (mesma tecnica usada no teste do LapSRN).
_LIMIAR_SAIDA_PX = 12_000_000

_cache_espcn = {}


def _carregar_espcn(escala, modelos_dir=None):
    """Carrega (e cacheia por escala) o modelo ESPCN via cv2.dnn_superres.
    Requer opencv-contrib-python (nao o opencv-python normal) - dnn_superres
    so existe no pacote contrib."""
    modelos_dir = modelos_dir or MODELOS_SR_DIR
    if escala in _cache_espcn:
        return _cache_espcn[escala]
    if not hasattr(cv2, "dnn_superres"):
        raise RuntimeError(
            "cv2.dnn_superres nao disponivel - instale opencv-contrib-python "
            "(nao basta opencv-python): pip install opencv-contrib-python")
    caminho = os.path.join(modelos_dir, f"ESPCN_x{escala}.pb")
    if not os.path.exists(caminho):
        raise FileNotFoundError(
            f"Modelo ESPCN nao encontrado: {caminho}. Escalas disponiveis no repo: "
            f"{ESCALAS_ESPCN_DISPONIVEIS} (pasta modelos_sr/).")
    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(caminho)
    sr.setModel("espcn", escala)
    _cache_espcn[escala] = sr
    return sr


def _upscale_espcn_tiled(frame_bgr, escala, modelos_dir=None, overlap=16):
    """
    Roda o ESPCN em blocos com pequena sobreposicao quando a foto de entrada
    e' grande o bastante pra' a saida estourar o limiar de memoria seguro
    (ver _LIMIAR_SAIDA_PX e o aviso no topo do arquivo - achado rodando
    contra uma foto NATIVA real do P070, nao a entrada ja' reduzida do teste
    inicial de comparacao). Sem tiling, uma foto grande o bastante (ex.:
    5760x2880 nativa, escala 2x) pode OOMar mesmo numa maquina com alguns GB
    de RAM livre.
    """
    H, W = frame_bgr.shape[:2]
    sr = _carregar_espcn(escala, modelos_dir)
    if H * W * escala * escala <= _LIMIAR_SAIDA_PX:
        return sr.upsample(frame_bgr)

    n = max(2, math.ceil(math.sqrt((H * W * escala * escala) / _LIMIAR_SAIDA_PX)))
    th, tw = math.ceil(H / n), math.ceil(W / n)
    saida = np.zeros((H * escala, W * escala, 3), dtype=np.uint8)
    for iy in range(n):
        for ix in range(n):
            y0_alvo, y1_alvo = iy * th, min(H, (iy + 1) * th)
            x0_alvo, x1_alvo = ix * tw, min(W, (ix + 1) * tw)
            if y0_alvo >= y1_alvo or x0_alvo >= x1_alvo:
                continue
            y0, y1 = max(0, y0_alvo - overlap), min(H, y1_alvo + overlap)
            x0, x1 = max(0, x0_alvo - overlap), min(W, x1_alvo + overlap)
            tile_saida = sr.upsample(frame_bgr[y0:y1, x0:x1])
            oy0, ox0 = (y0_alvo - y0) * escala, (x0_alvo - x0) * escala
            oy1 = oy0 + (y1_alvo - y0_alvo) * escala
            ox1 = ox0 + (x1_alvo - x0_alvo) * escala
            saida[y0_alvo * escala:y1_alvo * escala,
                  x0_alvo * escala:x1_alvo * escala] = tile_saida[oy0:oy1, ox0:ox1]
    return saida


def upscale_imagem(frame_bgr, metodo, escala, modelos_dir=None):
    """Aplica upscale numa unica imagem (array BGR do cv2). metodo='bicubic'
    (sem rede, so' interpolacao - barato em qualquer resolucao, sem risco de
    OOM) ou 'espcn' (rede leve pre-treinada, com tiling automatico pra fotos
    grandes - ver _upscale_espcn_tiled). Ver aviso no topo do arquivo -
    nenhum dos dois recupera detalhe real, e' upscale cosmetico."""
    if metodo == "bicubic":
        H, W = frame_bgr.shape[:2]
        return cv2.resize(frame_bgr, (W * escala, H * escala), interpolation=cv2.INTER_CUBIC)
    elif metodo == "espcn":
        return _upscale_espcn_tiled(frame_bgr, escala, modelos_dir)
    else:
        raise ValueError(f"Metodo de upscale invalido: {metodo!r} (use {METODOS_VALIDOS}).")


# ─── Uso standalone: aplica numa pasta de quadros JA GERADA ──────────────────

def upscale_pasta_quadros(pasta_quadros, metodo, escala=2, modelos_dir=None,
                           manter_original=False, qualidade_jpeg=92):
    """Aplica upscale em TODOS os quadros de uma pasta ja' gerada por
    gerar_quadros.py (le manifest.json pra saber os arquivos - ignora a pasta
    mini/ de miniaturas, que nao faz sentido upscalar). Sobrescreve cada
    arquivo no lugar (mesma resolucao mais alta, mesmo nome - nada mais no
    manifest.json precisa mudar, ele nao guarda largura/altura por quadro).

    manter_original=True guarda uma copia pre-upscale (sufixo _orig) - util
    pra comparar depois sem precisar regerar a vistoria inteira.
    """
    manifest_path = os.path.join(pasta_quadros, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    quadros = doc.get("quadros", [])
    print(f"[Upscale] {len(quadros)} quadros | metodo={metodo} escala={escala}x "
          f"| pasta={pasta_quadros}")

    t_inicio = time.time()
    tam_antes = 0
    tam_depois = 0
    n_ok = 0
    for i, q in enumerate(quadros):
        caminho = os.path.join(pasta_quadros, q["arquivo"])
        if not os.path.exists(caminho):
            print(f"[Upscale] [AVISO] {caminho} nao encontrado - pulando")
            continue
        tam_antes += os.path.getsize(caminho)
        frame = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if frame is None:
            print(f"[Upscale] [AVISO] Nao consegui ler {caminho} - pulando")
            continue
        saida = upscale_imagem(frame, metodo, escala, modelos_dir)
        if manter_original:
            base, ext = os.path.splitext(caminho)
            os.replace(caminho, f"{base}_orig{ext}")
        cv2.imwrite(caminho, saida, [cv2.IMWRITE_JPEG_QUALITY, qualidade_jpeg])
        tam_depois += os.path.getsize(caminho)
        n_ok += 1
        if n_ok % 50 == 0 or (i + 1) == len(quadros):
            decorrido = time.time() - t_inicio
            media = decorrido / n_ok
            restante = media * (len(quadros) - i - 1)
            print(f"[Upscale] {i+1}/{len(quadros)} | {decorrido:.0f}s decorridos | "
                  f"~{media:.2f}s/foto | ETA {restante:.0f}s")

    total = time.time() - t_inicio
    print(f"[Upscale] Concluido: {n_ok}/{len(quadros)} fotos em {total:.1f}s "
          f"({total/max(n_ok,1):.2f}s/foto media)")
    if tam_antes > 0:
        print(f"[Upscale] Tamanho em disco: {tam_antes/1e6:.1f}MB -> {tam_depois/1e6:.1f}MB "
              f"({tam_depois/tam_antes:.2f}x)")
    return dict(metodo=metodo, escala=escala, n_fotos=n_ok, tempo_total_s=round(total, 1),
                tam_antes_mb=round(tam_antes/1e6, 1), tam_depois_mb=round(tam_depois/1e6, 1))


def main():
    ap = argparse.ArgumentParser(
        description="Upscale (bicubic ou ESPCN) de todos os quadros de uma vistoria ja' "
                     "gerada por gerar_quadros.py - uso exploratorio (ver aviso no topo "
                     "do arquivo sobre limitacoes reais medidas com dados do P070).")
    ap.add_argument("--quadros", required=True, help="Pasta com os quadros + manifest.json.")
    ap.add_argument("--metodo", choices=METODOS_VALIDOS, required=True)
    ap.add_argument("--escala", type=int, default=2, choices=ESCALAS_ESPCN_DISPONIVEIS,
                    help="Fator de ampliacao (padrao 2x - 4x cria fotos MUITO grandes).")
    ap.add_argument("--modelos-dir", default=None, help="Pasta com ESPCN_x{2,3,4}.pb (padrao: modelos_sr/ do repo).")
    ap.add_argument("--manter-original", action="store_true",
                     help="Guarda uma copia pre-upscale (sufixo _orig) de cada foto.")
    ap.add_argument("--qualidade-jpeg", type=int, default=92)
    args = ap.parse_args()

    t0 = time.time()
    resultado = upscale_pasta_quadros(
        args.quadros, args.metodo, args.escala, args.modelos_dir,
        args.manter_original, args.qualidade_jpeg)
    print(f"[TIMING] upscale_quadros.py TOTAL: {time.time() - t0:.1f}s")
    print(f"[Resumo] {json.dumps(resultado, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
