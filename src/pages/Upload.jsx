// src/pages/Upload.jsx
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { criarVisita } from '../lib/visitas'
import { ref, uploadBytes, getDownloadURL } from 'firebase/storage'
import { storage } from '../lib/firebase'
import * as tus from 'tus-js-client'
import PlantaViewer from '../components/PlantaViewer'

const PAVIMENTOS = [
  'Térreo', 'Mezanino',
  ...Array.from({ length: 20 }, (_, i) => `${i + 1}° Pavimento`),
  'Cobertura', 'Ático',
]

export default function Upload() {
  const navigate = useNavigate()
  const [uploadMethod, setUploadMethod] = useState('direct') // 'direct' | 'manual'
  const [form, setForm] = useState({
    pavimento: '1° Pavimento',
    hls_url: '',
    thumbnail_url: '',
    planta_url: '',
    duracao_segundos: '',
  })
  
  // Estados para Upload do Vídeo
  const [videoFile, setVideoFile] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState('')
  
  // Estado para Upload da Planta Baixa
  const [plantaFile, setPlantaFile] = useState(null)

  // Ancoragem antecipada: define o ponto de partida na planta JA no upload,
  // em vez de deixar pra depois (Visita.jsx) - evita a vistoria ficar "pendente
  // de calibracao" e o risco de uma aba antiga sobrescrever a ancora depois
  // (ver conversa sobre o bug de "ancora saindo do lugar"). O objetivo e' que,
  // quando o cliente abrir os panoramas prontos, a trajetoria ja esteja
  // alinhada - sem etapa manual extra.
  const [plantaPreviewUrl, setPlantaPreviewUrl] = useState(null)
  const [ancora1, setAncora1] = useState(null)
  const [modoCalibrarAncora, setModoCalibrarAncora] = useState(false)

  useEffect(() => {
    let blobUrl = null
    if (plantaFile) {
      if (plantaFile.type === 'application/pdf') {
        const reader = new FileReader()
        reader.onload = (e) => setPlantaPreviewUrl(e.target.result)
        reader.readAsDataURL(plantaFile)
      } else {
        blobUrl = URL.createObjectURL(plantaFile)
        setPlantaPreviewUrl(blobUrl)
      }
    } else if (form.planta_url.trim()) {
      setPlantaPreviewUrl(form.planta_url.trim())
    } else {
      setPlantaPreviewUrl(null)
    }
    return () => { if (blobUrl) URL.revokeObjectURL(blobUrl) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plantaFile, form.planta_url])

  // Estado para Importação de Trajetória JSON
  const [jsonFile, setJsonFile] = useState(null)
  const [waypoints, setWaypoints] = useState([])
  
  const [salvando, setSalvando] = useState(false)
  const [erro, setErro] = useState('')
  const [activeStep, setActiveStep] = useState(1) // 1, 2, 3

  function handleJsonFileChange(e) {
    const file = e.target.files[0]
    if (!file) return

    setJsonFile(file)
    setErro('')

    const reader = new FileReader()
    reader.onload = (event) => {
      try {
        const data = JSON.parse(event.target.result)
        if (Array.isArray(data)) {
          const valid = data.every(pt => typeof pt.t === 'number' && typeof pt.x === 'number' && typeof pt.y === 'number')
          if (!valid) {
            throw new Error('Formato inválido. O JSON deve conter objetos com t, x e y.')
          }
          const pts = data.map(pt => ({
            t: pt.t,
            x: pt.x,
            y: pt.y,
            label: '',
            observacao: ''
          }))
          setWaypoints(pts)
        } else {
          throw new Error('O JSON deve ser um array.')
        }
      } catch (err) {
        setErro('Erro ao ler arquivo de trajetória: ' + err.message)
        setJsonFile(null)
        setWaypoints([])
      }
    }
    reader.readAsText(file)
  }

  function set(k, v) { setForm(prev => ({ ...prev, [k]: v })) }

  // Função auxiliar para subir a imagem da planta para o Firebase Storage
  async function uploadPlanta(file) {
    if (!file) return null
    // Cria referência única na pasta "plantas"
    const fileRef = ref(storage, `plantas/${Date.now()}_${file.name}`)
    await uploadBytes(fileRef, file)
    const downloadURL = await getDownloadURL(fileRef)
    return downloadURL
  }

  // 1. Upload Direto para Cloudflare Stream via Tus + Planta Baixa
  async function iniciarUploadDireto() {
    if (!videoFile) {
      setErro('Selecione um arquivo de vídeo .mp4 para fazer o upload.')
      return
    }
    setUploading(true)
    setErro('')
    setUploadStatus('Solicitando credenciais de upload seguro...')

    try {
      // Requisita URL assinada do Cloudflare a partir do nosso backend serverless
      const response = await fetch('/api/get-upload-url', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          fileSize: videoFile.size,
        }),
      })
      
      if (!response.ok) {
        const errData = await response.json()
        throw new Error(errData.error || 'Erro ao gerar credenciais de upload.')
      }
      
      const { uploadURL, uid, accountId } = await response.json()
      setUploadStatus('Iniciando envio para o Cloudflare Stream...')

      const upload = new tus.Upload(videoFile, {
        endpoint: uploadURL,
        chunkSize: 50 * 1024 * 1024, // 50MB (múltiplo de 256KB exigido pelo Cloudflare)
        retryDelays: [0, 3000, 5000, 10000, 20000],
        onError: function (error) {
          console.error('Erro no upload Tus:', error)
          setErro('Falha no envio do vídeo: ' + error.message)
          setUploading(false)
        },
        onProgress: function (bytesUploaded, bytesTotal) {
          const percentage = ((bytesUploaded / bytesTotal) * 100).toFixed(0)
          setUploadProgress(parseInt(percentage))
          setUploadStatus(`Enviando vídeo: ${percentage}%`)
        },
        onSuccess: async function () {
          setUploadStatus('Vídeo enviado! Enviando imagem da planta baixa...')
          
          let finalPlantaUrl = form.planta_url.trim() || null
          try {
            if (plantaFile) {
              finalPlantaUrl = await uploadPlanta(plantaFile)
            }
          } catch (uploadError) {
            console.error('Erro ao subir planta:', uploadError)
            setErro('Vídeo enviado, mas erro ao salvar imagem da planta baixa: ' + uploadError.message)
            setUploading(false)
            return
          }

          setUploadStatus('Salvando vistoria no Firebase...')
          
          // Formatos de URLs geradas automaticamente pelo Cloudflare Stream
          const hlsUrl = `https://customer-${accountId}.cloudflarestream.com/${uid}/manifest/video.m3u8`
          const thumbnailUrl = `https://customer-${accountId}.cloudflarestream.com/${uid}/thumbnails/thumbnail.jpg`

          try {
            const id = await criarVisita({
              pavimento: form.pavimento,
              hls_url: hlsUrl,
              thumbnail_url: thumbnailUrl,
              planta_url: finalPlantaUrl,
              duracao_segundos: parseInt(form.duracao_segundos) || 0,
              waypoints: waypoints,
              is_imported: waypoints.length > 0,
              ancora1,
            })
            navigate(`/visita/${id}`)
          } catch (e) {
            setErro('Vídeo enviado, mas falha ao salvar visita no Firebase: ' + e.message)
            setUploading(false)
          }
        },
      })

      // Inicia o upload
      upload.start()

    } catch (e) {
      setErro('Erro no processo de upload: ' + e.message)
      setUploading(false)
    }
  }

  // 2. Criação com link manual (e upload opcional de planta)
  async function salvarManual() {
    if (!form.hls_url.trim()) {
      setErro('Informe a URL do arquivo index.m3u8.')
      return
    }
    if (!form.hls_url.endsWith('.m3u8')) {
      setErro('A URL deve terminar em .m3u8')
      return
    }
    setSalvando(true)
    setErro('')

    let finalPlantaUrl = form.planta_url.trim() || null
    try {
      if (plantaFile) {
        finalPlantaUrl = await uploadPlanta(plantaFile)
      }
    } catch (uploadError) {
      setErro('Erro ao subir imagem da planta baixa: ' + uploadError.message)
      setSalvando(false)
      return
    }

    try {
      const id = await criarVisita({
        pavimento: form.pavimento,
        hls_url: form.hls_url.trim(),
        thumbnail_url: form.thumbnail_url.trim() || null,
        planta_url: finalPlantaUrl,
        duracao_segundos: parseInt(form.duracao_segundos) || 0,
        waypoints: waypoints,
        is_imported: waypoints.length > 0,
        ancora1,
      })
      navigate(`/visita/${id}`)
    } catch (e) {
      setErro('Erro ao salvar: ' + e.message)
      setSalvando(false)
    }
  }

  return (
    <div className="min-h-screen bg-concreto-950 flex flex-col text-aco-200">
      
      {/* Header */}
      <header className="border-b border-concreto-700 bg-concreto-900 shadow-md">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center gap-4">
          <button
            onClick={() => navigate('/')}
            className="font-mono text-xs text-aco-400 hover:text-aco-200 px-3 py-1.5 rounded bg-concreto-800/50 transition-colors"
          >
            ← Voltar
          </button>
          <h1 className="text-base font-semibold text-aco-100">Nova Vistoria 360°</h1>
        </div>
      </header>

      {/* Main Grid */}
      <main className="flex-1 max-w-4xl mx-auto w-full px-6 py-8 grid grid-cols-1 md:grid-cols-12 gap-8 items-start">
        
        {/* Left Form: 7 cols */}
        <div className="md:col-span-7 space-y-5 bg-concreto-900 border border-concreto-700/60 p-6 rounded-xl shadow-lg">
          <div className="flex items-center justify-between border-b border-concreto-800 pb-2">
            <h2 className="text-sm font-semibold text-aco-100">Configurações da Vistoria</h2>
            
            {/* Seletor de método de upload */}
            <div className="flex border border-concreto-700 rounded bg-concreto-950/40 p-0.5 text-[9px] font-mono">
              <button
                onClick={() => !uploading && !salvando && setUploadMethod('direct')}
                className={`px-2 py-1 rounded transition-all ${
                  uploadMethod === 'direct' ? 'bg-sinal-500 text-concreto-950 font-bold' : 'text-aco-400 hover:text-aco-200'
                }`}
                disabled={uploading || salvando}
              >
                Upload MP4
              </button>
              <button
                onClick={() => !uploading && !salvando && setUploadMethod('manual')}
                className={`px-2 py-1 rounded transition-all ${
                  uploadMethod === 'manual' ? 'bg-sinal-500 text-concreto-950 font-bold' : 'text-aco-400 hover:text-aco-200'
                }`}
                disabled={uploading || salvando}
              >
                Link Manual
              </button>
            </div>
          </div>

          <Field label="Pavimento">
            <select
              value={form.pavimento}
              onChange={e => set('pavimento', e.target.value)}
              disabled={uploading || salvando}
              className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 focus:outline-none focus:border-sinal-500 transition-colors disabled:opacity-55"
            >
              {PAVIMENTOS.map(p => <option key={p}>{p}</option>)}
            </select>
          </Field>

          {/* Renderização Condicional de Entrada do Vídeo */}
          {uploadMethod === 'direct' ? (
            <Field label="Arquivo de Vídeo MP4 (360°)" required hint="Selecione o arquivo equiretangular da Insta360 X3">
              {uploading ? (
                <div className="bg-concreto-800/40 border border-concreto-750 rounded-lg p-5 flex flex-col items-center gap-3">
                  <div className="w-full bg-concreto-950 rounded-full h-2 overflow-hidden border border-concreto-800">
                    <div
                      className="bg-sinal-500 h-full rounded-full transition-all duration-300 shadow-sm"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                  <p className="text-[10px] font-mono text-sinal-400 animate-pulse">{uploadStatus}</p>
                </div>
              ) : (
                <div className="relative border-2 border-dashed border-concreto-700/80 hover:border-sinal-500/50 rounded-lg p-6 bg-concreto-800/20 text-center transition-all cursor-pointer">
                  <input
                    type="file"
                    accept="video/mp4"
                    onChange={e => {
                      const file = e.target.files[0]
                      if (file) {
                        setVideoFile(file)
                        setErro('')
                      }
                    }}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  />
                  <div className="flex flex-col items-center gap-2">
                    <span className="text-2xl">📹</span>
                    <p className="text-xs text-aco-200 font-semibold">
                      {videoFile ? videoFile.name : 'Clique ou arraste o arquivo MP4 aqui'}
                    </p>
                    {videoFile && (
                      <p className="text-[10px] text-aco-400 font-mono">
                        ({(videoFile.size / (1024 * 1024)).toFixed(1)} MB)
                      </p>
                    )}
                  </div>
                </div>
              )}
            </Field>
          ) : (
            <>
              <Field label="URL do HLS (index.m3u8)" required hint="Endereço do manifesto HLS ex: no Cloudflare R2">
                <input
                  value={form.hls_url}
                  onChange={e => set('hls_url', e.target.value)}
                  placeholder="https://pub-xxx.r2.dev/visitas/terreo/index.m3u8"
                  className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 font-mono focus:outline-none focus:border-sinal-500 transition-colors"
                />
              </Field>

              <Field label="URL de thumbnail (opcional)" hint="Imagem de capa do card">
                <input
                  value={form.thumbnail_url}
                  onChange={e => set('thumbnail_url', e.target.value)}
                  placeholder="https://pub-xxx.r2.dev/visitas/terreo/thumb.jpg"
                  className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
                />
              </Field>
            </>
          )}

          {/* Planta Baixa: Upload ou link manual */}
          <Field label="Planta Baixa (PDF ou Imagem)" hint="Selecione a planta baixa (PDF vetorizado ou imagem PNG/JPG) para mapear os waypoints">
            {plantaFile ? (
              <div className="flex items-center justify-between bg-concreto-800 border border-concreto-700 p-2.5 rounded-lg text-xs">
                <span className="font-mono text-aco-200 truncate">{plantaFile.name}</span>
                <button
                  type="button"
                  onClick={() => setPlantaFile(null)}
                  className="text-alerta text-xs hover:underline font-mono bg-transparent border-0 cursor-pointer"
                >
                  Remover
                </button>
              </div>
            ) : (
              <div className="relative border border-dashed border-concreto-700 hover:border-sinal-500/50 rounded-lg p-3 bg-concreto-800/10 text-center cursor-pointer transition-all">
                <input
                  type="file"
                  accept="application/pdf, image/png, image/jpeg, image/jpg"
                  onChange={e => {
                    const file = e.target.files[0]
                    if (file) setPlantaFile(file)
                  }}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                />
                <span className="text-xs text-aco-300">Escolher arquivo PDF ou imagem da planta baixa</span>
              </div>
            )}
            
            {!plantaFile && (
              <input
                value={form.planta_url}
                onChange={e => set('planta_url', e.target.value)}
                disabled={uploading || salvando}
                placeholder="Ou cole uma URL ex: https://pub-xxx.r2.dev/planta.pdf"
                className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2 text-xs text-aco-200 mt-2 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors disabled:opacity-55"
              />
            )}
          </Field>

          {/* Ancoragem antecipada: marca o ponto de partida na planta ja no upload */}
          {plantaPreviewUrl && (
            <Field
              label="Ponto de Partida na Planta (opcional)"
              hint="Clique em 'Marcar ponto' e depois clique na planta, no local exato de onde você começou a gravação. Se pular, dá pra calibrar depois na tela da vistoria."
            >
              <div className="h-56 rounded-lg overflow-hidden border border-concreto-700 mb-2">
                <PlantaViewer
                  plantaUrl={plantaPreviewUrl}
                  waypoints={[]}
                  ancora1={ancora1}
                  modoCalibrarAncoras={modoCalibrarAncora ? 'ancora1' : null}
                  onClickCoordenada={(x, y) => {
                    setAncora1({ x, y })
                    setModoCalibrarAncora(false)
                  }}
                />
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setModoCalibrarAncora(v => !v)}
                  className={`flex-1 text-xs font-mono py-2 rounded-lg border transition-colors ${
                    modoCalibrarAncora
                      ? 'bg-sinal-500 text-concreto-950 border-sinal-500 font-bold'
                      : ancora1
                        ? 'bg-concreto-800 border-sinal-600/50 text-sinal-400'
                        : 'bg-concreto-800 border-concreto-700 text-aco-300 hover:border-sinal-500/50'
                  }`}
                >
                  {modoCalibrarAncora ? 'Clique na planta...' : ancora1 ? '✓ Ponto definido (clique pra ajustar)' : 'Marcar ponto de partida'}
                </button>
                {ancora1 && (
                  <button
                    type="button"
                    onClick={() => { setAncora1(null); setModoCalibrarAncora(false) }}
                    className="text-alerta text-xs font-mono px-3 py-2 rounded-lg border border-concreto-700 hover:border-alerta/50"
                  >
                    Limpar
                  </button>
                )}
              </div>
            </Field>
          )}

          {/* Trajetória Automática JSON (Gerada via Python) */}
          <Field label="Importar Trajetória (.json do Python) - Opcional" hint="Carregue o arquivo de trajetória calculado localmente">
            {jsonFile ? (
              <div className="flex items-center justify-between bg-concreto-800 border border-concreto-700 p-2.5 rounded-lg text-xs">
                <span className="font-mono text-aco-200 truncate">{jsonFile.name}</span>
                <button
                  type="button"
                  onClick={() => {
                    setJsonFile(null)
                    setWaypoints([])
                  }}
                  className="text-alerta text-xs hover:underline font-mono bg-transparent border-0 cursor-pointer"
                >
                  Remover
                </button>
              </div>
            ) : (
              <div className="relative border border-dashed border-concreto-700 hover:border-sinal-500/50 rounded-lg p-3 bg-concreto-800/10 text-center cursor-pointer transition-all">
                <input
                  type="file"
                  accept=".json"
                  onChange={handleJsonFileChange}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                />
                <span className="text-xs text-aco-300">Selecionar arquivo caminho_vistoria.json</span>
              </div>
            )}
          </Field>

          <Field label="Duração do vídeo (segundos)" hint="Opcional">
            <input
              type="number"
              value={form.duracao_segundos}
              onChange={e => set('duracao_segundos', e.target.value)}
              disabled={uploading || salvando}
              placeholder="ex: 180"
              className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors disabled:opacity-55"
            />
          </Field>

          {erro && <p className="text-alerta text-xs font-mono">{erro}</p>}

          <button
            onClick={uploadMethod === 'direct' ? iniciarUploadDireto : salvarManual}
            disabled={uploading || salvando}
            className="w-full bg-sinal-500 hover:bg-sinal-400 active:scale-[0.99] disabled:opacity-50 text-concreto-950 font-semibold text-xs py-3 rounded-lg transition-all shadow-md shadow-sinal-500/10"
          >
            {uploading ? 'Enviando Vistoria...' : salvando ? 'Salvando Vistoria...' : 'Criar Vistoria e Iniciar Mapeamento'}
          </button>
        </div>

        {/* Right Instructions: 5 cols */}
        <div className="md:col-span-5 space-y-4">
          <div className="bg-concreto-900 border border-concreto-700/60 p-5 rounded-xl shadow-lg space-y-4">
            <h2 className="text-xs font-semibold text-aco-200 uppercase tracking-wider font-mono">Guia de Preparação</h2>
            <p className="text-[11px] text-aco-400 leading-relaxed">
              O upload automático processa tudo de forma integrada. Veja abaixo as etapas:
            </p>

            {/* Interactive Steps */}
            <div className="space-y-3 font-mono text-[11px]">
              
              {/* Step 1 */}
              <div className="border border-concreto-700/60 rounded-lg overflow-hidden">
                <button
                  onClick={() => setActiveStep(1)}
                  className={`w-full text-left px-3 py-2 flex items-center justify-between transition-colors ${
                    activeStep === 1 ? 'bg-concreto-800 text-sinal-400' : 'bg-concreto-900 text-aco-300 hover:bg-concreto-800/40'
                  }`}
                >
                  <span>1. Exportar da Insta360</span>
                  <span>{activeStep === 1 ? '▼' : '►'}</span>
                </button>
                {activeStep === 1 && (
                  <div className="p-3 bg-concreto-950/40 text-aco-400 leading-relaxed border-t border-concreto-800">
                    Abra o arquivo `.insv` no **Insta360 Studio**. Exporte em **MP4 Equiretangular** na resolução **4K** ou **1080p**. Ative a estabilização FlowState para obter uma caminhada suave.
                  </div>
                )}
              </div>

              {/* Step 2 */}
              <div className="border border-concreto-700/60 rounded-lg overflow-hidden">
                <button
                  onClick={() => setActiveStep(2)}
                  className={`w-full text-left px-3 py-2 flex items-center justify-between transition-colors ${
                    activeStep === 2 ? 'bg-concreto-800 text-sinal-400' : 'bg-concreto-900 text-aco-300 hover:bg-concreto-800/40'
                  }`}
                >
                  <span>2. Como funciona o upload</span>
                  <span>{activeStep === 2 ? '▼' : '►'}</span>
                </button>
                {activeStep === 2 && (
                  <div className="p-3 bg-concreto-950/40 text-aco-400 space-y-2 border-t border-concreto-800 leading-relaxed">
                    <p>O site solicita uma credencial temporária para o backend (sem expor suas chaves de segurança) e envia o MP4 em blocos estáveis direto para o Cloudflare Stream.</p>
                  </div>
                )}
              </div>

              {/* Step 3 */}
              <div className="border border-concreto-700/60 rounded-lg overflow-hidden">
                <button
                  onClick={() => setActiveStep(3)}
                  className={`w-full text-left px-3 py-2 flex items-center justify-between transition-colors ${
                    activeStep === 3 ? 'bg-concreto-800 text-sinal-400' : 'bg-concreto-900 text-aco-300 hover:bg-concreto-800/40'
                  }`}
                >
                  <span>3. Armazenamento da Planta</span>
                  <span>{activeStep === 3 ? '▼' : '►'}</span>
                </button>
                {activeStep === 3 && (
                  <div className="p-3 bg-concreto-950/40 text-aco-400 space-y-2 border-t border-concreto-800 leading-relaxed">
                    <p>Ao selecionar uma imagem local para a Planta Baixa, ela é enviada com segurança e de forma automática para o seu **Firebase Storage**, permitindo a marcação dos caminhos sem depender de links externos.</p>
                  </div>
                )}
              </div>

            </div>
          </div>
        </div>

      </main>
    </div>
  )
}

function Field({ label, hint, required, children }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-xs text-aco-300 font-semibold">
        {label}{required && <span className="text-sinal-500 ml-1">*</span>}
      </label>
      {hint && <p className="text-[10px] text-aco-400 font-mono">{hint}</p>}
      {children}
    </div>
  )
}
