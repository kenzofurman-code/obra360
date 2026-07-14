// src/pages/Obras.jsx
// Seletor de projeto (nível mais alto do site) - substitui Home.jsx como rota
// "/". Grid de obras (empreendimentos), cada uma levando pra sua aba de
// locais (Locais.jsx). Layout de card inspirado no app de "Planejamento de
// Curto Prazo" que o Pedro referenciou (thumbnail, área, contagem de locais,
// endereço, botão "Selecionar"), adaptado ao tema escuro já usado no Obra360.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listarObras, criarObra } from '../lib/obras'
import { listarLocaisDaObra } from '../lib/locais'
import { uploadArquivo } from '../lib/storage'

export default function Obras() {
  const navigate = useNavigate()
  const [obras, setObras] = useState([])
  const [loading, setLoading] = useState(true)
  const [busca, setBusca] = useState('')
  const [criando, setCriando] = useState(false)

  async function carregar() {
    setLoading(true)
    const lista = await listarObras()
    // Contagem de locais por obra - N+1, mas o volume de obras/locais nesse
    // estágio do produto é pequeno o bastante pra não valer a pena denormalizar
    // um contador ainda (reavaliar se a lista crescer muito).
    const comContagem = await Promise.all(
      lista.map(async o => {
        const locais = await listarLocaisDaObra(o.id)
        return { ...o, n_locais: locais.length }
      })
    )
    setObras(comContagem)
    setLoading(false)
  }

  useEffect(() => { carregar() }, [])

  const filtradas = obras.filter(o =>
    !busca.trim() || o.nome?.toLowerCase().includes(busca.trim().toLowerCase())
  )

  return (
    <div className="min-h-screen bg-concreto-950 flex flex-col text-aco-200">
      {/* Header */}
      <header className="border-b border-concreto-700/60 bg-concreto-900/60 backdrop-blur-md sticky top-0 z-10 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
          <div>
            <p className="font-mono text-[10px] text-sinal-500 uppercase tracking-widest font-semibold">Gestão de Obras</p>
            <h1 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-aco-100 to-aco-300 font-sans">
              Meus Projetos
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/visita/demo')}
              className="bg-concreto-800 hover:bg-concreto-700 active:scale-[0.98] text-aco-200 border border-concreto-600/50 font-semibold text-xs px-4 py-2.5 rounded transition-all shadow-sm"
            >
              🧪 Modo Demo
            </button>
            <input
              value={busca}
              onChange={e => setBusca(e.target.value)}
              placeholder="Buscar projeto..."
              className="bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors w-48"
            />
            <button
              onClick={() => setCriando(v => !v)}
              className="bg-sinal-500 hover:bg-sinal-400 active:scale-[0.98] text-concreto-950 font-semibold text-xs px-4 py-2.5 rounded transition-all shadow-md shadow-sinal-500/10"
            >
              {criando ? 'Cancelar' : '+ Nova Obra'}
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-8">
        {criando && (
          <NovaObraForm
            onCriada={() => { setCriando(false); carregar() }}
            onCancelar={() => setCriando(false)}
          />
        )}

        {loading && (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <div className="w-6 h-6 border-2 border-aco-400 border-t-transparent rounded-full animate-spin" />
            <span className="font-mono text-xs text-aco-400">Buscando projetos...</span>
          </div>
        )}

        {!loading && filtradas.length === 0 && (
          <div className="text-center py-24 border border-dashed border-concreto-700/50 rounded-xl bg-concreto-900/10">
            <p className="text-aco-400 text-sm font-mono mb-4">
              {busca ? 'Nenhum projeto encontrado.' : 'Nenhuma obra cadastrada ainda.'}
            </p>
            {!busca && (
              <button
                onClick={() => setCriando(true)}
                className="bg-sinal-500 hover:bg-sinal-400 text-concreto-950 text-xs px-6 py-2.5 rounded font-semibold transition-all shadow"
              >
                Cadastrar Primeira Obra
              </button>
            )}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
          {filtradas.map(o => (
            <div
              key={o.id}
              onClick={() => navigate(`/obra/${o.id}`)}
              className="group bg-concreto-900/60 border border-concreto-700/60 hover:border-sinal-500/40 rounded-xl overflow-hidden cursor-pointer shadow-md hover:shadow-2xl hover:shadow-sinal-500/5 transition-all duration-300 hover:-translate-y-1 flex flex-col"
            >
              <div className="relative w-full aspect-[16/10] bg-concreto-950 overflow-hidden border-b border-concreto-850 flex items-center justify-center">
                {o.thumbnail_url ? (
                  <img
                    src={o.thumbnail_url}
                    alt={o.nome}
                    className="w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-500"
                    onError={e => { e.target.style.display = 'none' }}
                  />
                ) : (
                  <div className="flex flex-col items-center gap-1.5 opacity-60">
                    <span className="text-[20px]">🏗️</span>
                    <span className="text-aco-400 text-[10px] font-mono">sem imagem</span>
                  </div>
                )}
              </div>

              <div className="p-4 flex flex-col gap-2 flex-1">
                <p className="font-semibold text-aco-100 text-sm group-hover:text-sinal-400 transition-colors">
                  {o.nome}
                </p>

                <div className="flex flex-col gap-1 font-mono text-[10px] text-aco-400">
                  {o.area_m2 != null && (
                    <span className="flex items-center gap-1.5">📐 {Number(o.area_m2).toLocaleString('pt-BR')} m²</span>
                  )}
                  <span className="flex items-center gap-1.5">📍 {o.n_locais} local(is) cadastrado(s)</span>
                  {o.endereco && (
                    <span className="flex items-center gap-1.5 text-aco-500 leading-snug">🏠 {o.endereco}</span>
                  )}
                </div>

                <button
                  onClick={e => { e.stopPropagation(); navigate(`/obra/${o.id}`) }}
                  className="mt-2 w-full bg-concreto-800 hover:bg-sinal-500 hover:text-concreto-950 text-aco-200 text-xs font-semibold py-2 rounded-lg border border-concreto-700 hover:border-sinal-500 transition-all"
                >
                  Selecionar
                </button>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}

function NovaObraForm({ onCriada, onCancelar }) {
  const [nome, setNome] = useState('')
  const [endereco, setEndereco] = useState('')
  const [areaM2, setAreaM2] = useState('')
  const [thumbFile, setThumbFile] = useState(null)
  const [salvando, setSalvando] = useState(false)
  const [erro, setErro] = useState('')

  async function salvar() {
    if (!nome.trim()) { setErro('Informe o nome da obra.'); return }
    setSalvando(true)
    setErro('')
    try {
      let thumbnail_url = null
      if (thumbFile) thumbnail_url = await uploadArquivo(thumbFile, 'obras_thumbnails')
      await criarObra({
        nome: nome.trim(),
        endereco: endereco.trim(),
        area_m2: areaM2 ? parseFloat(areaM2) : null,
        thumbnail_url,
      })
      onCriada()
    } catch (e) {
      setErro('Erro ao criar obra: ' + e.message)
      setSalvando(false)
    }
  }

  return (
    <div className="mb-6 bg-concreto-900 border border-concreto-700/60 p-5 rounded-xl shadow-lg space-y-4">
      <h2 className="text-sm font-semibold text-aco-100">Nova Obra</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <label className="block text-xs text-aco-300 font-semibold">Nome da Obra <span className="text-sinal-500">*</span></label>
          <input
            value={nome}
            onChange={e => setNome(e.target.value)}
            placeholder="ex: Residencial Pace"
            className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
          />
        </div>
        <div className="space-y-1.5">
          <label className="block text-xs text-aco-300 font-semibold">Endereço</label>
          <input
            value={endereco}
            onChange={e => setEndereco(e.target.value)}
            placeholder="ex: Rua Exemplo, 123 - Curitiba, PR"
            className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
          />
        </div>
        <div className="space-y-1.5">
          <label className="block text-xs text-aco-300 font-semibold">Área Total (m²)</label>
          <input
            type="number"
            value={areaM2}
            onChange={e => setAreaM2(e.target.value)}
            placeholder="ex: 12500"
            className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
          />
        </div>
        <div className="space-y-1.5">
          <label className="block text-xs text-aco-300 font-semibold">Imagem de Capa (opcional)</label>
          <input
            type="file"
            accept="image/png, image/jpeg, image/jpg"
            onChange={e => setThumbFile(e.target.files[0] || null)}
            className="w-full text-xs text-aco-300 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:bg-concreto-800 file:text-aco-200 file:text-xs"
          />
        </div>
      </div>

      {erro && <p className="text-alerta text-xs font-mono">{erro}</p>}

      <div className="flex items-center gap-3">
        <button
          onClick={salvar}
          disabled={salvando}
          className="bg-sinal-500 hover:bg-sinal-400 disabled:opacity-50 text-concreto-950 font-semibold text-xs px-5 py-2.5 rounded-lg transition-all"
        >
          {salvando ? 'Criando...' : 'Criar Obra'}
        </button>
        <button
          onClick={onCancelar}
          disabled={salvando}
          className="text-aco-400 hover:text-aco-200 text-xs font-mono"
        >
          Cancelar
        </button>
      </div>
    </div>
  )
}
