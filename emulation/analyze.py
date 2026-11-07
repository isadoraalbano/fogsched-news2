"""Análise da emulação em três visões (simulado, interno no Pi e ponta a ponta).

    python -m emulation.analyze results_emulation/short

Os testes estatísticos usam a P95 das críticas: com cerca de 140 alertas
críticos por execução, a P99 depende de uma ou duas amostras e é apresentada
apenas de forma descritiva. Alertas que não chegam contam como falha de entrega.

Gera raw.csv, summary.csv, tests_<view>.csv, report.md e plots/critical_p95.png."""

import argparse
import json
import os

import numpy as np
import pandas as pd

from fogsched import analysis as analise
from fogsched.metrics import calcular_metricas
from fogsched.model import ResultadoSimulacao, Tarefa, TarefaSpec
from fogsched.policies import criar_politica
from fogsched.simulator import simular

from .make_trace import trace_para_specs

VISOES = ["simulado", "interno", "ponta_a_ponta"]
METRICAS_TESTADAS = ["critica_lat_p95", "estavel_espera_max"]
CORES_VISOES = {"simulado": "#9e9d98", "interno": "#6250d6", "ponta_a_ponta": "#e34948"}
NOMES_VISOES = {"simulado": "Simulado", "interno": "Pi (interno)",
                "ponta_a_ponta": "Ponta a ponta"}


def _resultado_medido(df: pd.DataFrame, visao: str, preempcoes: int, ocupado: float):
    """Monta um ResultadoSimulacao com os tempos MEDIDOS, para reaproveitar
    exatamente o mesmo cálculo de métricas da simulação (metrics.py)."""
    tarefas = []
    for r in df.itertuples():
        if visao == "interno":
            chegada = r.t_plan              # base comum para o filtro de aquecimento
            latencia = r.fim_no - r.chegada_no
            inicio = chegada + (r.inicio_no - r.chegada_no)
        else:
            chegada = r.t_envio
            latencia = r.t_recebido - r.t_envio
            inicio = None
        spec = TarefaSpec(id=int(r.id), paciente=int(r.paciente), chegada=float(chegada),
                          servico=float(r.servico), classe=int(r.classe),
                          news2=int(r.news2), estado_paciente="?")
        t = Tarefa.de_spec(spec)
        t.fim = chegada + latencia
        t.inicio_primeiro = inicio
        t.preempcoes = int(r.preempcoes)
        tarefas.append(t)
    fim = max(t.fim for t in tarefas)
    return ResultadoSimulacao(tarefas=tarefas, tempo_ocupado=ocupado, total_preempcoes=preempcoes,
                              custo_preempcao=0.0, tempo_final=fim)


def chegadas_apos_termino(df: pd.DataFrame, janela: float = 0.001) -> float:
    """% de amostras registradas no Pi até 1 ms depois do término de outra tarefa.

    Se a rede entrega as amostras de forma independente do processamento, esse
    valor fica em poucos por cento (taxa de términos × 1 ms). Um valor alto
    indica que as amostras ficaram retidas no caminho e só foram entregues
    quando o Pi transmitiu um alerta, comportamento típico da economia de
    energia do Wi-Fi: nesse caso, a fila se forma na rede, e não no Pi."""
    fins = np.sort(df["fim_no"].to_numpy())
    chegadas = df["chegada_no"].to_numpy()
    i = np.searchsorted(fins, chegadas) - 1
    validos = i >= 0
    dist = chegadas[validos] - fins[i[validos]]
    return 100.0 * float(np.mean(dist < janela)) if len(dist) else float("nan")


def carregar(pasta: str) -> pd.DataFrame:
    with open(os.path.join(pasta, "metadata.json"), encoding="utf-8") as f:
        meta = json.load(f)
    traces = {}
    linhas = []
    for nome, info in meta["execucoes"].items():
        df = pd.read_csv(os.path.join(pasta, "runs", info["arquivo"]))
        if info["trace"] not in traces:
            with open(os.path.join(pasta, "traces", info["trace"]), encoding="utf-8") as f:
                traces[info["trace"]] = json.load(f)
        trace = traces[info["trace"]]
        cfg = trace["config"]
        aq, dur = cfg["aquecimento"], cfg["duracao"]
        recebidos = df.dropna(subset=["t_recebido"])
        est = info.get("estatisticas") or {}
        base = {"cenario": info["cenario"], "parametro": info["parametro"], "valor": info["valor"],
                "replicacao": info["replicacao"], "politica": info["politica"],
                "servico_medio": trace["servico_medio"], "perdidos": len(df) - len(recebidos),
                "entrega_pct": 100.0 * len(recebidos) / len(df),
                "duplicadas": int(est.get("duplicadas", 0)),
                "rtt_mediana_ms": info["rede"]["rtt_mediana_ms"],
                "pedaco_media_ms": (info.get("no_fog") or {}).get("calibracao", {}).get("pedaco_media_ms"),
                "news2_divergente": int((recebidos["classe"] != recebidos["classe_esperada"]).sum())}

        # Medidas derivadas, por amostra (só as que chegaram depois do aquecimento).
        m = recebidos[(recebidos["t_plan"] >= aq) & (recebidos["t_plan"] <= dur)]
        interno = m["fim_no"] - m["chegada_no"]
        e2e = m["t_recebido"] - m["t_envio"]
        reacao = m["visto_no"] - m["chegada_no"]
        extras = {"rede_media_ms": 1e3 * float((e2e - interno).mean()),
                  "rede_p99_ms": 1e3 * float(np.percentile(e2e - interno, 99)),
                  "reacao_media_ms": 1e3 * float(reacao.mean()),
                  "reacao_p99_ms": 1e3 * float(np.percentile(reacao, 99)),
                  "chegadas_pos_termino_pct": chegadas_apos_termino(recebidos)}

        resultados = {
            "simulado": simular(trace_para_specs(trace),
                                criar_politica(info["politica_cfg"], trace["servico_medio"])),
            "interno": _resultado_medido(recebidos, "interno", est.get("preempcoes", 0),
                                         est.get("tempo_ocupado", float("nan"))),
            "ponta_a_ponta": _resultado_medido(recebidos, "ponta_a_ponta", est.get("preempcoes", 0),
                                               est.get("tempo_ocupado", float("nan"))),
        }
        for visao, res in resultados.items():
            linha = {**base, "visao": visao, **calcular_metricas(res, aq, dur)}
            if visao != "simulado":
                linha.update(extras)
            linhas.append(linha)
    return pd.DataFrame(linhas)


def _fmt_ms(media, ic):
    if pd.isna(media):
        return "—"
    return f"{1e3 * media:.1f} ± {1e3 * ic:.1f}" if not pd.isna(ic) else f"{1e3 * media:.1f}"


def escrever_relatorio(brutos: pd.DataFrame, resumo: pd.DataFrame, testes: dict, caminho: str):
    L = ["# Relatório da emulação (Raspberry Pi)", "",
         "Valores: média entre replicações ± meia-largura do IC95. Latências em **ms**.", ""]
    for cen in dict.fromkeys(resumo["cenario"]):
        r = resumo[resumo["cenario"] == cen]
        b = brutos[(brutos["cenario"] == cen) & (brutos["visao"] == "ponta_a_ponta")]
        for col, titulo in [("critica_lat_p95", "Latência P95 dos alertas críticos (ms) — métrica dos testes"),
                            ("critica_lat_p99", "Latência P99 dos alertas críticos (ms) — descritiva")]:
            L += [f"## {cen}" if col == "critica_lat_p95" else "", "", f"### {titulo}", "",
                  "| Política | Simulado | Pi (interno) | Ponta a ponta |", "|---|---|---|---|"]
            for pol in dict.fromkeys(r["politica"]):
                cel = []
                for v in VISOES:
                    x = r[(r["politica"] == pol) & (r["visao"] == v)]
                    cel.append(_fmt_ms(x[f"{col}_media"].iloc[0], x[f"{col}_ic95"].iloc[0])
                               if len(x) else "—")
                L.append(f"| {pol} | " + " | ".join(cel) + " |")
        L += ["", "### Outras métricas (ponta a ponta)", "",
              "| Política | p99 média (ms) | espera máx. estáveis (ms) | preempções/tarefa | "
              "rede média (ms) | reação média (ms) | alertas perdidos | entrega (%) |",
              "|---|---|---|---|---|---|---|---|"]
        for pol in dict.fromkeys(r["politica"]):
            x = r[(r["politica"] == pol) & (r["visao"] == "ponta_a_ponta")].iloc[0]
            bb = b[b["politica"] == pol]
            L.append(f"| {pol} | {_fmt_ms(x['media_lat_p99_media'], x['media_lat_p99_ic95'])} | "
                     f"{_fmt_ms(x['estavel_espera_max_media'], x['estavel_espera_max_ic95'])} | "
                     f"{x['preempcoes_por_tarefa_media']:.3f} | {bb['rede_media_ms'].mean():.2f} | "
                     f"{bb['reacao_media_ms'].mean():.3f} | {int(bb['perdidos'].sum())} | "
                     f"{bb['entrega_pct'].mean():.2f} |")
        t = testes.get("ponta_a_ponta")
        if t is not None and len(t):
            tt = t[(t["cenario"] == cen) & (t["metrica"] == "critica_lat_p95")]
            if len(tt):
                L += ["", "### Wilcoxon pareado (Proposta × outras), P95 das críticas, ponta a ponta", "",
                      "| Comparação | p (Holm) | efeito r |", "|---|---|---|"]
                for row in tt.itertuples():
                    L.append(f"| Proposta × {row.comparada} | {row.p_holm:.4f} | {row.efeito_r:+.2f} |")
        L.append("")
    div = int(brutos.loc[brutos["visao"] == "interno", "news2_divergente"].sum())
    L += ["## Verificações", "",
          f"- Amostras em que o NEWS2 calculado no Pi diferiu do esperado: **{div}** (deve ser 0).",
          f"- Alertas que não chegaram ao posto (falhas de entrega): **{int(brutos.loc[brutos['visao'] == 'interno', 'perdidos'].sum())}**.",
          f"- Amostras recebidas em duplicidade pelo nó fog (QoS 1) e descartadas: {int(brutos.loc[brutos['visao'] == 'interno', 'duplicadas'].sum())}.",
          f"- RTT mediano da rede sem carga: {brutos['rtt_mediana_ms'].median():.2f} ms.",
          f"- Duração média de um pedaço do estágio 2 no Pi: {brutos['pedaco_media_ms'].median():.3f} ms.",
          f"- Amostras entregues ao Pi logo após o término de outra tarefa (até 1 ms): "
          f"{brutos['chegadas_pos_termino_pct'].median():.0f}% (esperado: poucos %).", ""]
    if brutos["chegadas_pos_termino_pct"].median() > 30:
        L += ["> **ATENÇÃO: as amostras ficaram retidas antes de chegar ao escalonador.** Elas foram",
              "> registradas no Pi quase sempre logo depois do término de outra tarefa, então a fila",
              "> se formou fora do Pi e a comparação entre as políticas fica distorcida. Confira se o",
              "> Pi está com o pacote mais recente (nó fog em dois processos) e se a economia de",
              "> energia do Wi-Fi está desligada (`sudo bash pi/install_pi.sh`); depois repita.", ""]
    elif brutos["chegadas_pos_termino_pct"].median() > 5:
        L += ["> Nota: parte das amostras chegou ao Pi em pequenos grupos (rede sem fio). Isso deixa",
              "> as chegadas mais irregulares que no roteiro e tende a aumentar a fila no Pi nas",
              "> políticas sem preempção; registre esse valor ao comparar com a simulação.", ""]
    if brutos["rtt_mediana_ms"].median() > 20:
        L += [f"> Nota: rede lenta (RTT mediano {brutos['rtt_mediana_ms'].median():.0f} ms). A latência",
              "> interna não é afetada, mas a ponta a ponta inclui esses atrasos e suas variações.", ""]
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def graficar(resumo: pd.DataFrame, pasta: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(pasta, exist_ok=True)
    cenarios = list(dict.fromkeys(resumo["cenario"]))
    fig, eixos = plt.subplots(1, len(cenarios), figsize=(4.6 * len(cenarios), 4), squeeze=False)
    for ax, cen in zip(eixos[0], cenarios):
        r = resumo[resumo["cenario"] == cen]
        politicas = list(dict.fromkeys(r["politica"]))
        x = np.arange(len(politicas))
        for k, v in enumerate(VISOES):
            d = r[r["visao"] == v].set_index("politica").reindex(politicas)
            ax.bar(x + (k - 1) * 0.27, 1e3 * d["critica_lat_p95_media"], width=0.25,
                   yerr=1e3 * d["critica_lat_p95_ic95"].fillna(0), capsize=2,
                   color=CORES_VISOES[v], label=NOMES_VISOES[v])
        ax.set_xticks(x, politicas, rotation=20, ha="right", fontsize=8)
        ax.set_title(cen, fontsize=10)
        ax.set_ylabel("Latência P95 — críticas (ms)")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#e4e3df")
        ax.set_axisbelow(True)
    eixos[0][0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    caminho = os.path.join(pasta, "critical_p95.png")
    fig.savefig(caminho, dpi=150)
    plt.close(fig)
    return caminho


def analisar(pasta: str) -> dict:
    brutos = carregar(pasta)
    brutos.to_csv(os.path.join(pasta, "raw.csv"), index=False)
    resumos, testes = [], {}
    for v in VISOES:
        d = brutos[brutos["visao"] == v]
        rs = analise.resumir(d)
        rs["visao"] = v
        resumos.append(rs)
        if analise.REFERENCIA in set(d["politica"]) and d["replicacao"].nunique() >= 2:
            testes[v] = analise.testar(d, METRICAS_TESTADAS)
            testes[v].to_csv(os.path.join(pasta, f"tests_{v}.csv"), index=False)
    resumo = pd.concat(resumos, ignore_index=True)
    resumo.to_csv(os.path.join(pasta, "summary.csv"), index=False)
    escrever_relatorio(brutos, resumo, testes, os.path.join(pasta, "report.md"))
    grafico = graficar(resumo, os.path.join(pasta, "plots"))
    return {"brutos": brutos, "resumo": resumo, "testes": testes, "grafico": grafico}


def main():
    ap = argparse.ArgumentParser(description="Análise dos resultados da emulação.")
    ap.add_argument("pasta")
    a = ap.parse_args()
    analisar(a.pasta)
    print(f"Relatório em {os.path.join(a.pasta, 'report.md')}")


if __name__ == "__main__":
    main()
