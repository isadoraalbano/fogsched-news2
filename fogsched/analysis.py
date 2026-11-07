"""Estatística (IC95, Wilcoxon pareado + Holm, rank-bisserial) e gráficos.

    python -m fogsched.analysis results/full

Gera summary.csv, tests.csv, report.md e plots/ na pasta indicada."""

import argparse
import os

import numpy as np
import pandas as pd
import yaml
from scipy import stats

# Métricas que entram no resumo e nos testes: (coluna, rótulo, menor_é_melhor)
METRICAS = [
    ("critica_lat_p99", "Latência p99 — críticas (s)", True),
    ("critica_lat_p95", "Latência p95 — críticas (s)", True),
    ("critica_lat_media", "Latência média — críticas (s)", True),
    ("critica_lat_max", "Latência máxima — críticas (s)", True),
    ("media_lat_p99", "Latência p99 — médias (s)", True),
    ("estavel_espera_max", "Espera máxima — estáveis (s)", True),
    ("baixa_espera_max", "Espera máxima — baixas (s)", True),
    ("geral_lat_media", "Latência média — todas (s)", True),
    ("preempcoes_por_tarefa", "Preempções por tarefa", True),
]

# Métricas testadas estatisticamente (as que respondem às hipóteses H1 e H2).
METRICAS_TESTADAS = ["critica_lat_p99", "critica_lat_max", "media_lat_p99", "estavel_espera_max"]

# Os 4 painéis de cada figura.
PAINEIS = [
    ("critica_lat_p99", "Latência p99 — alertas críticos (s)"),
    ("media_lat_p99", "Latência p99 — classe média (s)"),
    ("estavel_espera_max", "Espera máxima — estáveis (s)"),
    ("preempcoes_por_tarefa", "Preempções por tarefa"),
]

# Cores fixas por política (a cor acompanha a política em TODAS as figuras).
# Paleta categórica validada para daltonismo (ordem fixa, nunca reciclada).
CORES = {
    "Proposta": "#2a78d6",
    "FIFO": "#eb6834",
    "Prioridade NP": "#1baf7a",
    "Prioridade P": "#eda100",
    "Proposta (teto único)": "#e87ba4",
}
CORES_EXTRAS = ["#6250d6", "#e34948"]
MARCADORES = {"Proposta": "o", "FIFO": "s", "Prioridade NP": "^", "Prioridade P": "D",
              "Proposta (teto único)": "P"}
# Política de referência nos testes estatísticos (comparada com as demais).
REFERENCIA = "Proposta"
TINTA, TINTA_2, GRADE, FUNDO = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


# ---------------------------------------------------------------------------
# Estatística
# ---------------------------------------------------------------------------
def ic95(serie: pd.Series) -> float:
    """Meia-largura do intervalo de confiança de 95% (t de Student)."""
    x = serie.dropna()
    n = len(x)
    if n < 2:
        return float("nan")
    return float(stats.t.ppf(0.975, n - 1) * x.std(ddof=1) / np.sqrt(n))


def resumir(df: pd.DataFrame) -> pd.DataFrame:
    """Média, desvio, n e IC95 por cenário × valor × política."""
    linhas = []
    chaves = ["cenario", "parametro", "valor", "politica"]
    for chave, g in df.groupby(chaves, sort=False):
        linha = dict(zip(chaves, chave))
        for col, _, _ in METRICAS:
            if col in g:
                linha[f"{col}_media"] = g[col].mean()
                linha[f"{col}_dp"] = g[col].std(ddof=1)
                linha[f"{col}_ic95"] = ic95(g[col])
        linha["n_replicacoes"] = len(g)
        linhas.append(linha)
    return pd.DataFrame(linhas)


def rank_bisserial(diferencas: np.ndarray) -> float:
    """Correlação rank-bisserial para amostras pareadas (tamanho de efeito)."""
    d = diferencas[diferencas != 0]
    if len(d) == 0:
        return 0.0
    postos = stats.rankdata(np.abs(d))
    w_mais, w_menos = postos[d > 0].sum(), postos[d < 0].sum()
    return float((w_mais - w_menos) / (w_mais + w_menos))


def holm(pvalores: list) -> list:
    """Correção de Holm-Bonferroni para comparações múltiplas."""
    p = np.asarray(pvalores, dtype=float)
    m = len(p)
    ordem = np.argsort(p)
    ajustados = np.empty(m)
    maximo = 0.0
    for posicao, idx in enumerate(ordem):
        valor = min(1.0, (m - posicao) * p[idx])
        maximo = max(maximo, valor)         # garante monotonicidade
        ajustados[idx] = maximo
    return ajustados.tolist()


def testar(df: pd.DataFrame, metricas=None) -> pd.DataFrame:
    """Compara a Proposta com cada outra política, em cada cenário e valor.

    Família de Holm: para cada métrica, todas as comparações (cenários, valores
    e políticas) juntas."""
    metricas = metricas or METRICAS_TESTADAS
    linhas = []
    for (cen, valor), g in df.groupby(["cenario", "valor"], sort=False):
        if REFERENCIA not in set(g["politica"]):
            continue
        ref = REFERENCIA
        for outra in g["politica"].unique():
            if outra == ref:
                continue
            for col in metricas:
                tab = g.pivot_table(index="replicacao", columns="politica", values=col)
                if ref not in tab or outra not in tab:
                    continue
                par = tab[[ref, outra]].dropna()
                if len(par) < 5:
                    continue
                d = (par[ref] - par[outra]).to_numpy()
                if np.allclose(d, 0):
                    p = 1.0
                else:
                    p = float(stats.wilcoxon(par[ref], par[outra], zero_method="wilcox").pvalue)
                linhas.append(dict(cenario=cen, valor=valor, metrica=col, proposta=ref,
                                   comparada=outra, n=len(par),
                                   media_proposta=par[ref].mean(), media_comparada=par[outra].mean(),
                                   mediana_diferenca=float(np.median(d)),
                                   efeito_r=rank_bisserial(d), p=p))
    res = pd.DataFrame(linhas)
    if res.empty:
        return res
    # Holm aplicado separadamente para cada métrica (cada família de hipóteses).
    res["p_holm"] = np.nan
    for col, g in res.groupby("metrica"):
        res.loc[g.index, "p_holm"] = holm(g["p"].tolist())
    res["significativo_5pct"] = res["p_holm"] < 0.05
    return res


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------
def _cor(politica: str, extras: dict) -> str:
    if politica in CORES:
        return CORES[politica]
    if politica not in extras:
        extras[politica] = CORES_EXTRAS[len(extras) % len(CORES_EXTRAS)]
    return extras[politica]


def rotulo_valor(v) -> str:
    """Encurta valores longos: "{0: 1.9, 1: 2.5, 2: 2.9}" vira "1.9/2.5/2.9"."""
    texto = str(v)
    if texto.startswith("{"):
        try:
            d = yaml.safe_load(texto)
            return "/".join(str(d[k]) for k in sorted(d))
        except Exception:  # noqa: BLE001 — se não der para encurtar, mostra como está
            return texto
    return texto


def _numerico(valores) -> bool:
    try:
        pd.to_numeric(pd.Series(valores), errors="raise")
        return True
    except (ValueError, TypeError):
        return False


def graficar_cenario(resumo: pd.DataFrame, cenario: str, titulo: str, pasta: str) -> str:
    """Uma figura com 4 painéis (uma métrica por painel, um eixo cada)."""
    import matplotlib
    matplotlib.use("Agg")                    # gera arquivos sem abrir janela
    import matplotlib.pyplot as plt

    r = resumo[resumo["cenario"] == cenario].copy()
    politicas = list(dict.fromkeys(r["politica"]))
    # Desenha a política de referência por último, para ficar visível por cima
    # quando várias linhas coincidem (resultados idênticos se sobrepõem).
    politicas.sort(key=lambda p: p == REFERENCIA)
    numerico = _numerico(r["valor"].unique())
    if numerico:
        r["x"] = pd.to_numeric(r["valor"])
    else:
        categorias = list(dict.fromkeys(r["valor"]))
        r["x"] = r["valor"].map({c: i for i, c in enumerate(categorias)})

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": GRADE, "axes.labelcolor": TINTA_2,
                         "xtick.color": TINTA_2, "ytick.color": TINTA_2, "text.color": TINTA})
    fig, eixos = plt.subplots(2, 2, figsize=(10, 7), facecolor=FUNDO)
    extras = {}
    for ax, (col, rotulo) in zip(eixos.flat, PAINEIS):
        ax.set_facecolor(FUNDO)
        media, ic = f"{col}_media", f"{col}_ic95"
        if media not in r:
            ax.set_visible(False)
            continue
        n_pol = len(politicas)
        for k, pol in enumerate(politicas):
            d = r[r["politica"] == pol].sort_values("x")
            cor = _cor(pol, extras)
            # Em eixos categóricos, desloca levemente cada política para os
            # pontos não ficarem um em cima do outro.
            x = d["x"] if numerico else d["x"] + (k - (n_pol - 1) / 2) * 0.12
            ax.errorbar(x, d[media], yerr=d[ic], color=cor, marker=MARCADORES.get(pol, "o"),
                        markersize=5, linewidth=2 if numerico else 0, elinewidth=1, capsize=2,
                        markeredgecolor=FUNDO, markeredgewidth=0.8, label=pol)
        valores_pos = r[media][r[media] > 0]
        if col != "preempcoes_por_tarefa" and len(valores_pos) and valores_pos.max() / valores_pos.min() > 50:
            ax.set_yscale("log")            # escala log quando os valores variam muito
        if numerico and r["x"].min() > 0 and r["x"].max() / r["x"].min() >= 20:
            ax.set_xscale("log")            # ex.: 10, 50, 200, 400 ficam bem espaçados
        ax.set_title(rotulo, fontsize=9.5, color=TINTA, loc="left")
        ax.set_xlabel(r["parametro"].iloc[0])
        if not numerico:
            ax.set_xticks(range(len(categorias)), [rotulo_valor(c) for c in categorias])
        ax.grid(True, color=GRADE, linewidth=0.6)
        ax.set_axisbelow(True)
        for lado in ("top", "right"):
            ax.spines[lado].set_visible(False)
    alcas, rotulos = eixos.flat[0].get_legend_handles_labels()
    fig.legend(alcas, rotulos, loc="upper center", ncol=len(rotulos), frameon=False,
               bbox_to_anchor=(0.5, 0.955))
    fig.suptitle(titulo, x=0.01, y=0.995, ha="left", fontsize=11, color=TINTA)
    fig.text(0.01, 0.005, "Pontos: média das replicações; barras: IC95. Linhas sobrepostas = resultados idênticos.", fontsize=8, color=TINTA_2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.92))
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, f"{cenario}.png")
    fig.savefig(caminho, dpi=160, facecolor=FUNDO)
    plt.close(fig)
    return caminho


# ---------------------------------------------------------------------------
# Relatório em Markdown
# ---------------------------------------------------------------------------
def _fmt(media, ic) -> str:
    if pd.isna(media):
        return "—"
    if pd.isna(ic):
        return f"{media:.3g}"
    return f"{media:.3g} ± {ic:.2g}"


def _chave_ordem(v):
    """Ordena valores numericamente quando possível; textos vão depois."""
    try:
        return (0, float(v), "")
    except ValueError:
        return (1, 0.0, str(v))


def escrever_relatorio(resumo, testes, descricoes, caminho):
    linhas = ["# Relatório dos experimentos", "",
              "Valores: média ± meia-largura do IC95 sobre as replicações.", ""]
    for cen in dict.fromkeys(resumo["cenario"]):
        r = resumo[resumo["cenario"] == cen]
        linhas += [f"## {cen}", "", descricoes.get(cen, ""), ""]
        for col, rotulo in [("critica_lat_p99", "Latência p99 das críticas (s)"),
                            ("media_lat_p99", "Latência p99 da classe média (s)"),
                            ("estavel_espera_max", "Espera máxima das estáveis (s)"),
                            ("preempcoes_por_tarefa", "Preempções por tarefa")]:
            if f"{col}_media" not in r:
                continue
            tab = r.pivot_table(index="valor", columns="politica", values=f"{col}_media", aggfunc="first")
            ics = r.pivot_table(index="valor", columns="politica", values=f"{col}_ic95", aggfunc="first")
            ordem = sorted(tab.index, key=_chave_ordem)   # 10 < 50 < 200, não "10" < "200" < "50"
            tab, ics = tab.reindex(ordem), ics.reindex(ordem)
            linhas += [f"**{rotulo}** (parâmetro: `{r['parametro'].iloc[0]}`)", "",
                       "| valor | " + " | ".join(tab.columns) + " |",
                       "|---|" + "---|" * len(tab.columns)]
            for v in tab.index:
                linhas.append(f"| {rotulo_valor(v)} | " + " | ".join(_fmt(tab.loc[v, c], ics.loc[v, c]) for c in tab.columns) + " |")
            linhas.append("")
        if not testes.empty:
            t = testes[(testes["cenario"] == cen) & (testes["metrica"] == "critica_lat_p99")]
            if not t.empty:
                linhas += ["**Testes (Proposta × outra) — latência p99 das críticas**", "",
                           "| valor | comparada | mediana da diferença (s) | r | p (Holm) |", "|---|---|---|---|---|"]
                for _, x in t.iterrows():
                    linhas.append(f"| {rotulo_valor(x['valor'])} | {x['comparada']} | {x['mediana_diferenca']:.3g} | "
                                  f"{x['efeito_r']:.2f} | {x['p_holm']:.3g} |")
                linhas.append("")
        linhas += [f"![{cen}](plots/{cen}.png)", ""]
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Analisa os resultados dos experimentos.")
    ap.add_argument("pasta", help="pasta com raw.csv (ex.: results/demo)")
    args = ap.parse_args(argv)

    df = pd.read_csv(os.path.join(args.pasta, "raw.csv"))
    df["valor"] = df["valor"].astype(str)   # valores podem ser números ou textos
    descricoes = {}
    caminho_cfg = os.path.join(args.pasta, "config_used.yaml")
    if os.path.exists(caminho_cfg):
        with open(caminho_cfg, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        descricoes = {c["id"]: c.get("descricao", "") for c in cfg.get("cenarios", [])}

    resumo = resumir(df)
    resumo.to_csv(os.path.join(args.pasta, "summary.csv"), index=False)
    testes = testar(df)
    testes.to_csv(os.path.join(args.pasta, "tests.csv"), index=False)
    for cen in dict.fromkeys(resumo["cenario"]):
        caminho = graficar_cenario(resumo, cen, f"{cen} — {descricoes.get(cen, '')}",
                                   os.path.join(args.pasta, "plots"))
        print("figura:", caminho)
    escrever_relatorio(resumo, testes, descricoes, os.path.join(args.pasta, "report.md"))
    print("resumo, testes e report.md gerados em", args.pasta)


if __name__ == "__main__":
    main()
