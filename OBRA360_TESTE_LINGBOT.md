# Teste do LingBot-Map no Obra360 (GTX 1080)

Objetivo: ver se o **LingBot-Map** (reconstrução 3D densa por rede neural,
streaming) rastreia o corredor **sem derivar** onde o stella_vslam se perdeu —
e se a nuvem densa resolveria o problema de landmarks esparsos da medição.

É um **experimento**, não produção. Roda na máquina do Pedro (GTX 1080, 8GB).
A GTX 1080 é Pascal (sem bf16/Tensor cores) — por isso usamos `--use_sdpa`
(atenção nativa do PyTorch) em vez do FlashInfer.

Repo: https://github.com/Robbyant/lingbot-map — Apache 2.0.

---

## Passo 0 — Preparar os frames pinhole (a partir do vídeo 360)

O LingBot-Map espera **câmera normal (pinhole)**, não 360 equiretangular. O
script `preparar_lingbot.py` (no repo do Obra360) converte um trecho do vídeo
360 numa pasta de frames pinhole frontais.

Comece com um **trecho curto** que INCLUA o corredor problemático (ex.: 30s):

```powershell
python preparar_lingbot.py --video corredor.mp4 --out frames_lingbot ^
    --inicio 0 --duracao 30 --passo 2 --fov 90 --tamanho 640
```

- `--inicio`/`--duracao`: recorte no tempo (comece pequeno; a 1080 tem 8GB).
- `--passo 2`: pega 1 a cada 2 frames (menos memória/tempo).
- Se o vídeo for ProRes e o OpenCV não abrir, reencode antes:
  `ffmpeg -i original.mov -c:v libx264 -crf 18 corredor.mp4`

Resultado: pasta `frames_lingbot/` com `frame_00000.jpg`, `frame_00001.jpg`...

## Passo 1 — Ambiente Python + PyTorch (CUDA)

```powershell
conda create -n lingbot-map python=3.10 -y
conda activate lingbot-map
pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
```

(Se a 1080 reclamar de compute capability, ignore o aviso — sm_61 ainda roda.)

## Passo 2 — Clonar e instalar o LingBot-Map

```powershell
git clone https://github.com/Robbyant/lingbot-map.git
cd lingbot-map
pip install -e .
```

**NÃO instale o FlashInfer** (não gosta de Pascal) — vamos usar `--use_sdpa`.

## Passo 3 — Baixar os pesos do modelo

Do Hugging Face (`robbyant/lingbot-map`). Precisa da CLI:

```powershell
pip install -U "huggingface_hub[cli]"
huggingface-cli download robbyant/lingbot-map lingbot-map-long.pt --local-dir ./pesos
```

(Se `huggingface-cli` falhar, baixe `lingbot-map-long.pt` manualmente da página
https://huggingface.co/robbyant/lingbot-map e ponha em `./pesos/`.)

## Passo 4 — Rodar

Para um trecho curto (< ~300 frames), modo streaming padrão:

```powershell
python demo.py --image_folder ..\frames_lingbot ^
    --model_path .\pesos\lingbot-map-long.pt --use_sdpa
```

Para trecho longo (> 3000 frames — o corredor inteiro), modo windowed:

```powershell
python demo.py --image_folder ..\frames_lingbot ^
    --model_path .\pesos\lingbot-map-long.pt --use_sdpa ^
    --mode windowed --window_size 128 --overlap_keyframes 16 --keyframe_interval 2
```

Abre um viewer 3D no navegador (`http://localhost:8080`) com a nuvem de pontos
e a trajetória. Também salva um `output_pointcloud_side_by_side.mp4`.

## O que observar (é isso que decide se vale a pena)

1. **A trajetória do corredor deriva?** Compare com o que o stella_vslam fez
   no mesmo trecho — se o LingBot atravessa o corredor com trajetória estável
   onde o stella se perdeu, é o sinal que queremos.
2. **A nuvem é densa?** Diferente dos ~700 keyframes esparsos do stella, aqui
   cada frame vira geometria — é o que resolveria a medição.
3. **Coube na 1080?** Se der OOM (memória), reduza: `--tamanho 512`,
   `--passo 3`, trecho mais curto, ou `--keyframe_interval 3`.

## Se der problema

- **OOM de VRAM**: reduza `--tamanho`, aumente `--passo`, trecho menor.
- **Erro de bf16 / dtype**: force fp32 (procure flag `--dtype fp32` no
  `demo.py --help`) — a 1080 não tem bf16 nativo.
- **`huggingface.co` bloqueado/lento**: use o mirror ModelScope
  (modelscope.cn/models/Robbyant/lingbot-map).
- **OpenCV não abre o vídeo**: reencode com ffmpeg (ver Passo 0).

## Pendências pra decidir DEPOIS do teste (não agora)

- **Escala métrica**: mesmo se a trajetória for ótima, a escala provavelmente
  ainda precisa de calibração (porta/planta) — o modelo dá escala consistente,
  não necessariamente em metros. Ver LingBot-Depth (modelo irmão, profundidade
  métrica) se precisar.
- **Produção**: a VPS não tem GPU. Se o teste for positivo, produção exigiria
  máquina com GPU — decisão de infra/custo pra depois.
- **360 completo**: este teste usa só o recorte frontal (perde a cobertura
  360°). Se funcionar, avaliar processar múltiplas direções ou o cubemap
  inteiro.
