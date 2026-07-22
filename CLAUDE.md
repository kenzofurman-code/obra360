# Obra360 — Contexto para o Claude Code

> Handoff de uma sessão de debugging fim-a-fim (2026-07-15) rodando o pipeline
> real (worker.py) contra um vídeo ProRes de 46.8GB + a planta
> `P070-ARQ-EX-007-R01-P4P.pdf`. Leia também `OBRA360_ROADMAP.md` (arquitetura e
> roadmap comercial) e `OBRA360_SLAM_HANDOFF.md` (pipeline SLAM/stella_vslam) —
> este arquivo cobre o que mudou DEPOIS desses dois, mais notas operacionais.

## Como o Pedro trabalha (importante pro Claude Code saber)

- Pedro reexecuta o `worker.py` real contra vídeo/PDF reais a cada fix — não
  aceita "deveria funcionar", quer o log de execução. Valide sempre rodando,
  não só lendo o código.
- Cada CAD/projeto pode ter convenções diferentes de rótulo (P55 vs PM1,
  "A=8,28m2" vs "11,11m²`). Regex de extração deve ser estendida como
  superset estrito das anteriores — nunca reescrita do zero — pra não
  regredir projetos já validados.
- Sem acesso direto a git/terminal nesta sessão: os commits foram entregues
  como arquivos `commitN.txt` (git add + commit + push) pro Pedro rodar
  manualmente no PowerShell dele. **Isso deixou um backlog**: no último
  `git status` que ele colou, vários arquivos com fix já validado localmente
  ainda apareciam como modificados/não commitados: `extrair_portas.py`,
  `gerar_quadros.py`, `process_trajectory.py`, `rodar_slam.py`,
  `rodar_worker.bat`, `video_io.py`. **Primeira coisa a checar**: rodar
  `git status` e `git log --oneline -15` pra ver o que realmente chegou no
  remoto antes de assumir que os fixes abaixo estão no repo.
- Deploy: frontend (Vite/React) é Vercel, precisa de rebuild após `git push`
  (não é instantâneo como os scripts Python, que rodam direto do disco).
  Confirme deploy no dashboard Vercel antes de dizer "já está no ar".
- Commits não cascateiam: cada `commitN.txt` só dá `git add` nos arquivos
  daquele fix específico.
- **PowerShell quebra ao colar mensagem de commit multi-parágrafo direto
  com `-m "..."` de múltiplas linhas** (confirmado 2026-07-15, `commit9`):
  linha em branco no meio do texto colado faz o console perder o fechamento
  da aspas e passar a executar pedaços do texto como comandos soltos
  (piora se o texto tiver `<`/`>`, que são operadores reservados do
  PowerShell). Fix adotado: a partir do `commit9`, cada `commitN.txt` vira
  só `git add ...` + `git commit -F commitN_msg.txt` + `git push`, com a
  mensagem de commit num arquivo `.txt` separado (sem passar pelo parser
  do shell). Pedro precisa salvar os DOIS arquivos (`commitN.txt` e
  `commitN_msg.txt`) na raiz do repo antes de rodar.
- **PowerShell corrompe caminhos com acento passados como argumento pro
  Python** (confirmado 2026-07-16, testando `testar_depth_anything.py`):
  `cv2.imread` recebeu `teste medi├º├úo e resolucao/...` em vez de `teste
  medição e resolucao/...` e falhou com "can't open/read file". Fix
  aplicado: pasta renomeada pra `teste_medicao_e_resolucao` (sem espaço,
  sem acento — mesmo padrão de `modelos_sr`/`cache_mapas`). Regra geral
  daqui pra frente: evitar acento/espaço em nomes de pasta/arquivo que vão
  ser passados como argumento de linha de comando pro Python no Windows;
  se precisar usar um caminho acentuado mesmo assim, `chcp 65001` antes do
  `python` no PowerShell costuma resolver.

## O que foi corrigido e validado nesta sessão

Todos os itens abaixo foram testados rodando o script real contra o PDF/vídeo
real (não só lidos/revisados) — resultado numérico ao lado.

1. **`extrair_portas.py` achava 0 portas** — duas causas: (a) regex de código
   de porta exigia letra após "P" (`P[MJUCAF]{1,2}\d+`), essa planta usa
   `P55`/`P90A` puro → ampliado pra `{0,2}`. (b) o detector de arco só lia
   segmentos de reta (`'l'`), essa planta desenha o arco da porta como
   Bézier cúbica (`'c'`) → adicionado `_bezier_pts()` (amostra 8 pontos da
   curva) e unificado no mesmo pipeline de fit-de-círculo. **0 → 31 portas**.
2. **Tabela "RELAÇÃO DE ESQUADRIAS" quebrava em P80/P90A** — parser assumia
   QTDE numa posição fixa após a altura, mas essas linhas têm uma coluna
   PEITORIL no meio (às vezes "-", não numérico) que descasava tudo.
   Trocado para busca de janela pelo token de TIPO (mais robusto que posição
   fixa); QTDE virou best-effort. **31 → 33 vãos** (ganha as 2 portas de
   correr PJ02/PJ08).
3. **0 panoramas gerados, sem erro visível** — `video_io.py` jogava fora o
   stderr do ffmpeg (`DEVNULL`) e usava `bufsize` customizado gigante
   (~190MB) — suspeito de falha silenciosa de pipe no Windows. Corrigido:
   stderr vai pra um tempfile e é impresso na primeira falha de leitura;
   `bufsize` voltou ao padrão do Python.
4. **Causa raiz real do item 3: ffmpeg rejeitava a flag `-vsync`** (removida
   em builds novos do ffmpeg, que exigem `-fps_mode passthrough` — mas
   builds antigas rejeitam essa flag nova). Corrigido com uma cadeia de
   fallback em runtime (`-fps_mode` → `-vsync 0` → nenhuma flag), detectando
   rejeição de CLI via `Popen.poll()` logo após abrir o processo. **0 → 257
   → 1062 panoramas** confirmados em logs reais sucessivos.
5. **Firestore 1MB por documento estourado** — trajetória SLAM completa
   (16515+ pontos, >1MB serializado) ia inteira pro campo `waypoints` do
   Firestore. Corrigido subindo a trajetória pro R2 (mesmo bucket dos
   panoramas) e salvando só `waypoints_url`; fallback inline se <700KB;
   erro claro (não crash silencioso) se R2 não configurado e o arquivo for
   grande demais. Replicado em `worker.py` e `processar_vistoria.py`;
   `Visita.jsx` atualizado pra buscar do R2 quando `waypoints_url` existir,
   e o botão "Salvar" ganhou o mesmo guard de tamanho. **Confirmado**: log
   real mostrou o documento salvo com `waypoints_url`/`manifest_url`, sem
   crash.
6. **`extrair_ambientes.py` achava 0 ambientes** — só reconhecia área como
   `11,11m²`; esta planta escreve `A=8,28m2` (prefixo colado, "2" em vez de
   "²"). Adicionado `PAT_AREA_PREFIXO`. **0 → 54 ambientes**. Um nome saiu
   errado (token de cotagem corrompido no próprio PDF — não é bug de
   código).
7. **Novas vistorias não nasciam pré-calibradas** — depois de descobrir, em
   duas importações reais, que a mesma calibração de SLAM funciona bem
   (bússola 90°, escala do caminho 2.9%, espelhar E/D ligado, escala da
   passarela 8%, rotação da passarela 90°, ajuste do cone 90°), os defaults
   de `criarVisita()` em `src/lib/visitas.js` foram trocados de
   `heading_offset=0/path_scale=0.15/espelhar_caminho=false` (sem
   `passarela_escala`/`passarela_rotacao`/`cone_frame_offset`, que só
   existiam via botão Salvar) para os valores acima já no create. Motivo:
   sem cruzamento de porta detectado no primeiro passe, o SLAM não converge
   — pré-calibrar evita rodar o worker várias vezes por vistoria.
8. **Regressão de CSS no "Painel de Controle" (drawer de configurações)** —
   um fix anterior (rolagem geral, "commit6") tinha colocado
   `overflow-y-auto` no drawer inteiro + header sticky, o que colapsava a
   altura do `WaypointEditor` (`flex-1 min-h-0`) a zero — sumindo o seletor
   "2. Sobreposição de Plantas" e o botão "Salvar Alterações".
   - Tentativa 1 (`commit7`): isolar rolagem só no bloco de sliders
     (`min-h-0 overflow-y-auto`, sem `flex-1`), esperando que o navegador
     encolhesse esse bloco sozinho sobrando espaço pro `WaypointEditor`.
     **Pedro confirmou que não funcionou** — a rolagem do bloco de config
     batia no fim sem nunca expor o editor abaixo.
   - Tentativa 2 (`commit8`, atual): trocado por limite de altura explícito
     `shrink-0 max-h-[48vh]` no bloco de config, garantindo espaço fixo
     restante pro `WaypointEditor`. **Ainda não confirmado pelo Pedro** —
     primeira coisa a verificar/perguntar.
9. **Sem visibilidade de qual etapa consumia o tempo num run de >1h** —
   `worker.py`/`rodar_slam.py`/`gerar_quadros.py` não imprimiam timestamp
   nenhum, só texto do que estava rodando. Adicionado `[TIMING] <etapa>: Xs`
   em cada fronteira relevante nos 3 arquivos (download, corte inicial,
   SLAM/odometria, conversão TUM, PDF, map matching, panoramas, Firestore,
   e dentro do próprio `rodar_slam.py`: vocabulário, redução de vídeo, o
   Docker/stella_vslam em si, cópia final; dentro do `gerar_quadros.py`: abrir
   vídeo, a passada de decode full-res com frames/s+ETA a cada 2000 frames,
   upload R2). Suspeita levantada (não confirmada ainda): pra um vídeo ProRes
   de 46.8GB em 5760x2880, o pipeline faz até 3 passadas completas sobre o
   vídeo inteiro (corte inicial + redução pro SLAM + decode full-res no
   `gerar_quadros.py`) — o decode full-res é o principal suspeito de ser o
   gargalo, mais que o SLAM em si (que roda sobre o vídeo já reduzido/H264).
   **Confirmar no próximo run real com os novos logs.**
10. **Calibração automática por portas testava espelhar (True/False) e
    deixava heading flutuar livremente (Umeyama geral)** — depois de 3
    vistorias reais, ficou claro que heading_offset (90°) e espelhar_caminho
    (ligado) são propriedade da câmera/convenção de gravação e não deveriam
    variar vídeo a vídeo; só a escala é genuinamente arbitrária no SLAM
    monocular (varia ~2-3% nos testes reais). `calibrar_por_portas()` em
    `processar_vistoria.py` foi reescrita: heading/espelhar agora são
    FIXOS (passthrough, nunca recalculados); só escala+âncora (translação)
    são ajustados por mínimos quadrados contra os cruzamentos de porta
    (substituindo o Umeyama geral de 4 parâmetros livres por um fit
    restrito de 2). Validado numericamente (matemática pura + mock da
    detecção geométrica de cruzamento) — recupera escala/âncora corretos a
    partir de um chute ~20% errado, e heading/espelhar saem sempre
    idênticos ao que entrou, mesmo se passados errados de propósito.
    **Ainda não validado rodando o worker.py real contra um vídeo** — só
    matemática/mock, não Docker+SLAM real.
11. **`Ambientes: 0/16670 pontos associados` mesmo com 54 ambientes
    detectados** — reimportação do P070 mostrou no log real que
    `extrair_ambientes.py` achava os 54 ambientes certinho, mas nenhum
    waypoint caía dentro do raio de nenhum. Causa: `_parsear_esquadrias()`
    em `extrair_portas.py` aceita largura/altura tanto em metros com
    vírgula (`"0,80"`) quanto em centímetros sem vírgula (`"80"`, caso das
    portas P80/P90A desta planta) com o MESMO regex — mas sempre tratava o
    valor como metros. `largura_m` virava 80.0 (80 METROS) em vez de 0.80.
    Isso não quebrava a geometria da porta de correr (o vão sintético
    cancela esse fator), mas quebrava `escala_pts_por_m` — usado pelos
    ambientes contra `area_m2` real, sem esse cancelamento — deixando
    `raio_fis` ~100x menor que o real. Fix: se largura_m/altura_m > 10
    (nenhuma porta/janela real passa de 10m), assume centímetro e divide
    por 100 — superset estrito, plantas já em metros-com-vírgula não
    mudam. **Validado rodando contra o PDF real**: `escala_pts_por_m`
    0.566 → 56.6 (100x, como esperado); ambientes seguem 54/54; portas
    seguem 33/33 (sem regressão). Commit `commit12`.

12. **Marcadores de foto na planta e na fita 3D** — cada quadro do
    manifest.json já tem x/y/t próprios (gerar_quadros.py); pedido do
    Pedro em 2026-07-15 pra ver onde cada foto fica (marcador azul-claro)
    tanto na planta quanto sobreposta na fita 3D, pra clicar de forma mais
    assertiva. `PanoramaViewer.jsx` ganhou prop `onQuadros` (expõe a lista
    carregada do manifest pro pai); `PlantaViewer.jsx` ganhou prop `frames`
    (desenha um marcador por foto SEMPRE visível + testa clique perto de
    uma foto ANTES de cair na interpolação por segmento, pulando direto pro
    tempo exato dela); `Visita.jsx` ganhou o estado `quadros` +
    `framesAlinhados` (mesmo `alinharPonto` de `waypointsAlinhados`).
    **Iteração 2 (mesmo dia, feedback do Pedro na 1ª versão)**: a fita 3D
    (`PanoramaViewer.jsx`) NÃO mostra mais todas as fotos o tempo todo (a
    v1 poluía a imagem 360° com 1000+ pontos) — agora só destaca a foto mais
    próxima de onde o MOUSE está passando sobre a fita (raycast contra o
    mesh da fita → acha o ponto 3D → acha o quadro mais próximo), e ficou
    clicável (clique sem arrastar, mesmo limiar de 6px do drag-to-look, pula
    pro tempo daquela foto). A planta baixa continua mostrando todos os
    marcadores sempre (é vista geral, não a foto imersiva, sem o mesmo
    problema de poluição visual). Escopo: só vistorias com `manifest_url`
    (Player360/vídeo puro não tem "foto" discreta pra marcar). **Validado só
    via esbuild (sintaxe)** — ainda não testado no navegador. Commit
    `commit13`.

13. **Super-resolução multi-frame sob demanda (`super_resolucao.py`, novo
    arquivo)** — ideia do próprio Pedro (2026-07-12, roadmap Fase 4 item 7):
    clicar num ponto de uma foto do tour, reprojetar aquele recorte em
    outras fotos da MESMA vistoria onde o ponto é visível, alinhar sub-pixel
    e fundir numa imagem de resolução mais alta do que qualquer foto
    individual sozinha. Ganho esperado ~2-4x, NÃO "zoom infinito" — depende
    de diversidade real de ângulo/posição entre as fotos que veem aquele
    ponto.
    - **REDESENHO no mesmo dia (2026-07-15)**: a 1ª versão reprojetava no
      VÍDEO BRUTO original (via `frame_trajectory.txt` + ffmpeg seek).
      Pedro perguntou "porque eu preciso do vídeo se eu já tenho os frames
      e é nos frames que temos a posição exata?" — resposta honesta: os
      quadros do tour são ESPARSOS (1 por parada, amostragem por distância
      percorrida), raramente têm mais de 1-2 observações do mesmo ponto,
      mas evitar depender do vídeo bruto elimina de vez o problema de
      guardar 46.8GB por vistoria pra sempre. Pedro topou a troca (ganho
      menor e dependente da geometria de cada vistoria, em troca de nunca
      mais precisar do vídeo bruto pós-processamento) e pediu pra seguir.
    - Novo desenho: `gerar_quadros.py` ganhou `--traj-completa` (opcional,
      aponta pro `frame_trajectory.txt` do SLAM) — casa o frame EXATO
      escolhido (mais nítido da janela) com a pose mais próxima da
      trajetória densa e anexa `pose_raw` (posição+quaternion brutos do
      SLAM) a cada quadro do `manifest.json`. `worker.py` passa
      `frame_trajectory.txt` pra isso sempre que o SLAM rodou com sucesso.
      Retrocompatível: vistorias antigas/sem SLAM simplesmente não têm
      `pose_raw` nos quadros — super-resolução fica indisponível só pra
      elas, resto do pipeline intacto. Import de scipy é LAZY em
      `gerar_quadros.py` (só acontece se `--traj-completa` for passado) —
      não virou dependência do pipeline principal.
    - `super_resolucao.py` agora reprojeta nos QUADROS (fotos já
      extraídas/JPEG), não no vídeo — `extrair_recorte` lê direto do
      arquivo da foto (`cv2.imread`), sem ffmpeg/seek. `mapa.msg` ainda é
      usado, mas só pelos landmarks (achar o ponto 3D clicado via RANSAC de
      plano local) — a pose de origem do raio agora vem do próprio quadro
      clicado, não mais de um keyframe do mapa.
    - `video_io.py::extrair_frame_no_tempo()` (da 1ª versão) ficou sem uso
      por esta feature agora — não removido (pode servir de novo se um dia
      precisarmos voltar a tocar no vídeo bruto), mas não faz mais parte do
      fluxo de super-resolução.
    - Reusa a mesma geometria de `medir_panorama.py` (clique → ponto 3D via
      landmarks + plano local por RANSAC) sem duplicar código.
    - Um bug de seleção de candidatos (da 1ª versão, baseada em vídeo) foi
      encontrado e corrigido por teste sintético próprio antes do redesenho
      — não chegou a ser exposto ao Pedro; a lógica de âncora-primeiro foi
      preservada no redesenho baseado em quadros.
    - **Validado SÓ com dados sintéticos nesta sessão** (sem Docker/
      stella_vslam/vídeo real no sandbox): parsing/casamento de poses do
      `frame_trajectory.txt` sintético, pipeline completo (`super_resolver`)
      rodado ponta a ponta contra um `mapa.msg` + manifest sintéticos (6
      quadros vendo o mesmo ponto de ângulos diferentes — todos
      selecionados e fundidos com sucesso), e os 2 casos de borda mais
      importantes: só 1 quadro disponível (avisa, não trava) e quadro sem
      `pose_raw` (erro claro, não crash). **Ainda NÃO rodado contra
      `mapa.msg`/`manifest.json` reais de uma vistoria** — primeira coisa a
      fazer no próximo run real do worker.py (que agora precisa passar
      `--traj-completa` — já vem automático do worker.py atualizado).
      Commit `commit15`.

14. **BUG GRAVE encontrado e corrigido 2026-07-16: `worker.py` nunca gravava
    os dados finais no Firestore.** Pedro reparou que os pontos/marcadores
    não apareciam no site depois de 2 runs completos (79min cada) da
    vistoria `Nf1KoXXPByR9G01WvnjO`. Causa: `processar_visita()` montava o
    dict `dados` inteiro (status, ancora1, heading_offset, path_scale,
    espelhar_caminho, ambientes, selo_qualidade, waypoints_url,
    manifest_url) mas **nunca chamava `firebase_client.atualizar_campos()`**
    pra gravar isso — faltava só essa 1 linha. `processar_vistoria.py`
    (script manual mais antigo que o `worker.py` substituiu) sempre chamou
    essa função corretamente; a chamada não foi portada quando `worker.py`
    foi escrito. Efeito: TODA vistoria processada via `worker.py` ficava
    travada em `status='processando'` (único valor gravado, no início da
    função) — mesmo com o pipeline inteiro rodando com sucesso e
    imprimindo "processada com sucesso" nos logs. Bug 100% silencioso, sem
    erro nenhum. **Fix**: adicionada a chamada faltante antes do log de
    timing da etapa 6.
    - Novo arquivo `reparar_firestore.py`: conserta vistorias já
      processadas antes deste fix SEM reprocessar o pipeline inteiro (SLAM
      + panoramas já rodaram e já estão no R2 — refazer tudo custaria de
      novo os ~70-80min). Reaproveita `tum_para_raw_waypoints`/
      `subir_json_r2` (worker.py) e `run_pdf_extractor`/
      `run_ambientes_extractor`/`run_map_matching`/`estabilizar_paradas`
      (processar_vistoria.py) pra refazer só as etapas baratas que ficaram
      faltando (segundos, não minutos), usando o `frame_trajectory.txt` já
      salvo na pasta temp. `manifest_url` é reconstruído a partir do
      padrão de URL do R2 (panoramas já foram upados, não sobe de novo).
    - **Confirmado por Pedro** rodando `reparar_firestore.py --id
      Nf1KoXXPByR9G01WvnjO --traj-completa <caminho>` — funcionou na 2ª
      tentativa (1ª deu erro de PowerShell por causa de aspas no
      `--traj-completa`, resolvido passando o caminho corretamente).

15. **Bug de timestamp de relógio de parede (`carregar_poses_tum`,
    `gerar_quadros.py`) — `pose_raw` vinha com valores sem sentido
    (`dist_pose_s` na casa dos bilhões)**. Causa: o stella_vslam, quando
    roda sem `--start-timestamp` (nosso caso — confirmado no log real do
    Docker: "--start-timestamp is not set. using system timestamp."),
    carimba cada frame do `frame_trajectory.txt` (e cada keyframe de
    `mapa.msg`) com o **timestamp real do relógio (epoch Unix, ~1.78
    bilhão em 2026)**, não com o tempo relativo ao vídeo. `tum_para_raw_waypoints`
    (worker.py) já normalizava isso (`ts - ts[0]`) há tempos; `carregar_poses_tum`
    (gerar_quadros.py, escrita nesta mesma sessão pro item 13) não fazia
    essa normalização — bug introduzido e corrigido no mesmo dia. Fix:
    `ts = ts - ts[0]` logo após ordenar por tempo. **Validado com dados
    reais do Pedro** (`frame_trajectory.txt` de 16686 linhas, vistoria
    `Nf1KoXXPByR9G01WvnjO`): após o fix, `dist_pose_s` médio nos 1077
    quadros = 0.008s, máximo = 0.048s (excelente). Mesma normalização
    também precisa ser aplicada em qualquer lugar que leia `ts` de
    `mapa.msg` diretamente (keyframes) — confirmado que os keyframes têm
    o MESMO problema (`ts` também em epoch), então qualquer código que
    precise de tempo relativo a partir do mapa (não só do
    `frame_trajectory.txt`) deve subtrair o primeiro `ts` da mesma forma.

16. **`medir_panorama.py`: descoberta de que o ajuste de plano local por
    RANSAC não é confiável na prática** — 1º teste com dados reais (mapa.msg
    de 927 keyframes/19763 landmarks, fotos reais `quadro_0559.jpg` e
    `quadro_0080.jpg`). A geometria clique→raio 3D está correta (ângulo
    entre 2 raios bateu quase exato com o esperado a partir de Δu, ex.:
    44.74° vs 45°), mas o RANSAC de plano local às vezes "acha" um plano
    ERRADO (o mais numeroso dentro do raio de busca escolhido, não
    necessariamente o correto) — o MESMO clique deu distâncias de 3.03,
    10.72 e 5.22 unidades SLAM variando só `t_max`/`dist_max`, sem erro
    nenhum (falha silenciosa). Em outro caso (porta de `quadro_0080.jpg`,
    t=40.9s, bem no início da gravação) o RANSAC não achou NENHUM plano em
    NENHUM parâmetro — porque essa região do mapa ainda estava esparsa
    (keyframe mais próximo só apareceu ~186s depois, quando o inspetor
    revisitou a área).
    - **Fix**: nova função `medir_ponto_robusto()` em `medir_panorama.py` —
      roda o RANSAC com 6 combinações diferentes de `(t_max, dist_max)` e só
      aceita o resultado se os pontos 3D encontrados CONVERGIREM entre si
      (dispersão máxima < `tolerancia_consistencia`, default 0.15 unid.
      SLAM); caso contrário retorna `sucesso=False` com o motivo, em vez de
      um número que parece válido mas pode estar errado. CLI ganhou
      `--robusto`/`--tolerancia-consistencia`; o modo antigo (single-shot)
      continua disponível sem essa flag, sem quebrar uso existente.
    - **Validado contra os MESMOS cliques problemáticos** (janelas de
      `quadro_0080.jpg`, usando a própria `pose_raw` do quadro — mesmo
      caminho que `super_resolucao.py` usa): antes dava 3.03/10.72/5.22 sem
      aviso nenhum; com `medir_ponto_robusto()`, os 2 cantos da janela agora
      voltam `sucesso=False` com `dispersao=3.36` e `dispersao=28.43`
      respectivamente — a inconsistência real está sendo detectada e
      reportada, não mais mascarada.
    - `super_resolucao.py::super_resolver()` **também tinha essa mesma
      vulnerabilidade** (usava o RANSAC single-shot direto pra achar o ponto
      3D do clique antes de reprojetar nos outros quadros) — trocado pra usar
      `medir_ponto_robusto()` também, com o mesmo `--tolerancia-consistencia`
      exposto na CLI. `--t-max`/`--dist-max-landmark` ficaram sem efeito
      nesse pipeline (mantidos só pra não quebrar chamadas existentes) —
      `medir_ponto_robusto()` testa as combinações internamente.
    - **Pendência real**: a causa raiz (regiões com poucos landmarks/mapa
      esparso não têm como ser medidas com confiança por este método) não
      tem solução ainda — `medir_por_epipolar_fallback()` (matching de
      features + restrição epipolar entre 2 keyframes vizinhos) continua
      NÃO implementado, é o próximo passo natural pra cobrir esses casos.
    - Commit `commit18` (junto com o fix de timestamp do item 15).

17. **Upscale cosmético opcional (bicubic/ESPCN) no pipeline, discussão com o
    Pedro em 2026-07-16** — depois de ele perguntar sobre usar EDSR/TensorFlow
    "pra aumentar a densidade de todos os frames", montamos um teste real
    comparando bicubic (sem rede), ESPCN, LapSRN e EDSR contra o PIXEL REAL de
    fotos do P070 (reduzindo 4x e reconstruindo, medindo PSNR/SSIM contra o
    gabarito verdadeiro — não contra "parece nítido"). Resultado: nenhuma rede
    recuperou detalhe real mensurável em cenas escuras/baixo contraste
    (típico de obra em construção — o pior caso pra esse tipo de modelo);
    bicubic às vezes ganhou. EDSR custa ~10min/foto extrapolado em CPU
    (inviável pra ~1000+ fotos/vistoria sem GPU); LapSRN estourou memória
    rodando uma foto inteira de uma vez. Conclusão passada ao Pedro: nenhum
    upscale "de todo frame" recupera detalhe de medição real — no máximo é
    cosmético pro zoom no viewer 360°.
    - Pedro pediu pra deixar isso implementado mesmo assim (`upscale_quadros.py`,
      novo arquivo) com uma opção no `worker.py`/`gerar_quadros.py`, pra ele
      testar num caminho de vídeo inteiro. Só bicubic/ESPCN ficaram
      disponíveis (EDSR/LapSRN de fora, por não compensarem custo).
    - `upscale_imagem(frame_bgr, metodo, escala, modelos_dir)` é a função
      central, chamada de dentro de `gerar_quadros.py::finalizar()` — ANTES de
      gravar o arquivo (upscale único por foto, sem reescrever/reenviar pro R2
      duas vezes). Miniatura (`mini/`) continua sendo gerada a partir do frame
      ORIGINAL, não upscalado (upscalar antes seria desperdício, ela é
      reduzida de qualquer forma).
    - `--upscale-metodo {none,bicubic,espcn}` / `--upscale-escala {2,3,4}` em
      `gerar_quadros.py` (forwardado por `worker.py`, mesmos flags). Padrão
      `none` — comportamento idêntico a antes de tudo isso existir.
    - Modelos ESPCN (`ESPCN_x2.pb`/`x3.pb`/`x4.pb`, ~90-100KB cada) baixados de
      `github.com/fannymonori/TF-ESPCN` (via `git clone` — `raw.githubusercontent.com`
      direto está bloqueado pelo proxy do sandbox, `github.com` normal não) e
      commitados em `modelos_sr/` no repo — o worker não precisa de internet
      pra achar o modelo em produção/VPS.
    - **ACHADO IMPORTANTE rodando contra a foto NATIVA de verdade** (não a
      versão pré-reduzida do teste de comparação inicial): aplicar ESPCN
      DIRETO numa foto já em alta resolução (5760x2880) tentou alocar ~4.2GB
      e OOMou numa máquina de 3.8GB de RAM. Corrigido com tiling automático
      em `_upscale_espcn_tiled()` (mesma técnica usada pro teste do LapSRN) —
      divide a foto em blocos com pequena sobreposição quando a saída passaria
      de ~12M pixels, roda o modelo por bloco, remonta sem costura visível
      (confirmado por inspeção de um recorte na junção). Bicubic nunca precisa
      de tiling (é so' interpolação, não rede neural — barato em qualquer
      escala).
    - **TEMPO REAL medido com tiling, foto nativa 5760x2880, 2 vCPU**: ESPCN
      escala 2x ~8.3s/foto, escala 4x ~12s/foto — bem mais lento que os
      0.75s do teste de comparação inicial (que alimentava o modelo com 1/4
      da foto, não a foto inteira). Pra uma vistoria com ~1000-1100 fotos,
      isso soma **~2.3 a 3.7 HORAS extras** só de upscale, em cima da já
      longa ~80min do pipeline. Bicubic continua ~0.07s/foto (instantâneo)
      em qualquer escala. **Pedro decidiu testar mesmo assim** contra um
      vídeo real pra ver o resultado prático antes de decidir se vale o custo.
    - **Ainda NÃO testado**: rodar o `worker.py --upscale-metodo espcn` (ou
      bicubic) contra um vídeo real de ponta a ponta — só testado a função
      isolada contra 1 foto real (`quadro_0080.jpg`) nesta sessão. Primeira
      coisa a confirmar no próximo run real do Pedro.

18. **Ferramenta de medição no site (2 cliques na foto → distância) —
    2026-07-16, pedido do Pedro após perguntar "você colocou alguma
    ferramenta pra medição nos panoramas?"**. Reusa `medir_ponto_robusto()`
    (item 16) sem duplicar nenhuma lógica de RANSAC/landmarks. Arquitetura
    escolhida pelo Pedro entre as opções apresentadas: **API Python numa VPS**
    (a mesma que vai rodar `worker.py --poll`), em vez de WASM/reimplementação
    em JS — os landmarks (`mapa.msg`, 100MB+) e o RANSAC sobre dezenas de
    milhares de pontos 3D só fazem sentido processar no servidor.
    - **Gap descoberto e corrigido nesta sessão**: `mapa.msg` (o mapa 3D do
      stella_vslam) NUNCA tinha sido persistido em lugar nenhum — só existia
      na pasta temp durante o `worker.py`, perdido depois do processamento.
      Sem isso, a API de medição não teria o que baixar. Fix: `worker.py`
      ganhou `subir_arquivo_r2()` (upload genérico de arquivo binário, mesmo
      padrão de `subir_json_r2`) — chamado logo após o SLAM rodar com
      sucesso, gravando `mapa_url` no Firestore (mesmo padrão de
      `waypoints_url`/`manifest_url`). Falha de upload não trava o pipeline
      (`mapa_r2_key = None`, log de aviso) — a vistoria só fica sem medição
      disponível, não quebra o resto.
    - **Novo arquivo `api_medicao.py`** (Flask, roda na VPS junto do
      `worker.py --poll`): `POST /medir` (2 pontos `{u, v, pos_w, quat_wc}` —
      a pose vem do próprio `pose_raw` do quadro clicado no navegador, igual
      `super_resolucao.py` — mais `mapa_url` e `escala_slam_metros` opcional)
      → baixa/cacheia o `mapa.msg` (disco + memória, por hash da URL), roda
      `medir_ponto_robusto()` nos 2 pontos, devolve `distancia_slam` (sempre)
      e `distancia_m` (se calibrado) ou motivo de falha por ponto. `POST
      /calibrar` (mesma coisa + `largura_real_m` de uma medida conhecida, ex.
      largura de porta) → devolve `escala_slam_metros` via `calibrar_escala()`
      (já existia em `medir_panorama.py`, sem uso até agora). Cache de
      landmarks em disco (`cache_mapas/`, por SHA1 da URL) evita rebaixar
      100MB+ a cada clique. `MEDICAO_API_KEY` opcional (env var) — proteção
      mínima via header, sem autenticação de usuário de verdade ainda.
    - **Validado com HTTP real nesta sessão** (não só review de código):
      serviu o `mapa.msg` real de 112MB via `http.server`, subiu a API numa
      porta local, e testou os 3 endpoints com curl: `/saude` ok; `/medir`
      com 1 ponto bom (u=0.30/v=0.35, quadro 559 — ver item 16) + 1 ponto
      ruim inventado → devolveu diagnóstico por ponto corretamente
      (dispersao=0.853 no ruim, sucesso=false, sem travar o ponto bom);
      `/medir` com 2 pontos bons sem escala → `distancia_slam=2.1604`;
      mesma chamada com `escala_slam_metros=0.30` → `distancia_m=0.6481`
      (2.1604×0.30, conferido); `/calibrar` com `largura_real_m=0.80` nos
      mesmos 2 pontos → `escala_slam_metros=0.3703` (0.80/2.1604, conferido).
    - **Frontend (`PanoramaViewer.jsx`)**: novas props `modoMedicao`,
      `modoCalibrar`, `mapaUrl`, `apiMedicaoUrl`, `escalaSlamMetros`,
      `larguraCalibracaoM`, `onResultadoMedicao`, `onErroMedicao` — mesmo
      padrão de refs-espelhando-props já usado no resto do componente (não
      recria a cena 3D quando o modo muda). Clique (não arrasto, mesmo
      limiar de 6px do resto do viewer) na ESFERA do panorama, quando
      `modoMedicao`/`modoCalibrar` ativo, tem PRIORIDADE sobre o pulo de
      frame por clique na fita (mutuamente exclusivos). Usa
      `raycaster.intersectObject(esferaVisível).uv` pra achar (u,v) —
      **ATENÇÃO: convenção do eixo v do UV ainda NÃO confirmada
      visualmente** (comentário no código apontando que pode precisar de
      `1 - v` se a medição sair sistematicamente no lado/altura errado).
      Acumula 2 pontos (marcador esfera colorida como feedback visual),
      dispara `fetch()` pro endpoint certo, chama o callback com o
      resultado, limpa pro próximo par. `limparMedicaoRef` permite que o
      componente pai reseté pontos em andamento ao trocar de modo sem
      recriar a cena inteira.
    - **`Visita.jsx`**: botão 📏 na barra de controle (só aparece com
      `manifest_url`, desabilitado com aviso se faltar `mapa_url`) + painel
      flutuante com toggle "Calibrar", input de largura real (m), e exibição
      do último resultado. `escala_slam_metros` persiste no Firestore via
      `atualizarVisita()` assim que uma calibração tem sucesso. Precisa de
      `VITE_API_MEDICAO_URL` (env var, Vercel) apontando pra API na VPS —
      sem isso o botão aparece mas retorna erro claro ao tentar medir.
    - **Ainda NÃO testado**: nenhum teste no navegador de verdade (só
      esbuild/leitura de código) — a convenção do UV, o clique na esfera
      certa durante o crossfade, e a API rodando de fato na VPS (hoje só
      validada localmente neste sandbox) são os 3 pontos a confirmar no
      próximo uso real do Pedro. `api_medicao.py` ainda não foi deployado na
      VPS (ver `obra360_hosting_decision`).
    - **Deliberadamente adiado** (escolha do Pedro, opção "a" quando
      perguntado se preferia terminar o fluxo de 2 cliques primeiro ou
      construir os dois modos juntos): o modo alternativo de medição por
      altura de câmera (`medir_piso_por_altura_bastao()`, já existe em
      `medir_panorama.py` mas sem uso, técnica clássica de apps de AR —
      `distância = altura_câmera / tan(ângulo_elevação)`) fica pra depois
      deste fluxo estar validado ponta a ponta.

19. **Teste de Depth-Anything V2 Small como alternativa/fallback ao RANSAC
    de landmarks — 2026-07-16, Pedro pediu pra testar depois de eu sugerir
    como possível solução pro maior gap conhecido do item 16
    (`medir_por_epipolar_fallback()` não implementado, cliques em regiões de
    mapa esparso simplesmente falham).**
    - Ideia: Depth-Anything estima profundidade densa por pixel a partir de
      UMA FOTO SÓ (sem precisar de landmarks/matching entre keyframes). Como
      cada quadro já tem `pose_raw`, dava pra usar profundidade + pose pra
      obter o ponto 3D de qualquer clique direto — mas na prática, pra 2
      pontos de uma MEDIÇÃO típica (largura de porta/janela/rachadura) que
      normalmente aparecem na MESMA foto, nem precisa de pose_raw/mundo: os
      2 pontos retroprojetados no espaço da própria câmera já dão a
      distância entre eles. Isso é mais simples que todo o pipeline SLAM
      atual — não usa `mapa.msg`, não usa RANSAC, não depende de quantos
      keyframes existem perto do clique.
    - **Problema identificado antes de testar**: as fotos do worker.py são
      EQUIRETANGULARES (360°), mas Depth-Anything foi treinado em fotos de
      câmera normal (pinhole) — rodar direto na equiretangular daria
      profundidade geometricamente errada (distorção forte, principalmente
      longe do "equador" da imagem). Fix: novo arquivo
      `equirect_perspectiva.py` — `recortar_perspectiva(img, u_centro,
      v_centro, fov_h_graus, tamanho_saida)` gera um recorte PINHOLE
      retilíneo centrado em qualquer direção, usando a MESMA convenção
      (u,v)→(longitude,latitude) de `raio_do_clique()` em
      `medir_panorama.py` (não inventa mais uma convenção divergente em
      cima das que já existem). Devolve também a matriz intrínseca `K` do
      recorte, necessária pra depois retroprojetar (pixel, profundidade) em
      ponto 3D.
    - **Validado com dados reais nesta sessão** (não só revisão de código):
      rodei `recortar_perspectiva()` contra o `quadro_0080.jpg` real (o
      mesmo já usado nos testes do item 16) — o recorte resultante tem
      linhas retas (junta do teto, quinas de parede) saindo RETAS, sem a
      curvatura característica da distorção equiretangular, confirmando que
      a reprojeção geométrica está correta. Também validei
      `retroprojetar()`/`amostrar_profundidade()` com um teste sintético:
      2 pontos 3D conhecidos, projetados pela mesma fórmula pinhole,
      retroprojetados de volta — bateram exatos (erro < 1e-6), confirmando
      que a matemática de volta a 3D está correta.
    - **NÃO validado ainda: a parte que realmente importa (o modelo em
      si)**. Este sandbox de desenvolvimento bloqueia `huggingface.co` (e
      todos os mirrors alternativos testados: `cdn-lfs.huggingface.co`,
      `modelscope.cn`, `gitee.com`, `sourceforge.net`) — só `pypi.org` e
      `github.com` (páginas HTML, não os assets de release) estão
      liberados. Não consegui baixar nenhum checkpoint do Depth-Anything V2
      pra rodar de verdade. Novo arquivo `testar_depth_anything.py` está
      pronto (recorta a foto, roda o modelo via `transformers`, retroprojeta
      2 pontos clicados, compara com uma distância real opcional) mas
      **precisa ser rodado na máquina do Pedro** (`pip install torch
      transformers pillow`), não neste sandbox.
    - **RODADO DE VERDADE pelo Pedro em 2026-07-16 — resultado NEGATIVO,
      erro de 204%.** Model id correto descoberto nesse processo (meu
      primeiro palpite, `Metric-Hypersim-Small-hf`, não existe — deu
      `RepositoryNotFoundError`; o nome certo é `Metric-Indoor-Small-hf`,
      "Hypersim" é só o nome do dataset sintético usado pra treinar essa
      variante, não faz parte do model id). Teste real: 2 pontos nas bordas
      do vão de uma porta em `quadro_0080.jpg` (distância real medida com
      trena = 0.86m) → distância estimada = 2.62m (erro 204%). Diagnóstico
      olhando o mapa de profundidade colorido: o vão inteiro (porta +
      corredor escuro atrás dela) saiu como a coisa MAIS DISTANTE de toda a
      cena — mais longe até que o fundo da sala do lado com janela. Os 2
      pontos deram profundidade quase idêntica entre si (7.84 e 7.75,
      consistente — são os 2 lados do MESMO vão), mas o valor absoluto
      atribuído a essa região está inflado (~3x, o que bate com o padrão do
      erro: 2.62/0.86 ≈ 3.0). Causa provável: área escura/sem textura
      (corredor sem luz) confundida pelo modelo com "vazio distante" — falha
      clássica de profundidade monocular em regiões sem informação visual,
      e exatamente o tipo de cena (concreto bruto, sem iluminação) que o
      checkpoint `Metric-Indoor-Small` (treinado em interiores MOBILIADOS e
      iluminados do dataset Hypersim) não viu no treino.
    - **Veredito, por ora**: mesma conclusão do upscale (item 17) — o
      checkpoint métrico não generaliza bem pra cenas de obra em construção
      sem acabamento, pelo menos não em áreas escuras/sem textura. Ainda não
      testado numa parede lisa e bem iluminada (próximo passo sugerido, pra
      isolar se o problema é so' de áreas escuras/sem textura ou mais
      geral). Enquanto isso não for testado, não recomendo usar este método
      como fallback de produção — ver item 20 pra uma alternativa mais
      promissora que surgiu dessa mesma discussão.

20. **Calibração automática de escala via altura da câmera (não a largura de
    porta/janela do projeto) — 2026-07-16, ideia do Pedro depois do teste
    negativo do Depth-Anything.** Pedro observou algo importante: usar a
    largura de porta/janela EXTRAÍDA DA PLANTA como referência de calibração
    (o que `calibrar_por_portas()` já faz) tem um problema de origem — na
    obra, o que foi construído quase sempre diverge um pouco do projeto. A
    altura da câmera acima do piso, por outro lado, é uma constante FÍSICA
    do equipamento de captação (Pedro confirmou: 2.0m nesses frames), não
    depende de nada ter sido construído conforme a planta.
    - **Novo arquivo `calibrar_altura_camera.py`**: em vez de calibrar por
      clique/porta, ajusta um plano por RANSAC (restrito a normais
      quase-verticais, pra não confundir com parede) nos landmarks MAIS
      BAIXOS de toda a nuvem de pontos do `mapa.msg` (candidatos a piso),
      mede a distância perpendicular mediana de toda a trajetória da câmera
      até esse plano (em unidades SLAM), e divide a altura real conhecida
      (2.0m) por esse valor — `escala_slam_metros` automática, sem clicar
      em nada, sem depender de nenhuma medida do projeto.
    - **Bug encontrado e corrigido no mesmo dia, ANTES de confiar no
      resultado**: a 1ª versão escolhia os candidatos a piso com um corte
      relativo à altura da própria câmera (landmarks abaixo de um percentil
      da altura da câmera, com margem pequena) — isso pegava majoritariamente
      RODAPÉ/PAREDE perto da altura do operador (paredes têm muito mais
      pontos SLAM que o piso, porque concreto liso dá pouca textura pro ORB
      se agarrar), não o piso de verdade. Resultado da 1ª versão:
      `escala_slam_metros=4.27`. Fix: cortar pelos percentis mais baixos da
      nuvem de landmarks INTEIRA (não relativos à câmera) — busca
      especificamente os pontos mais baixos que existem no mapa.
    - **Validado com dados reais nesta sessão**: rodando a versão corrigida
      contra o `mapa.msg` real (927 keyframes, 19763 landmarks) com 4 cortes
      de percentil diferentes (10%, 15%, 20%, 25% dos landmarks mais
      baixos), a escala resultante convergiu fortemente: 3.719, 3.723,
      3.714, 3.729 — dispersão de só **0.4%** entre os 4 cortes (bem abaixo
      da tolerância de 8% que a função `calibrar_por_altura_camera_robusto()`
      exige pra aceitar o resultado). Isso é um sinal forte de que o plano
      encontrado é estável e não um acaso do RANSAC — mesmo espírito de
      "várias tentativas precisam concordar" já usado em
      `medir_ponto_robusto()` (item 16).
    - **Pendência real antes de confiar nisso pra produção**: ainda NÃO
      cross-validado contra uma medida real independente. A única medida
      real que o Pedro forneceu nesta sessão (a porta de 0.86m em
      `quadro_0080.jpg`) fica exatamente na região de mapa esparso onde
      `medir_ponto_robusto()` já falha (ver item 16/19) — não deu pra usar
      essa porta pra confirmar a nova escala porque o RANSAC nem consegue
      achar um ponto 3D ali. Próximo passo: pedir ao Pedro as coordenadas de
      um par de pontos NUM LOCAL BEM MAPEADO (onde `medir_ponto_robusto` já
      dá `sucesso=True`) com uma distância real conhecida (medida com trena),
      pra comparar a distância em metros usando esta nova escala contra a
      medida real — só depois disso decidir se essa calibração substitui ou
      complementa `calibrar_por_portas()` no `worker.py`.
    - **2ª tentativa de cross-check, mesma sessão, TAMBÉM sem sucesso**:
      Pedro copiou `quadro_0714.jpg` (outro frame da mesma vistoria,
      `pose_raw` via retrofit também, `dist_pose_s=0.003`) pra pasta de
      teste e escolheu 2 pontos nas bordas de OUTRO vão de porta (pixels
      245,580 e 931,498 do recorte gerado com `--u-centro 0.3 --v-centro
      0.5 --fov 90 --tamanho 1200`). Convertendo de volta pra (u,v) e
      rodando `medir_ponto_robusto()` com a pose real do quadro 714: os 2
      pontos falharam de novo (dispersão 42+ unid. SLAM) — MESMO padrão do
      quadro_0080: vãos de porta/corredor parecem ser sistematicamente mal
      mapeados nesta vistoria (pouca luz/textura ali).
    - **Varredura sistemática feita pra parar de tentar às cegas**: rodei
      uma grade de (u,v) no `quadro_0714.jpg` (passo 0.10 em u e v, faixa
      v=0.3–0.7) chamando `medir_ponto_robusto()` em cada ponto — achei 11
      pontos com `confianca='alta'`, TODOS concentrados na faixa v≈0.5–0.7
      (mais baixa da foto) e NENHUM no `quadro_0080.jpg` na mesma varredura
      (0 pontos de alta confiança). Os pontos bons do quadro 714 caem quase
      todos no CHÃO (base de parede/batente de porta encontrando o piso) —
      condizente com o item 20: o piso tem poucos landmarks mas os que
      existem são estáveis; paredes lisas no meio da foto (onde a maioria
      dos cliques de medição realmente vai acontecer na prática, ex. altura
      de porta/janela) continuam mal cobertas nesta vistoria específica.
      Gerei `recorte_bompar_marcado.jpg` marcando 4 desses pontos bons
      (A/B/C/D, todos onde uma parede encontra o chão) e pedi ao Pedro pra
      medir com trena a distância real entre dois deles (B: base da parede
      central: A: base do batente da porta à direita) — **resposta do
      Pedro ainda pendente no momento em que esta sessão foi encerrada**.
    - **Ponto em aberto pra próxima sessão**: assim que o Pedro der a
      distância real entre B e A (ou C e D), rodar `medir_ponto_robusto()`
      nesses 2 pontos com a pose do quadro 714 (já validada, `dist_pose_s`
      baixo), multiplicar `distancia_slam` pela escala 3.721 e comparar com
      a medida real — essa é a validação que falta pra decidir se
      `calibrar_por_altura_camera_robusto()` pode substituir/complementar
      `calibrar_por_portas()` em produção. Os scripts/dados pra isso já
      estão prontos (`calibrar_altura_camera.py`, `equirect_perspectiva.py`,
      `mapa.msg` e `manifest_corrigido.json` na pasta
      `teste_medicao_e_resolucao/`) — só falta o número real do Pedro.

21. **DESCOBERTA GRANDE 2026-07-17 (sessão de retomada): 2 bugs que invalidam
    as conclusões de medição dos itens 16/19/20.** Investigando a validação
    da escala por altura (item 20), com acesso a shell/git direto nesta sessão:
    - **BUG A — `pose_raw` e `mapa.msg` estão em REFERENCIAIS DIFERENTES.**
      Casando keyframes do mapa com poses do manifest por timestamp
      (vistoria `Nf1KoXXPByR9G01WvnjO`): distância mediana de 15.5 unid
      entre os dois. Umeyama: rotação ~180° em torno do eixo vertical +
      translação, escala IGUAL (s=1.0033), resíduo mediano 0.29 (drift
      trajetória-vs-mapa-otimizado). Afeta TUDO que combina `pose_raw` com
      landmarks do mapa: `super_resolucao.py`, `api_medicao.py`, e todos os
      testes do item 16 feitos com pose_raw. As falhas "sistemáticas" em
      vãos de porta e no quadro_0080 eram em grande parte ISSO (raio
      atirado do lugar errado), não mapa esparso: com a pose corrigida,
      7/8 cliques nas bordas da porta de 0.86m deram confiança alta
      (antes: 1/8). Fix possível em runtime: Umeyama por timestamp entre
      keyframes do mapa e frame_trajectory (ou usar keyframes do mapa
      interpolados/slerp direto — validado nesta sessão).
    - **BUG B — o RANSAC de plano local devolve ponto confiante mas ERRADO
      quando a nuvem perto do raio é mista/inclinada.** Prova visual:
      overlay dos landmarks reprojetados sobre `quadro_0080.jpg` (arquivos
      `overlay_landmarks_*.jpg` em `teste_medicao_e_resolucao/`) mostra que
      na direção da porta os landmarks estão a 1.5–4.0 unid, mas o
      RANSAC devolvia interseções a 0.8–1.0 com "confiança alta" — o plano
      ajustado numa nuvem inclinada intersecta o raio onde não há nada. A
      checagem de consistência (item 16) NÃO pega isso (todas as combos
      concordam no mesmo plano errado).
    - **Fix validado pro BUG B: seleção de landmarks POR REPROJEÇÃO NA
      IMAGEM** (não por proximidade 3D ao raio): reprojeta os landmarks na
      equiretangular com a pose correta, seleciona os que caem a <25px do
      clique, mediana de profundidade com corte de outliers. Resultado no
      único caso com trena real (porta 0.86m, quadro_0080): vão = 0.79
      unid → com a escala da calibração por portas (~1.0 m/unid), 0.79m
      vs 0.86m real = **erro 8.5%** (contra falha total/204% antes).
    - **A escala real DESTA vistoria ≈ 1.0–1.1 m/unid** — confirmada por 3
      vias independentes: calibração por portas (0.996 mediana ao longo da
      trajetória), extensão do prédio (19×28 unid vs planta em metros,
      54 ambientes ≈ 10m² cada), e agora a porta com trena via reprojeção
      (1.089). Velocidade de caminhada implícita 0.52 m/s (plausível).
    - **A escala por altura da câmera (3.721, item 20) está ERRADA para
      esta vistoria** — o overlay prova que o piso de concreto liso NÃO
      TEM landmarks (histograma: quase nada abaixo de y=-1.5, onde o piso
      real deveria estar com escala 1.0); o "plano mais baixo" que a
      calibração achou era rodapé/base de parede (0.42–0.54 abaixo da
      câmera). `calibrar_altura_camera.py` não deve ir pra produção sem um
      guard que detecte essa situação (ex.: comparar a escala resultante
      com a da calibração por portas e recusar se divergirem muito).
    - **A medida da trena B–A do item 20 NÃO é mais necessária pra escala**
      (a validação veio da porta 0.86m) — mas 1-2 medidas extras de trena
      em outros pontos continuam úteis pra confirmar o erro típico do
      método por reprojeção.
    - **IMPLEMENTADO em código na mesma sessão (2026-07-17, tarde)**:
      (a) `medir_panorama.py` ganhou `pose_no_frame_do_mapa()` (pose por
      slerp dos keyframes do PRÓPRIO mapa.msg, via t do quadro — dispensa o
      pose_raw e o problema de referencial inteiro), `reprojetar_landmarks()`
      e `medir_por_reprojecao()` (seleção por reprojeção + cluster de
      profundidade mais denso com REGRA DO OCLUSOR: entre clusters ≥80% do
      maior, fica o mais próximo — você clica no que VÊ) e
      `medir_vao_coplanar()` (estimativa auxiliar com profundidade única).
      (b) `api_medicao.py`: pontos agora mandam `{u, v, t}` (pose_raw virou
      fallback legado com aviso); usa `medir_por_reprojecao`; `/medir`
      devolve `distancia_slam` (por ponto, principal) + coplanar/divergência
      como diagnóstico. (c) `super_resolucao.py`: todas as poses (clique +
      candidatos) vêm de `poses_no_frame_do_mapa()`. (d)
      `PanoramaViewer.jsx`: envia `t` do quadro no ponto.
      **VALIDADO na porta de 0.86m (trena real)**: por ponto 0.865 slam ×
      escala 0.996 = **0.861m — erro 0.2%**; e o `/calibrar` HTTP real na
      mesma porta devolveu escala 0.9944, batendo com a calibração por
      portas (0.996) — os dois métodos independentes CONVERGEM. Teste HTTP
      de ponta a ponta rodado neste sandbox (mapa.msg de 112MB servido
      local): `/medir` → distancia_m=0.8614. Ressalva honesta: erro de 0.2%
      tem sorte envolvida (uma única medida de trena); o coplanar deu 16%
      no mesmo vão (porta vista em ângulo, profundidades 2.05/2.41
      genuinamente diferentes — por isso o por-ponto é o principal).
      Scripts/overlays de validação em `teste_medicao_e_resolucao/`.

22. **Fim do Cloudflare Stream: upload do site direto pro R2 + fila da VPS
    (2026-07-17, noite).** Decisão do Pedro: "não vamos mais usar o Cloudflare
    Stream, e sim o storage". Fecha o GAP 3 do worker.py (a fila `na_fila`
    nunca era alimentada pelo site).
    - `api_medicao.py` ganhou `/upload/iniciar` (cria multipart no R2 e
      devolve todas as presigned URLs de parte — 100MB/parte, exp. 24h),
      `/upload/concluir` e `/upload/abortar`. Credenciais R2 só no servidor
      (mesmas env vars do worker; no Coolify precisam ser configuradas).
      `R2_ENDPOINT_URL`/`UPLOAD_PARTE_TAMANHO_MB` são overrides só de teste.
    - `Upload.jsx`: tus/Stream removidos; fatia o arquivo, PUT por parte com
      retry (3x), progresso por bytes, aborta a sessão multipart em caso de
      erro. Ao final `criarVisita({video_r2_key, status:'na_fila',
      hls_url:null})` — o worker da VPS baixa do R2 e processa sozinho.
    - `visitas.js::criarVisita` aceita `video_r2_key`/`status` (defaults
      antigos preservados). `Visita.jsx` ganhou banners de `na_fila`/
      `processando`/`erro`.
    - **Infra necessária (uma vez)**: (a) Coolify → Environment Variables da
      API: R2_BUCKET_NAME/R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY
      + Redeploy; (b) CORS do bucket R2 (painel Cloudflare → R2 → bucket →
      Settings → CORS policy): AllowedOrigins=[dominio do site no Vercel],
      AllowedMethods=[GET,PUT], AllowedHeaders=[*], **ExposeHeaders=[ETag]**
      (sem ETag exposto o navegador não consegue montar a lista de partes).
    - **Validação**: endpoints exercitados contra S3 local (moto) — iniciar/
      concluir/abortar ok; o PUT pré-assinado o moto não implementa direito
      (500 mesmo em us-east-1; limitação conhecida do emulador), e o sandbox
      bloqueia o R2 real por proxy. **Falta o teste real no navegador**
      (primeiro com um vídeo PEQUENO): upload → na_fila → worker da VPS pega
      → processado. Se a parte falhar com erro de ETag/CORS, é o item (b)
      acima. esbuild ok em Upload/Visita/visitas.
    - **Exclusão completa de vistoria (mesma noite, pedido do Pedro)**:
      `/vistoria/excluir-storage` na api_medicao.py apaga o prefixo
      `{visita_id}/` inteiro (paginado, >1000 objetos) + o vídeo bruto
      (`video_r2_key`), com guarda de id (regex alfanumérico ≥8 — id
      vazio/malicioso varreria o bucket). `visitas.js::excluirVisitaCompleta`
      chama a API e só então deleta o doc; `Home.jsx` trocou o `confirm()`
      por modal que lista o que será apagado e, se a limpeza do storage
      falhar, oferece explicitamente "excluir só o registro". Validado
      contra moto: 1204 objetos paginados + vídeo removidos, vistoria
      vizinha intacta, ids inválidos rejeitados (400).

23. **Calibração automática "zero-clique" por busca global de portas
    (2026-07-18, roadmap Fase 4.3 - "a maior barreira de uso hoje").** Ideia do
    Pedro: em vez de refinar a partir de um heading/escala configurado na mão,
    BUSCAR heading+escala+espelhar do zero contra as portas do PDF. Motivou-se
    porque a última vistoria (2026-07-17) precisou de heading manual bem
    diferente do default 90 - a premissa do item 10 ("heading é constante da
    câmera") não vale sempre; heading varia por vídeo e precisa entrar na busca.
    - Por que a `calibrar_por_portas` existente não bastava: ela é um
      REFINAMENTO que depende de `detectar_cruzamentos_vaos`, que exige a
      trajetória JÁ cruzando os arcos das portas. Heading errado no chute →
      zero cruzamentos → desiste → força calibração manual. Chicken-and-egg.
    - **Nova `calibrar_auto_por_portas()` em `processar_vistoria.py`**: (1)
      busca grosseira heading (passo 6°) × escala (faixa centrada na razão de
      bbox trajetória/portas) × espelhar, pontuada por PROXIMIDADE dos centros
      de porta (quantas casam < ~4.5%), translação por ICP; (2) pega os basins
      distintos; (3) refino local de heading/escala (passo 1°); (4) DESEMPATE
      por cruzamento GEOMÉTRICO real (a proximidade de centros sozinha tem
      ambiguidade de espelho num prédio simétrico - a passagem direcional pelo
      arco desfaz). Entrega o resultado como CHUTE pra `calibrar_por_portas`,
      que refina escala+âncora e aplica o gate de holdout já existente
      (heading/espelhar ficam fixos no que a busca achou). `run_map_matching`
      chama a auto primeiro; se achar portas suficientes, adota; senão cai no
      manual (comportamento antigo, sem regressão).
    - **VALIDADO em dados reais** (`VID_021` + 40 portas de `planta_passagens.json`
      + `gabarito_trajetoria_6_pavimento.json`): a busca grosseira sozinha
      recupera heading=90/escala=0.46/espelhar=True do zero, batendo o gabarito
      humano a 2.7%; e o pipeline completo, partindo de um chute
      PROPOSITALMENTE ERRADO (heading=0, escala=0.1), conserta sozinho pra
      heading≈76/escala≈0.46/espelhar=True com a trajetória final a 2.74% do
      gabarito (vs 2.67% da calibração manual "certa") - 17-20 portas cruzando
      a 0.1-2.4% cada. **Ressalva honesta**: `planta_passagens.json` só tem
      CENTROS de porta; a geometria de arco (hinge/extremos) usada no desempate
      foi SINTETIZADA a partir do gabarito pra este teste. A busca grosseira
      está provada em dados 100% reais; o desempate por cruzamento roda sobre
      arcos plausíveis mas sintéticos - **falta um run real do worker.py com os
      arcos de verdade do `extrair_portas.py`** pra fechar 100%. Primeira coisa
      a confirmar no próximo processamento.

24. **A métrica de resíduo da calibração automática NÃO distingue heading
    certo de errado (achado do Pedro, 2026-07-21, primeira vistoria real
    processada 100%% pela VPS).** A auto-calibração adotou um heading errado
    reportando "28 portas, resíduo 0.44%%" com falsa confiança, atropelando a
    âncora manual do Pedro (que mudou de lugar sozinha - a impressão digital do
    problema). **Reproduzido nos dados reais** (`frame_trajectory.txt` +
    `vaos.json` desse run, trajetória crua derivada via `tum_para_raw_waypoints`):
    - O heading CORRETO do Pedro (137.1) dá resíduo 0.0072/7 portas; headings
      ERRADOS pontuam MELHOR (90→0.0009/6, 160→0.0048/9). Otimizar por
      resíduo/nº-de-portas necessariamente escolhe errado.
    - Causa: depois de fixar o heading, o código ajusta escala+translação
      LIVRES, que ABSORVEM o erro de rotação - pior ainda com portas
      quase-colineares (corredores). A âncora "andar sozinha" é a translação
      compensando a rotação errada.
    - Camada mais profunda: a trajetória está ENTORTADA pela deriva do SLAM
      (só 7 de 43 portas cruzam no heading certo). ICP de rotação LIVRE
      (Umeyama completo) também não acha um heading único - converge pra
      headings bem diferentes (42/139/167...) dependendo do start, vários
      casando as 43 portas. **Numa trajetória com deriva NÃO existe heading
      rígido único correto** - o humano escolhe reconhecendo a forma global do
      prédio, o que métrica de porta local não captura. Nenhuma métrica melhor
      sozinha conserta; a raiz é a qualidade do SLAM.
    - **FIX implementado (commit desta sessão): gate de ambiguidade em
      `calibrar_auto_por_portas`.** Varre o heading contando cruzamentos
      geométricos reais; se ≥2 headings DISTINTOS (>30°) chegam a ≥70%% do
      melhor, devolve `{ambiguo:True}` em vez de adotar. `run_map_matching`
      então MANTÉM o manual do usuário INTACTO (pula até o refino, que também
      moveria a âncora) e imprime aviso pra verificar no site. **Validado nos
      dados reais**: flagra este caso (headings empatados [144,40,254]) e não
      adota. NÃO acha o heading certo (isso exige trajetória limpa) - só para
      de sobrescrever o manual com lixo confiante. **Pendência**: validar que
      o gate NÃO rejeita casos limpos (sem vistoria limpa com geometria de arco
      em mãos pra testar - o `planta_passagens.json` só tinha centros).

25. **Orientação das portas de correr (PJ) saía errada (achado do Pedro,
    2026-07-21).** Sobre a planta P073, várias PJ (PJ3/PJ5/PJ6/PJ10) apareciam
    espetando pro meio dos cômodos em ângulos aleatórios (PJ6 em 67°) em vez de
    deitadas na parede. Causa em `_achar_angulo_parede()` (extrair_portas.py):
    o ranking da parede usava a distância do rótulo até o **ponto médio** do
    segmento - uma parede longa que passa colada na porta tem o ponto médio
    longe, então uma cota curta ou parede perpendicular ganhava. Fix: (a)
    distância **perpendicular** rótulo→segmento (`_dist_ponto_segmento`); (b)
    descarta segmentos curtos (< 30pt - paredes são longas, batentes/cotas
    curtos enganavam); (c) fallback de face única quando não há par de faces
    paralelas. **Validado visualmente em 2 plantas** (PNG das portas sobre o
    PDF): P073 (12 PJ - "melhorou muito") e P070 (2 PJ corretas, convenção de
    nome P8/P9 diferente), sem regressão nas portas de giro (que usam o
    detector de arco, não tocado). Só afeta PJ/correr. Melhora ~12/43 portas da
    P073 - reduz ruído na calibração por portas, mas NÃO resolve a ambiguidade
    de heading do item 24 (essa é deriva do SLAM).

26. **Calibração automática revista (2026-07-21, decisão do Pedro): heading
    vem do MANUAL, só escala+âncora são automáticas (com âncora TRAVADA).**
    Testado em run boa E ruim: contagem de portas NÃO distingue heading (um
    heading errado cruza tantas portas quanto o certo, porque a trajetória
    cobre a planta - sinal local, insensível à rotação global). Buscar heading
    do zero pelas portas não funciona. O que funciona (e sempre funcionou - a
    imagem de 1.3%): o humano dá o heading, a automática refina escala+âncora.
    - Ideia do Pedro pra âncora: travar a âncora a um RAIO MÁXIMO do ponto A
      marcado, senão o ajuste de translação livre escorrega pra casar portas
      por acaso (era a "âncora andando sozinha"). Implementado em
      `calibrar_por_portas` (`_clamp_ancora`, `raio_ancora=0.12` unid. planta):
      clampa a âncora ao raio e reajusta só a escala com ela presa.
    - `run_map_matching`: a busca auto de heading só adota se achar heading
      ÚNICO (raro); senão MANTÉM a bússola manual e SEMPRE refina escala+âncora
      (presa). Removido o "pular refino" do gate de ambiguidade (item 24) - agora
      refina mesmo quando ambíguo, só não mexe no heading.
    - **Validado**: heading manual 137.1 + âncora A → refino mantém heading,
      escala 0.0294, âncora move só 0.003 do A (limite 0.12), 18 portas,
      residual 1.2%. **Falta**: Pedro reprocessar com a bússola correta setada
      pra ver o encaixe melhorar no site.

## Pendências conhecidas (não resolvidas)

- Confirmar no navegador os marcadores de foto do `commit13` (planta e fita
  3D) — só validado por esbuild até agora, não visualmente.
- Confirmar com o Pedro se o `commit8` (max-h-[48vh]) resolveu de vez o
  drawer — se não, o problema pode estar em outro ancestral flex, vale
  inspecionar a árvore inteira de `Visita.jsx` em vez de só o wrapper local.
- Resolver o backlog de git não commitado (ver seção acima).
- PJ14 (porta de correr) não gera vão sintético — célula de TIPO vem colada
  sem espaço (`CORRER(2FLS)/FIXO(2FLS)`), não bate com
  `TIPOS_ESQUADRIA_VALIDOS`. Baixa prioridade.
- 1 dos 54 ambientes extraídos saiu com nome errado (encoding/fonte
  corrompida no PDF em si, não no parser). Baixa prioridade.
- Item 4.5 do roadmap: botão "🕐 Histórico" (abaixo do Painel de Controle,
  `commit11`) foi construído em 2026-07-15 — abre dropdown com as vistorias
  do mesmo `local_id` por data, navega entre elas. **Ainda falta a outra
  metade do item**: o viewer comparativo lado-a-lado de panoramas
  sincronizados (por enquanto só troca de página, não compara). Vistorias
  antigas sem `local_id` (anteriores a 2026-07-14) mostram o botão desabilitado.
  **Não confirmado pelo Pedro rodando no navegador ainda.**
- Candidatura ao SDK da Insta360 (https://www.insta360.com/sdk/apply):
  rascunho de texto em inglês pro campo "Reasons for SDK application" foi
  oferecido ao Pedro, sem confirmação se ele quer ajustar tom/tamanho ou
  incluir um número concreto de vistorias/mês.
- **CONFIRMADO 2026-07-16** (1º run real de ponta a ponta, vistoria
  `Nf1KoXXPByR9G01WvnjO`, vídeo `VID_20260710_163541_00_022.mp4`, total
  4586.9s): a suspeita do item 9 se confirma — o decode full-res do
  `gerar_quadros.py` (2390.6s, 52.1% do tempo total) pesa mais que o
  próprio `stella_vslam` (352.9s, 7.7%). Somando a redução de vídeo pro
  SLAM (494.6s) com esse decode full-res, são 2885.2s (62.9% do total)
  gastos decodificando o MESMO vídeo duas vezes. Upload pro R2 é o 2º maior
  custo isolado (1333.7s, 29.1%). **OTIMIZADO 2026-07-17** (commit
  `a558ece`): `rodar_slam.py --video-reduzido-out` preserva a cópia reduzida
  que já era gerada pro SLAM; `gerar_quadros.py --video-analise` varre ELA
  pra escolher o frame mais nítido (a nitidez já era calculada num downscale
  de 640px) e extrai só os ~1000 frames escolhidos do original via seek
  (`extrair_frame_no_tempo`, sem uso desde o item 13); `worker.py` liga as
  duas pontas e o corte inicial reencoda com `-g 15` (GOP curto — sem isso
  cada seek decodificaria até 8s de vídeo full-res). De brinde: o fallback
  de flags do ffmpeg no `video_io.py` agora também detecta rejeição na 1ª
  leitura (o `poll()` da abertura é corrida que máquina lenta perde —
  reproduzido em sandbox gerando 0 panoramas em silêncio). **Validado só com
  vídeo sintético** (mesmos quadros escolhidos nos 2 modos, 0 falhas de
  extração) — confirmar os `[TIMING]` novos no próximo run real; se a
  extração por seek ficar lenta em H264 de GOP longo (vídeo SEM corte
  inicial vindo direto da câmera), o plano B é paralelizar a extração
  (ThreadPool) ou reencodar com GOP curto antes.
- Falta ainda confirmar rodando de verdade: a mudança em `calibrar_por_portas`
  (heading/espelhar fixos, só escala calibrada) — validado só
  matematicamente/mock nesta sessão, não com Docker+SLAM real ainda (o run
  de 2026-07-16 usou calibração automática por portas com sucesso -
  residual 0.0057 - mas não isola especificamente essa mudança).
- `super_resolucao.py` (item 13) — validado só com dados sintéticos. 1º run
  real (2026-07-16, vistoria `Nf1KoXXPByR9G01WvnjO`) rodou com sucesso mas
  **sem scipy instalado no Python local do Pedro** (o que roda
  `gerar_quadros.py`, fora do Docker) - o guard funcionou como esperado (não
  travou o pipeline), mas nenhum quadro dessa vistoria ganhou `pose_raw`.
  Criado `retrofit_pose_raw.py` (novo script, não reprocessa a vistoria -
  anexa `pose_raw` num manifest.json já gerado usando o `frame_trajectory.txt`
  ainda salvo na pasta temp, casando pelo campo "t" - menos preciso que o
  fluxo normal via fidx exato, ±0.5s típico) pra Pedro testar sem esperar
  os ~76min de novo. 2º run (mesma vistoria, scipy já instalado) confirmou
  `pose_raw` populado nos 1077/1077 quadros. **Ainda falta**: ganho visual
  de fato em pontos vistos por 2+ quadros (nenhum teste real de fusão
  multi-frame com pontos reais ainda — só o passo 1, achar o ponto 3D do
  clique, foi testado com dados reais até agora, ver item 16) antes de
  ligar isso na UI. **ATENÇÃO (2026-07-17): aplicar os fixes do item 21
  antes de retomar isso.**
- `medir_por_epipolar_fallback()` continua **não implementado** — mas ver
  item 21: boa parte das falhas atribuídas a "mapa esparso" era o bug de
  referencial; reavaliar a real necessidade dele depois dos fixes.
- Item 16 (`medir_ponto_robusto`) — **as conclusões de validação foram
  revisadas pelo item 21**: os testes com pose_raw estavam no referencial
  errado, e o método de plano-RANSAC pode devolver ponto confiante e errado.
  Substituir/complementar por `medir_por_reprojecao()` (item 21).
- **Item 18 (ferramenta de medição) — nada testado no navegador ainda.**
  Fixes do item 21 já implementados e validados no backend (erro 0.2% na
  única trena disponível). Ordem: (1) ~~fixes item 21~~ FEITO; (2) subir
  `api_medicao.py` na VPS e configurar `VITE_API_MEDICAO_URL` no Vercel;
  (3) rodar `worker.py` numa vistoria nova pra confirmar `mapa_url` no
  Firestore; (4) testar 📏 no site contra medidas de trena conhecidas
  (colecionar mais medidas reais — só existe UMA até agora); (5) se o ponto
  clicado aparecer no lado/altura errado, ver a convenção do eixo v do UV
  (`pegarUVDoClique` no `PanoramaViewer.jsx`).

## Notas operacionais (não esquecer)

- **Docker Desktop precisa estar rodando** antes de `worker.py` — sem ele,
  `rodar_slam.py` falha e o worker cai (por design) no fallback de odometria
  leve (`process_trajectory.py`), que é bem menos preciso. Não é bug; é
  esperado no ambiente local do Pedro. Na VPS (Contabo Cloud VPS 20, KVM —
  ver `obra360_hosting_decision`) isso não deve acontecer se o Docker estiver
  provisionado corretamente no servidor.
- Vercel exige rebuild manual/automático após `git push` pro frontend
  refletir mudanças — scripts Python (`worker.py` e afins) rodam direto do
  disco, sem build step.
- Deploy do `worker.py --poll` na VPS ainda é o próximo passo pendente (ver
  `obra360_hosting_decision`) — nesta sessão o worker ainda rodou na máquina
  local do Pedro.
- **CUIDADO (descoberto 2026-07-17): o sync entre o lado Windows e o
  sandbox do Cowork pode TRUNCAR arquivos que CRESCEM** (o conteúdo novo é
  gravado mas cortado no tamanho antigo do arquivo, terminando no meio de
  uma palavra). Foi a causa dos vários arquivos truncados encontrados no
  working tree nesta sessão (worker.py, PanoramaViewer.jsx, Visita.jsx,
  upscale_quadros.py, testar_depth_anything.py, CLAUDE.md). Mitigação:
  depois de editar arquivo grande, conferir `tail` + tamanho nos DOIS
  lados antes de commitar; commitar logo pra prender o conteúdo certo no
  git.

## Onde encontrar o quê

- `OBRA360_ROADMAP.md` — arquitetura, decisões de backend (R2 vs Firebase),
  fases comerciais priorizadas, critério de "pronto pra vender".
- `OBRA360_SLAM_HANDOFF.md` — pipeline SLAM em si (stella_vslam, scripts,
  lições técnicas de coordenadas/aspecto, backlog de features).
- Este arquivo — o que mudou nas sessões de 2026-07-15/16/17 e notas
  operacionais do dia a dia.
