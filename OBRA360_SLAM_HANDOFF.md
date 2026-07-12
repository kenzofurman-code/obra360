# Obra360 — Pipeline SLAM de Vistoria 360 (handoff claude.ai → Claude Code)

> Contexto para o Claude Code: este documento resume um projeto desenvolvido e
> validado em conversas no claude.ai. Adicione ao CLAUDE.md do repo ou mantenha
> como OBRA360_SLAM.md referenciado por ele.

## O que é

Feature do Obra360 (plataforma IncorProjetos, React/TS/Supabase/Vercel): mapear
vistorias gravadas em vídeo 360 (Insta360, equiretangular) sobre a planta baixa
(PDF vetorial), estilo Matterport/OpenSpace. O inspetor caminha pelo pavimento
gravando; o sistema reconstrói a trajetória, alinha à planta e gera panoramas
estáticos navegáveis clicando na planta.

## Estado: pipeline completo e validado no 6º pavimento (P073)

**Precisão final medida: 1.3% vs gabarito de 95 pontos; residual mediano de
0.0009 (unid. planta) em 22/34 portas atravessadas** (~centímetros na escala do
prédio). Evolução: script leve v2 36% → v4+EKF → stella_vslam 5.8% → correção
de aspecto 1.3%.

## Scripts (todos testados; Python + OpenCV/NumPy; SLAM via Docker)

| Script | Papel |
|---|---|
| `process_trajectory.py` (v4) | Odometria leve 360 pura-Python: fluxo em faixa equiretangular, ajuste u(θ)=c0+p·sinθ+q·cosθ (separa rotação de translação), quality-gate por resíduos, EKF de pedestre com R adaptativo. Fallback sem Docker; menos preciso que SLAM. |
| `rodar_slam.py` / `rodar_slam.bat` | Orquestra o stella_vslam em Docker (headless, `--viewer none`, `--eval-log-dir`): detecta resolução, reduz vídeo p/ 1920, gera config equiretangular, baixa vocab ORB, roda container (`--entrypoint bash` é OBRIGATÓRIO — a imagem tem ENTRYPOINT /bin/bash), converte saída. Imagem: `stella_vslam-socket` (build do Dockerfile.socket do repo stella-cv/stella_vslam). |
| `slam_to_obra360.py` | Converte frame_trajectory.txt (TUM) → JSON `[{t,x,y}]`. Modo `--referencia` (recomendado): alinhamento Umeyama por N pontos clicados `[{t,x,y}]` + teste de espelhamento + calibração temporal em 2 etapas (direta primeiro; warp monotônico só se reduzir erro). `--portas`: relatório de qualidade + correção fina gateada por holdout. **Correção de aspecto**: alinha em coords físicas da página (auto-detecta do arquivo de portas ou `--aspecto`). |
| `extrair_portas.py` | PDF vetorial → portas: rótulos `P[MJUCAF]\d+`, arcos como polilinhas (encadeamento por path + curvatura consistente + fit de círculo), associação gulosa rótulo↔arco com teto. Salva dimensões da página (aspecto). `--limite-x` recorta carimbo. |
| `gerar_quadros.py` | Vídeo + trajetória → panoramas: amostragem por DISTÂNCIA percorrida (padrão 0.025 unid ≈1 m), quadro extra em pausas ≥4 s, seleção anti-blur (var. Laplaciano em janela ±0.5 s), miniaturas, `manifest.json` (enviado POR ÚLTIMO = sinal de "pronto"), upload R2 via boto3 (credenciais só por env: R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY). Estrutura: `<nome_video>/quadro_NNNN.jpg`, `/mini/`, `/manifest.json`. |
| `converter_slam.bat` | Conversão por duplo clique (estilo dos .bat do Pedro). |

## Lições técnicas críticas (não reaprender do jeito difícil)

1. **Coordenadas do Obra360 são normalizadas por eixo** (x÷largura, y÷altura da
   página; PDF do 6º pav: 3826.68×2383.92, aspecto 1.605). Alinhamento com
   escala uniforme nesse espaço distorce (achata y, alarga x, erro cresce
   radialmente). SEMPRE alinhar em coordenadas físicas e normalizar só na saída.
2. **Insta360 exporta com estabilização FlowState** (yaw travado no mundo) — a
   imagem não gira nas curvas; a direção vem toda do termo senoidal do fluxo.
3. **Timestamps do run_video_slam são sintéticos** (frame_skip/fps do config por
   pose). Duração 2× diferente do vídeo = fps errado no config OU vídeo errado.
4. **Antes de perseguir hipóteses sofisticadas, confirmar que os dados comparados
   são do mesmo evento** (perdemos horas comparando com gabarito de outro take).
5. Cloudflare **Stream limita saída a 1080p** (360 vira ~480p efetivos) → por
   isso quadros no R2, não vídeo no Stream.
6. Portas de **correr (PJ)** não têm arco no PDF → fora do extrator atual.

## Decisões de arquitetura acordadas

- **Processamento remoto** (não app local): upload navegador→R2 multipart; fila
  em tabela Supabase (Realtime p/ progresso); worker em VPS (Docker + estes
  scripts). stella_vslam é CPU-only; 8 min ≈ 10–20 min em 4–8 vCPU; VPS ~€30/mês
  cobre ~1.500 vistorias/mês.
- **Dois planos**: básico = só quadros (vídeo em `videos_temp/` com lifecycle R2
  auto-expirando ~21 dias após validação); premium = arquiva original
  (`videos/`, lifecycle → Infrequent Access após 90 d). Exclusão do vídeo SÓ
  após validação (manifest ok + relatório de portas ≥ limiar). NUNCA excluir:
  trajetória calibrada, manifest, relatório de portas, mapa.msg do SLAM.
- **Aceite automático por portas**: nº atravessadas + residual = selo de
  qualidade por vistoria, custo zero ao usuário.

## Protocolo de captura (para o manual do inspetor)

Loop closures deliberados (voltar por corredores já mapeados; terminar perto do
início; repassar cantos extremos) > curvas lentas (~2 s para 90°) > pausas
paradas são ok (girar rápido no lugar não) > velocidade constante ~0.8 m/s >
travar exposição/evitar contraluz de sacadas > bastão alto e estável.

## Backlog priorizado

1. **worker.py**: consumir fila Supabase → rodar pipeline → R2 → status Realtime.
2. Front: viewer de panoramas (Three.js troca de textura + preload de vizinhos
   + crossfade) lendo manifest; pontos na planta; destacar `tipo:"pausa"`.
3. **Ferramenta de medição nos panoramas** (2 cliques, 1 imagem): raio do clique
   (raycaster Three.js) × nuvem de landmarks do `mapa.msg` (parsear msgpack:
   pontos 3D + observações por keyframe) → ajuste de plano local (RANSAC nos k
   vizinhos) → interseção → ponto 3D; distância entre 2 cliques. Fallback:
   matching por curva epipolar em quadro vizinho. Validar medindo as portas PM
   (80 cm de projeto) = teste automático de precisão. Níveis mais simples:
   piso via altura do bastão (d = h/tanθ) e planos verticais. Futuro: depth
   monocular denso ancorado nos landmarks.
4. Detector de **portas de correr** (retângulos paralelos junto ao rótulo) e/ou
   flag `--dicas "PJ3@30,PJ5@180"` no conversor (restrições temporais manuais).
5. **Zero-clique**: registrar constelação de cruzamentos × constelação de portas
   do PDF (residuais atuais de 0.0009 tornam viável); desambiguar com 1 dica.
6. Mapa persistente: `--map-db-in --disable-mapping` para localizar vistorias
   novas no pavimento já calibrado (herda a calibração).
7. Comparação semana-a-semana: quadros indexados por posição → diff no mesmo
   ponto físico entre vistorias.

**REGRA OPERACIONAL: rodar SEMPRE com `--manter-mapa`** — o mapa.msg alimenta os
itens 3 e 6 e entra na lista de "nunca excluir".

## Dados de validação disponíveis

6º pav P073: `frame_trajectory.txt` (4555 poses/456 s), gabarito 95 pontos com t,
PDF `P073-ARQ-EX-010-R04-6PAV.pdf`, `portas_6pav.json` (34 portas),
`caminho_slam_6pav_FINAL.json` (938 waypoints, 1.3%).
