"""Pre-computa agregados para o dashboard, a partir do catalogo ja gerado.

O `series.json` tem contagens por serie, mas nao a distribuicao das edicoes no
tempo -- e isso que os graficos "edicoes por mes" precisam. Em vez de o navegador
baixar os 337 issues/*.json e somar, este script le tudo uma vez e grava um
`web/data/stats.json` enxuto que o dashboard carrega de uma vez.

Roda sobre o que ja esta em web/data -- nao raspa nada. Rode depois do ingest:

    python ingest/stats.py

Aviso de cobertura: a contagem por mes so e completa de `cobertura.de` (2026-01)
em diante. Meses anteriores vem so das series que seguem publicando (o back-fill),
entao subestimam o passado -- o dashboard mostra isso e filtra por janela.
"""

import datetime as dt
import json
import pathlib
from collections import defaultdict

RAIZ = pathlib.Path(__file__).resolve().parent.parent
DATA = RAIZ / "web" / "data"


def main():
    series = json.loads((DATA / "series.json").read_text(encoding="utf-8"))

    por_mes = defaultdict(lambda: {"dc": 0, "marvel": 0})   # mes -> contagem por editora
    novos_por_mes = defaultdict(int)                        # mes -> series estreando
    semanas_por_serie = {}                                  # id -> [quartas de lancamento]
    total_edicoes = 0

    def quarta_da_semana(iso):
        # Quarta-feira (ancora da semana da LOCG) da semana que contem `iso`.
        # Mesma logica do quartaDaSemana do app.js, para casar o filtro de data.
        d = dt.date.fromisoformat(iso)
        return (d - dt.timedelta(days=(d.weekday() - 2) % 7)).isoformat()

    for s in series:
        arq = DATA / "issues" / f"{s['id']}.json"
        edicoes = json.loads(arq.read_text(encoding="utf-8")).get("edicoes", [])
        semanas = set()
        for e in edicoes:
            data = e.get("data") or ""
            if len(data) == 10:
                por_mes[data[:7]][s["editora"]] += 1
                total_edicoes += 1
                semanas.add(quarta_da_semana(data))
        if semanas:
            semanas_por_serie[s["id"]] = sorted(semanas)
        if edicoes:
            estreia = min(edicoes, key=lambda e: e["data"])["data"][:7]
            if len(estreia) == 7:
                novos_por_mes[estreia] += 1

    top = sorted(series, key=lambda s: -(s.get("edicoes_conhecidas") or 0))[:20]

    stats = {
        "gerado_em": dt.datetime.now().replace(microsecond=0).isoformat(),
        "total_edicoes": total_edicoes,
        "por_mes": [
            {"mes": m, "dc": por_mes[m]["dc"], "marvel": por_mes[m]["marvel"],
             "total": por_mes[m]["dc"] + por_mes[m]["marvel"], "novos": novos_por_mes.get(m, 0)}
            for m in sorted(por_mes)
        ],
        "top_series": [
            {"id": s["id"], "nome": s["nome"], "editora": s["editora"],
             "edicoes": s.get("edicoes_conhecidas") or 0}
            for s in top
        ],
        "semanas_por_serie": semanas_por_serie,
    }
    (DATA / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"stats.json: {len(stats['por_mes'])} meses, {total_edicoes} edicoes, "
          f"top serie: {top[0]['nome']} ({top[0].get('edicoes_conhecidas')} ed)")


if __name__ == "__main__":
    main()
