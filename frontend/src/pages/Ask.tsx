import { useState } from "react";
import { api, type AskResult, type Meta } from "../api";
import { AnswerView } from "../components/AgentPanel";
import { Notice } from "../components/ui";
import { short } from "../format";

export default function Ask({ meta }: { meta: Meta }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<AskResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function submit(q: string) {
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.ask(q.trim());
      setHistory((h) => [res, ...h]);
      setQuestion("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ask">
      <header className="page-head">
        <div>
          <h1>Poser une question</h1>
          <p className="muted">
            La question est traduite en SQL {meta.llm ? `par ${meta.llm}` : "par les règles de la baseline (aucun LLM configuré)"},
            puis exécutée en lecture seule sur l'instantané audité du run publié. Tout résultat sur moins de{" "}
            {meta.min_group_size} demandes est masqué. Les adresses, emails et téléphones sont retirés avant tout envoi.
          </p>
        </div>
      </header>

      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); submit(question); }}>
        <label htmlFor="q" className="sr-only">Question</label>
        <input id="q" value={question} onChange={(e) => setQuestion(e.target.value)} autoComplete="off"
               placeholder="Combien de demandes de bruit à Brooklyn ?" maxLength={500} />
        <button className="btn-primary" disabled={busy || !question.trim()}>{busy ? "Calcul…" : "Répondre"}</button>
      </form>
      <div className="examples">
        <span className="muted">Essayer :</span>
        {meta.example_questions.map((q) => (
          <button key={q} className="chip" onClick={() => submit(q)} disabled={busy}>{q}</button>
        ))}
      </div>

      {error && <Notice tone="critical">{error}</Notice>}
      {history.length === 0 && !error && (
        <p className="muted empty">Les réponses s'affichent ici, avec le SQL exécuté. Elles ne sont pas publiées dans le tableau de bord.</p>
      )}
      <div className="answers">
        {history.map((h, i) => (
          <div key={history.length - i}>
            <AnswerView a={h.answer} />
            <p className="muted small answer-src">Instantané <code>{short(h.snapshot_hash)}</code> du run <code>{h.run_id}</code></p>
          </div>
        ))}
      </div>
    </div>
  );
}
