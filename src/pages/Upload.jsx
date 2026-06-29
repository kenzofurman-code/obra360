// src/pages/Upload.jsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { criarVisita } from '../lib/visitas'

const PAVIMENTOS = [
  'Térreo', 'Mezanino',
  ...Array.from({ length: 20 }, (_, i) => `${i + 1}° Pavimento`),
  'Cobertura', 'Ático',
]

export default function Upload() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    pavimento: '1° Pavimento',
    hls_url: '',
    thumbnail_url: '',
    planta_url: '',
    duracao_segundos: '',
  })
  const [salvando, setSalvando] = useState(false)
  const [erro, setErro] = useState('')
  const [activeStep, setActiveStep] = useState(1) // 1, 2, 3

  function set(k, v) { setForm(prev => ({ ...prev, [k]: v })) }

  async function salvar() {
    if (!form.hls_url.trim()) {
      setErro('Informe a URL do arquivo index.m3u8 no Cloudflare R2.')
      return
    }
    if (!form.hls_url.endsWith('.m3u8')) {
      setErro('A URL deve terminar em .m3u8')
      return
    }
    setSalvando(true)
    setErro('')
    try {
      const id = await criarVisita({
        pavimento: form.pavimento,
        hls_url: form.hls_url.trim(),
        thumbnail_url: form.thumbnail_url.trim() || null,
        planta_url: form.planta_url.trim() || null,
        duracao_segundos: parseInt(form.duracao_segundos) || 0,
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

      {/* Main Grid: Form on the Left, Setup Guide on the Right */}
      <main className="flex-1 max-w-4xl mx-auto w-full px-6 py-8 grid grid-cols-1 md:grid-cols-12 gap-8 items-start">
        
        {/* Left Form: 7 cols */}
        <div className="md:col-span-7 space-y-5 bg-concreto-900 border border-concreto-700/60 p-6 rounded-xl shadow-lg">
          <h2 className="text-sm font-semibold text-aco-100 border-b border-concreto-800 pb-2">Informações Básicas</h2>

          <Field label="Pavimento">
            <select
              value={form.pavimento}
              onChange={e => set('pavimento', e.target.value)}
              className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 focus:outline-none focus:border-sinal-500 transition-colors"
            >
              {PAVIMENTOS.map(p => <option key={p}>{p}</option>)}
            </select>
          </Field>

          <Field label="URL do HLS (index.m3u8 no Cloudflare R2)" required hint="Cole a URL pública do index.m3u8">
            <input
              value={form.hls_url}
              onChange={e => set('hls_url', e.target.value)}
              placeholder="https://pub-xxx.r2.dev/visitas/terreo/index.m3u8"
              className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 font-mono focus:outline-none focus:border-sinal-500 transition-colors"
            />
          </Field>

          <Field label="URL da planta PNG" hint="Upload da imagem da planta baixa (opcional)">
            <input
              value={form.planta_url}
              onChange={e => set('planta_url', e.target.value)}
              placeholder="https://pub-xxx.r2.dev/plantas/terreo.png"
              className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
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

          <Field label="Duração do vídeo (segundos)" hint="Opcional">
            <input
              type="number"
              value={form.duracao_segundos}
              onChange={e => set('duracao_segundos', e.target.value)}
              placeholder="ex: 180"
              className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
            />
          </Field>

          {erro && <p className="text-alerta text-xs font-mono">{erro}</p>}

          <button
            onClick={salvar}
            disabled={salvando}
            className="w-full bg-sinal-500 hover:bg-sinal-400 active:scale-[0.99] disabled:opacity-50 text-concreto-950 font-semibold text-xs py-3 rounded-lg transition-all shadow-md shadow-sinal-500/10"
          >
            {salvando ? 'Salvando...' : 'Criar Vistoria e Mapear'}
          </button>
        </div>

        {/* Right Instructions: 5 cols */}
        <div className="md:col-span-5 space-y-4">
          <div className="bg-concreto-900 border border-concreto-700/60 p-5 rounded-xl shadow-lg space-y-4">
            <h2 className="text-xs font-semibold text-aco-200 uppercase tracking-wider font-mono">Preparar Vídeo 360°</h2>
            <p className="text-[11px] text-aco-400 leading-relaxed leading-normal">
              Para reproduzir o vídeo de forma fluida e sem travamentos, siga estes 3 passos básicos:
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
                    Abra o arquivo `.insv` no **Insta360 Studio** no PC. Ative a estabilização FlowState e exporte em **MP4 Equiretangular** na resolução **4K (3840x1920)** ou inferior para garantir fluidez na web.
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
                  <span>2. Fragmentar em HLS (FFmpeg)</span>
                  <span>{activeStep === 2 ? '▼' : '►'}</span>
                </button>
                {activeStep === 2 && (
                  <div className="p-3 bg-concreto-950/40 text-aco-400 space-y-2 border-t border-concreto-800">
                    <p>Converta o arquivo para HLS (cria a playlist m3u8 e segmentos ts):</p>
                    <code className="block bg-concreto-950 p-2 rounded text-[10px] text-green-400 leading-normal overflow-x-auto whitespace-pre">
                      ffmpeg -i video.mp4 -codec: copy -hls_time 4 -hls_list_size 0 -f hls index.m3u8
                    </code>
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
                  <span>3. Hospedar no Cloudflare R2</span>
                  <span>{activeStep === 3 ? '▼' : '►'}</span>
                </button>
                {activeStep === 3 && (
                  <div className="p-3 bg-concreto-950/40 text-aco-400 space-y-2 border-t border-concreto-800 leading-relaxed">
                    <p>1. Crie um bucket no **R2** e ative **Public Access**.</p>
                    <p>2. Configure a regra **CORS** nas configurações do bucket para liberar as requisições WebGL.</p>
                    <p>3. Faça upload de toda a pasta (index.m3u8 + todos os arquivos .ts).</p>
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
