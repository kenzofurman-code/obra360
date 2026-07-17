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
    - **Pendências reais antes de confiar nisso pra produção**: (1) rodar o
      script de verdade contra fotos reais e comparar com trena, do mesmo
      jeito que fizemos com PSNR/SSIM pro ESPCN — não aceitar "parece certo"
      como validação; (2) confirmar se o checkpoint métrico
      (`Metric-Hypersim-Small`, treinado em cenas de interior) generaliza
      pra concreto bruto/pouca luz de obra em construção, ou se sofre do
      mesmo problema de domínio que o upscale (item 17); (3) o exact model
      id no Hugging Face não foi confirmado por mim (sem acesso pra
      conferir) — só um palpite educado no código, sinalizado com comentário
      pra ajustar se o `transformers` reclamar; (4) mesmo que funcione bem,
      só serve pra pares de pontos visíveis na MESMA foto — não substitui
      o SLAM pra medições entre pontos distantes/vistos de ângulos muito
      diferentes.

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
  custo isolado (1333.7s, 29.1%). Ainda não otimizado — só medido/confirmado.
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
  ligar isso na UI.
- `medir_por_epipolar_fallback()` (usado quando `medir_ponto_robusto`/RANSAC
  não acha plano confiável — ver item 16) continua **não implementado**.
  Sem ele, cliques em regiões de mapa esparso ou pouco texturizadas
  simplesmente falham (com motivo claro, não silenciosamente) em vez de
  serem medidos por um método alternativo. Maior lacuna de cobertura atual
  do `medir_panorama.py`/`super_resolucao.py`.
- Item 16 (`medir_ponto_robusto`) validado com dados reais nos DOIS
  sentidos: além dos cliques problemáticos já conhecidos (janelas de
  `quadro_0080.jpg`, portas de `quadro_0559.jpg` — todos corretamente
  `sucesso=False`), uma varredura de grade em `quadro_0559.jpg` achou casos
  reais de `sucesso=True` (ex.: u=0.30/v=0.35 — as 6 combinações de busca
  convergem com dispersão de só 0.018 unid. SLAM, `confianca='alta'`),
  confirmando que a função não é excessivamente conservadora — ela aceita
  cliques bem suportados por landmarks e só rejeita os genuinamente
  inconsistentes. Ainda falta o Pedro confirmar visualmente (clicando na
  interface, não via script) qual ponto da foto corresponde a u=0.30/v=0.35
  antes de usar esse caso como referência de "bom clique" pra ensinar o uso
  da ferramenta.
- **Item 18 (ferramenta de medição) — nada testado no navegador ainda.**
  Backend (`api_medicao.py`) validado com HTTP real neste sandbox, mas não
  deployado na VPS. Frontend (`PanoramaViewer.jsx`/`Visita.jsx`) só validado
  por leitura de código, sem browser real disponível nesta sessão. Ordem
  sugerida pro Pedro testar: (1) subir `api_medicao.py` na VPS e configurar
  `VITE_API_MEDICAO_URL` no Vercel; (2) rodar `worker.py` numa vistoria nova
  pra confirmar que `mapa_url` é gravado no Firestore; (3) abrir essa
  vistoria no site, clicar 📏, clicar "Calibrar" numa porta de largura
  conhecida, depois medir outro trecho e comparar com a régua real; (4) se o
  ponto clicado aparecer sistematicamente no lado/altura errado da foto,
  provavelmente é a convenção do eixo v do UV (ver comentário em
  `pegarUVDoClique` no `PanoramaViewer.jsx`).

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

## Onde encontrar o quê

- `OBRA360_ROADMAP.md` — arquitetura, decisões de backend (R2 vs Firebase),
  fases comerciais priorizadas, critério de "pronto pra vender".
- `OBRA360_SLAM_HANDOFF.md` — pipeline SLAM em si (stella_vslam, scripts,
  lições técnicas de coordenadas/aspecto, backlog de features).
- Este arquivo — o que mudou na sessão de 2026-07-15 (pipeline P070) e notas
  operacionais do dia a dia.
