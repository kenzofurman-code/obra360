// src/pages/Locais.jsx
// Aba de controle dos locais de uma obra (pavimentos, apartamentos, áreas
// comuns etc.). Cada local acumula várias vistorias ao longo do tempo (ver
// visitas.js::listarVisitasDoLocal) - a base do histórico/comparação do item
// 4.5. Tela simples pedida pelo Pedro: nome do local + planta já cadastrada
// ou upload dela na hora.
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getObra } from '../lib/obras'
import { listarLocaisDaObra, criarLocal, atualizarLocal } from '../lib/locais'
import { listarVisitasDoLocal } from '../lib/visitas'
import { uploadArquivo } from '../lib/storage'

export default function Locais() {
  const { obraId } = useParams()
  const navigate = useNavigate()
  const [obra, setObra] = useState(null)
  const [locais, setLocais] = useState([])
  const [loading, setLoading] = useState(true)
  const [criando, setCriando] = useState(false)

  async function carregar() {
    setLoading(true)
    const [o, lista] = await Promise.all([getObra(obraId), listarLocaisDaObra(obraId)])
    setObra(o)
    const comVisitas = await Promise.all(
      lista.map(async l => {
        const visitas = await listarVisitasDoLocal(l.id)
        return { ...l, n_visitas: visitas.length, ultima_visita_id: visitas[0]?.id || null }
      })
    )
    setLocais(comVisitas)
    setLoading(false)
  }

  useEffect(() => { carregar() }, [obraId])

  function abrirLocal(l) {
    if (l.ultima_visita_id) {
      navigate(`/visita/${l.ultima_visita_id}`)
    } else if (l.planta_url) {
      navigate(`/obra/${obraId}/local/${l.id}/upload`)
    }
    // sem planta ainda: nao navega - o proprio card ja mostra o upload inline
  }

  return (
    <div className="min-h-screen bg-concreto-950 flex flex-col text-aco-200">
      <header className="border-b border-concreto-700/60 bg-concreto-900/60 backdrop-blur-md sticky top-0 z-10 shadow-sm">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/')}
              className="font-mono text-xs text-aco-400 hover:text-aco-200 px-3 py-1.5 rounded bg-concreto-800/50 transition-colors"
            >
              ← Projetos
            </button>
            <div>
              <p className="font-mono text-[10px] text-sinal-500 uppercase tracking-widest font-semibold">Locais da Obra</p>
              <h1 className="text-lg font-bold text-aco-100">{obra?.nome || '...'}</h1>
            </div>
          </div>
          <button
            onClick={() => setCriando(v => !v)}
            className="bg-sinal-500 hover:bg-sinal-400 active:scale-[0.98] text-concreto-950 font-semibold text-xs px-4 py-2.5 rounded transition-all shadow-md shadow-sinal-500/10"
          >
            {criando ? 'Cancelar' : '+ Novo Local'}
          </button>
        </div>
      </header>

      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-8">
        {criando && (
          <NovoLocalForm
            obraId={obraId}
            onCriado={() => { setCriando(false); carregar() }}
            onCancelar={() => setCriando(false)}
          />
        )}

        {loading && (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <div className="w-6 h-6 border-2 border-aco-400 border-t-transparent rounded-full animate-spin" />
            <span className="font-mono text-xs text-aco-400">Carregando locais...</span>
          </div>
        )}

        {!loading && locais.length === 0 && (
          <div className="text-center py-24 border border-dashed border-concreto-700/50 rounded-xl bg-concreto-900/10">
            <p className="text-aco-400 text-sm font-mono mb-4">Nenhum local cadastrado nesta obra ainda.</p>
            <button
              onClick={() => setCriando(true)}
              className="bg-sinal-500 hover:bg-sinal-400 text-concreto-950 text-xs px-6 py-2.5 rounded font-semibold transition-all shadow"
            >
              Cadastrar Primeiro Local
            </button>
          </div>
        )}

        <div className="flex flex-col gap-3">
          {locais.map(l => (
            <LocalRow
              key={l.id}
              local={l}
              obraId={obraId}
              onAbrir={() => abrirLocal(l)}
              onNovaVistoria={() => navigate(`/obra/${obraId}/local/${l.id}/upload`)}
              onPlantaEnviada={carregar}
            />
          ))}
        </div>
      </main>
    </div>
  )
}

function LocalRow({ local, obraId, onAbrir, onNovaVistoria, onPlantaEnviada }) {
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState('')

  async function enviarPlanta(file) {
    if (!file) return
    setEnviando(true)
    setErro('')
    try {
      const planta_url = await uploadArquivo(file, 'plantas')
      await atualizarLocal(local.id, { planta_url })
      onPlantaEnviada()
    } catch (e) {
      setErro('Erro ao enviar planta: ' + e.message)
      setEnviando(false)
    }
  }

  return (
    <div className="bg-concreto-900/60 border border-concreto-700/60 rounded-xl p-4 flex items-center justify-between gap-4 flex-wrap">
      <div
        onClick={local.planta_url ? onAbrir : undefined}
        className={`flex-1 min-w-[180px] ${local.planta_url ? 'cursor-pointer group' : ''}`}
      >
        <p className={`font-semibold text-sm text-aco-100 ${local.planta_url ? 'group-hover:text-sinal-400' : ''} transition-colors`}>
          {local.nome}
        </p>
        <p className="font-mono text-[10px] text-aco-400 mt-1">
          {local.n_visitas > 0
            ? `${local.n_visitas} vistoria(s) registrada(s)`
            : 'nenhuma vistoria ainda'}
        </p>
      </div>

      <div className="flex items-center gap-3">
        {local.planta_url ? (
          <span className="text-[10px] font-mono px-2.5 py-1 rounded-full border bg-ok/10 border-ok/30 text-ok">
            ✓ Planta cadastrada
          </span>
        ) : (
          <label className={`text-[10px] font-mono px-3 py-1.5 rounded-lg border cursor-pointer transition-colors ${
            enviando
              ? 'bg-concreto-800 border-concreto-700 text-aco-400'
              : 'bg-alerta/10 border-alerta/30 text-alerta hover:bg-alerta/20'
          }`}>
            {enviando ? 'Enviando...' : '⬆ Enviar planta baixa'}
            <input
              type="file"
              accept="application/pdf, image/png, image/jpeg, image/jpg"
              className="hidden"
              disabled={enviando}
              onChange={e => enviarPlanta(e.target.files[0])}
            />
          </label>
        )}

        <button
          onClick={onNovaVistoria}
          disabled={!local.planta_url}
          title={!local.planta_url ? 'Envie a planta baixa deste local primeiro' : ''}
          className="bg-concreto-800 hover:bg-sinal-500 hover:text-concreto-950 disabled:opacity-40 disabled:hover:bg-concreto-800 disabled:hover:text-aco-200 text-aco-200 text-xs font-semibold px-3.5 py-2 rounded-lg border border-concreto-700 hover:border-sinal-500 transition-all"
        >
          + Nova Vistoria
        </button>
      </div>

      {erro && <p className="w-full text-alerta text-[10px] font-mono">{erro}</p>}
    </div>
  )
}

function NovoLocalForm({ obraId, onCriado, onCancelar }) {
  const [nome, setNome] = useState('')
  const [plantaUrl, setPlantaUrl] = useState('')
  const [plantaFile, setPlantaFile] = useState(null)
  const [salvando, setSalvando] = useState(false)
  const [erro, setErro] = useState('')

  async function salvar() {
    if (!nome.trim()) { setErro('Informe o nome do local.'); return }
    setSalvando(true)
    setErro('')
    try {
      let planta_url = plantaUrl.trim() || null
      if (plantaFile) planta_url = await uploadArquivo(plantaFile, 'plantas')
      await criarLocal({ obra_id: obraId, nome: nome.trim(), planta_url })
      onCriado()
    } catch (e) {
      setErro('Erro ao criar local: ' + e.message)
      setSalvando(false)
    }
  }

  return (
    <div className="mb-6 bg-concreto-900 border border-concreto-700/60 p-5 rounded-xl shadow-lg space-y-4">
      <h2 className="text-sm font-semibold text-aco-100">Novo Local</h2>

      <div className="space-y-1.5">
        <label className="block text-xs text-aco-300 font-semibold">Nome do Local <span className="text-sinal-500">*</span></label>
        <input
          value={nome}
          onChange={e => setNome(e.target.value)}
          placeholder="ex: Pavimento 6, Apto 302, Área Comum Térreo"
          className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2.5 text-xs text-aco-200 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
        />
      </div>

      <div className="space-y-1.5">
        <label className="block text-xs text-aco-300 font-semibold">Planta Baixa (opcional agora, obrigatória pra iniciar uma vistoria)</label>
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
              onChange={e => { const f = e.target.files[0]; if (f) setPlantaFile(f) }}
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            />
            <span className="text-xs text-aco-300">Escolher arquivo PDF ou imagem da planta baixa</span>
          </div>
        )}
        {!plantaFile && (
          <input
            value={plantaUrl}
            onChange={e => setPlantaUrl(e.target.value)}
            placeholder="Ou cole uma URL da planta"
            className="w-full bg-concreto-800 border border-concreto-700 rounded-lg px-3 py-2 text-xs text-aco-200 mt-1 placeholder-aco-400 focus:outline-none focus:border-sinal-500 transition-colors"
          />
        )}
      </div>

      {erro && <p className="text-alerta text-xs font-mono">{erro}</p>}

      <div className="flex items-center gap-3">
        <button
          onClick={salvar}
          disabled={salvando}
          className="bg-sinal-500 hover:bg-sinal-400 disabled:opacity-50 text-concreto-950 font-semibold text-xs px-5 py-2.5 rounded-lg transition-all"
        >
          {salvando ? 'Criando...' : 'Criar Local'}
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
