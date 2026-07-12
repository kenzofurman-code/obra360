# Obra360 — Roadmap para viabilidade comercial

> Baseado na leitura do `OBRA360_SLAM_HANDOFF.md` + análise completa do repositório (frontend React/Firebase + 8 scripts Python) em 2026-07-10. Decisões abaixo já confirmadas com o Pedro; revisar este arquivo a cada marco relevante.

## 1. O que existe hoje (dois projetos que ainda não se encontraram)

**A) App em produção** (este repo, React+Vite+Tailwind, deploy Vercel): player de vídeo 360 (Video.js/HLS via Cloudflare Stream) sincronizado com planta baixa (canvas + PDF.js), calibração manual por 2 âncoras, editor de waypoints, tudo salvo em Firebase (Firestore+Storage). Funciona, está no ar, ~60 commits de ajuste fino de orientação/cone de visão/calibração.

**B) Pipeline SLAM validado** (scripts Python soltos na raiz, desenvolvidos em paralelo no claude.ai): stella_vslam via Docker + correção de aspecto físico + extrator de portas do PDF vetorial. **Precisão: 1.3% de erro vs gabarito, validada em 1 pavimento (P073)**. Saída: panoramas estáticos (fotos por posição, estilo Matterport), não vídeo contínuo.

**O problema**: (A) e (B) são produtos diferentes. (A) toca vídeo contínuo; (B) foi desenhado para servir fotos por clique-na-planta. `processar_vistoria.py` (o "elo" atual entre os dois) usa a odometria leve v4 — não o stella_vslam — e ainda escreve no formato de waypoints de vídeo do app (A). O pipeline de alta precisão (B) nunca foi de fato plugado no produto.

**Decisão tomada com o Pedro:** seguir para (B) — panoramas SLAM — como direção do produto.

## 2. Backend: Obra360 fica independente, consulta o IncorProjetos por API

IncorProjetos (Supabase) é a plataforma de projetos/aprovação de arquivos de engenharia. A ideia mais recente do Pedro — e a que faz mais sentido — é que **Obra360 não precisa migrar para Supabase nem compartilhar banco**: ele só *consulta* o IncorProjetos para saber quais projetos existem e já foram aprovados (e puxar a planta baixa aprovada de lá). Tudo o que é específico de vistoria (vídeo bruto, trajetória, waypoints, panoramas) continua no domínio do Obra360, no backend que for melhor para esse tipo de dado.

Isso é uma fronteira de sistema saudável: acoplamento fraco (uma chamada de API/leitura, não um banco compartilhado), cada sistema evolui sem travar o outro, e dá pra trocar o backend do Obra360 no futuro sem tocar no IncorProjetos. Concretamente:

- IncorProjetos expõe um endpoint (ou view/RPC do Supabase com uma service key restrita) tipo `GET /projetos?status=aprovado` retornando id, nome, e URL da planta baixa aprovada.
- Obra360 chama isso só na hora de criar uma nova vistoria (escolher a qual projeto/pavimento ela pertence) e cacheia local.
- Nenhuma escrita do Obra360 no banco do IncorProjetos, nenhuma leitura direta do Postgres — sempre via API.

## 2.1. Decisão fechada (2026-07-10): mídia pesada → R2, dado calculado → Firebase por enquanto

Vídeo bruto e panoramas gerados (JPEG) vão **todos para o Cloudflare R2**, sem exceção — é pré-requisito da Fase 1 (o `worker.py` não tem onde salvar sem isso), não uma migração futura. Dado calculado (waypoints, portas detectadas, manifest.json) **continua no Firebase/Firestore por enquanto** — é pequeno demais para justificar troca de banco agora ou depois. Pedro também levantou que o IncorProjetos pode adotar R2 para os próprios arquivos de engenharia (20GB/projeto) pelo mesmo motivo de custo de egress — combina com a recomendação da seção 3.

Pendência concreta: **ainda não há credenciais de R2 configuradas no projeto** (`.env`/`.env.local` só têm Firebase + Cloudflare Stream). Adicionei os placeholders em `.env.example`; falta o Pedro criar o bucket e preencher as chaves reais.

## 3. Sobre a pergunta do Supabase em grande volume

Vale separar duas coisas dentro do Supabase: **Postgres** (dados relacionais — projetos, aprovações, usuários) e **Storage** (arquivos). O Postgres não é o problema — aguenta milhões de linhas de metadado sem esforço, isso não é o gargalo em nenhum cenário aqui.

O risco real é usar o **Supabase Storage para os arquivos de 20GB/projeto**. Esse serviço cobra por egress (saída de dados) além de armazenamento — toda vez que um engenheiro abre/baixa um arquivo aprovado, isso é banda paga. Em volume (muitos projetos × 20GB × múltiplos acessos), a conta de egress cresce rápido e vira o item mais caro da fatura, não o armazenamento em si.

Por isso o próprio handoff do SLAM já tinha decidido usar **Cloudflare R2** (zero taxa de egress, ~US$0.015/GB/mês de armazenamento) para os panoramas gerados, em vez de guardar isso no Supabase ou Firebase Storage. **Recomendo estender esse mesmo padrão para o IncorProjetos como um todo**: Postgres do Supabase guarda só metadado + URL/chave do arquivo; o arquivo em si (PDF, DWG, render, vídeo) vai para R2 (ou S3 com CloudFront). Isso deixa o Supabase competitivo em qualquer volume, porque ele nunca carrega o peso pesado dos binários.

Resumindo a resposta direta: Supabase é competitivo, sim — desde que "Supabase" signifique só o Postgres+Auth+Realtime, e o armazenamento de arquivo pesado (dos dois produtos) fique em R2 desde já.

## 4. Roadmap priorizado

### Fase 0 — Higiene do repositório (feito agora)
- `serviceAccountKey.json` (credencial admin do Firebase) estava solta na pasta e **fora do `.gitignore`** — um `git add .` descuidado vazaria a chave. Corrigido agora junto com `*.pem`/`*.key`/`__pycache__`.
- Dívida técnica identificada (não bloqueia, mas vai confundir quem mexer depois): `pdf_extractor.py` (pdfplumber) e `extrair_portas.py` (PyMuPDF/fitz) fazem a mesma coisa — extrair vãos de porta do PDF — com implementações diferentes. `extrair_portas.py` é o validado no handoff (arc-fitting, melhor precisão); `pdf_extractor.py` é o usado pelo pipeline atual do site. Ao migrar para (B), aposentar um dos dois.

### Fase 1 — Fechar o pipeline automático (bloqueador nº 1 para qualquer venda)
Hoje `processar_vistoria.py` roda **manualmente, na máquina do Pedro**, chamado por `.bat`. Isso não escala para um cliente. Sem isso, não existe produto — é o item mais urgente:
1. **`worker.py`** (já no backlog do handoff, nunca escrito): serviço rodando em VPS, consumindo fila, orquestrando stella_vslam (Docker) → `slam_to_obra360.py` → `extrair_portas.py` → `gerar_quadros.py` → upload R2 → grava status.
2. Fila de processamento com status em tempo real (o handoff sugeria tabela Supabase+Realtime; dado que Obra360 fica independente, pode ser uma coleção Firestore com o mesmo papel — não precisa ser Supabase só por causa disso).
3. Trigger automático no upload do vídeo (hoje o upload salva no Firebase e para; ninguém dispara o processamento sozinho).

### Fase 2 — Trocar o viewer de vídeo por viewer de panoramas
Mudança de front relevante, não incremental:
1. Novo componente substituindo `Player360.jsx`: viewer Three.js que troca de textura entre panoramas (preload dos vizinhos + crossfade), lendo o `manifest.json` do R2 — já descrito no backlog do handoff.
2. `Visita.jsx` deixa de girar em torno de `tempoAtual/duracao` (scrubbing contínuo) e passa a girar em torno de "quadro atual" por posição — repensar `useVideoSync.js`.
3. Upload deixa de ir para Cloudflare Stream (HLS) e passa a subir o MP4 bruto direto pro R2 (multipart, como o handoff já definia) — isso também resolve a limitação de "Stream trava em 1080p" citada nas lições aprendidas. `api/get-upload-url.js` precisa virar um endpoint de URL assinada do R2, não do Cloudflare Stream.
4. Regras de ciclo de vida do vídeo bruto (plano básico apaga após validação; premium arquiva) via lifecycle rules do próprio bucket R2.

### Fase 3 — Validar que a precisão generaliza (antes de vender)
O 1.3% foi medido em **um único pavimento** (P073). Antes de prometer isso a um cliente, rodar o pipeline completo em pelo menos 3-5 vídeos/plantas diferentes, incluindo um caso difícil (portas de correr, pouca luz, hall de elevador) — sem isso o número de precisão é uma amostra de tamanho 1.

### Fase 4 — Diferenciais comerciais (o que justifica o preço)
Do backlog do handoff (atualizado 2026-07-10 com a feature de medição), em ordem de impacto por esforço:
1. Selo de qualidade automático (nº de portas atravessadas + resíduo) — vira "aceite automático" que dá confiança ao cliente sem trabalho manual.
2. **Ferramenta de medição nos panoramas** (2 cliques → distância real em metros): implementação inicial em `medir_panorama.py` — raio do clique × landmarks do `mapa.msg` (stella_vslam) → plano local por RANSAC → interseção 3D. **Ainda não validada em campo**: a convenção de eixo do raio equiretangular e a escala SLAM→metros (calibrada medindo uma porta PM de 80cm) precisam ser confirmadas com um mapa real antes de expor no produto. Fallback por curva epipolar (quando faltam landmarks perto do clique) ainda não implementado. Diferencial forte de venda — é o tipo de feature que só existe em Matterport/OpenSpace.
3. Zero-clique: registrar constelação de cruzamentos vs. portas do PDF, eliminando a calibração manual por âncoras que hoje o inspetor faz na mão — maior barreira de uso hoje.
4. Mapa persistente por pavimento (`--map-db-in --disable-mapping`): vistorias repetidas no mesmo andar reaproveitam a calibração, ficando mais rápidas e mais baratas de processar. **Regra operacional nova: rodar o SLAM sempre com `--manter-mapa`** — o `mapa.msg` agora alimenta tanto a medição (item 2) quanto este item, e entra na lista de "nunca excluir" do worker (Fase 1).
5. Comparação semana-a-semana (mesmo ponto físico, panoramas indexados) — feature de acompanhamento de obra, provavelmente o gancho de venda mais forte para incorporadoras.
6. Detector de portas de correr (hoje fora do extrator).
7. **Super-resolução multi-frame sob demanda** (ideia do Pedro, 2026-07-12): em pontos do panorama com baixa nitidez, reprojetar o recorte daquele ponto em todos os frames onde ele aparece (usando as poses já calculadas pela odometria/SLAM — o passo mais caro dessa técnica já sai de graça do pipeline), alinhar sub-pixel e fundir para gerar uma versão em resolução mais alta do que qualquer frame individual — mesmo princípio do "Super Res Zoom" de celular / reconstrução de textura em fotogrametria (não é o mesmo mecanismo do ortomosaico de drone, que cobre áreas diferentes em vez de fundir observações do mesmo ponto). **Não é ganho ilimitado**: depende de diversidade real de ângulo/posição entre os frames daquele ponto (frames de um inspetor parado são quase idênticos, sem ganho) e funciona melhor pra borrão por distância/resolução do que por movimento de câmera; ganho esperado ~2-4x, não "zoom infinito". Rodar sob demanda (clique do usuário num ponto específico), não em todo o panorama — custo computacional não justifica pré-processar tudo.

### Fase 5 — Integração com IncorProjetos
Só depois que a Fase 1-2 estiverem de pé (senão trava o Obra360 numa dependência externa cedo demais): endpoint de leitura de projetos aprovados, conforme seção 2.

## 5. Critério de "pronto pra vender"
Antes de oferecer a clientes externos: pipeline roda sem intervenção manual (Fase 1) + viewer novo no ar (Fase 2) + precisão confirmada em >1 planta (Fase 3) + pelo menos o selo de qualidade automático (Fase 4.1), para o cliente confiar no resultado sem o Pedro validar cada vistoria na mão.
