"""Compara o simulador com a teoria de filas (ρ = 0,7) e grava results/validation.csv.

    python -m fogsched.validation"""

import os

import numpy as np
import pandas as pd

from .workload import ConfigCarga, gerar_carga
from .policies import FIFO, PrioridadeNaoPreemptiva, PrioridadePreemptiva
from .simulator import simular
from .queueing_theory import (mm1_tempo_resposta, prioridade_nao_preemptiva,
                           prioridade_preemptiva_resume)

PROBS = (0.40, 0.30, 0.20, 0.10)    # fração de tarefas nas classes 0, 1, 2, 3
ORDEM = (3, 2, 1, 0)                # da mais para a menos prioritária


def main(rho: float = 0.7, sementes=range(1, 11), duracao: float = 60000.0):
    cfg = ConfigCarga(modo_chegada="poisson", n_pacientes=60, intervalo_amostragem=60.0,
                      utilizacao=rho, duracao=duracao, probs_classe_poisson=PROBS)
    lam, mu = cfg.taxa_chegada, 1.0 / cfg.servico_medio
    lams = [lam * PROBS[c] for c in ORDEM]
    teorias = {
        "FIFO (M/M/1)": {c: mm1_tempo_resposta(lam, mu) for c in ORDEM},
        "Prioridade NP (Cobham)": dict(zip(ORDEM, prioridade_nao_preemptiva(lams, mu))),
        "Prioridade P (preemptiva-retomar)": dict(zip(ORDEM, prioridade_preemptiva_resume(lams, mu))),
    }
    fabricas = {
        "FIFO (M/M/1)": FIFO,
        "Prioridade NP (Cobham)": PrioridadeNaoPreemptiva,
        "Prioridade P (preemptiva-retomar)": lambda: PrioridadePreemptiva(classe_minima_preempcao=1),
    }
    linhas = []
    for nome, fabrica in fabricas.items():
        por_semente = {c: [] for c in ORDEM}
        for s in sementes:
            r = simular(gerar_carga(cfg, s), fabrica())
            for c in ORDEM:
                lat = [t.latencia for t in r.tarefas if t.classe == c and t.chegada > 0.1 * duracao]
                por_semente[c].append(np.mean(lat))
        for c in ORDEM:
            x = np.array(por_semente[c])
            media = x.mean()
            ic = 2.262 * x.std(ddof=1) / np.sqrt(len(x)) if len(x) == 10 else float("nan")
            teoria = teorias[nome][c]
            linhas.append(dict(politica=nome, classe=c, simulado=media, ic95=ic, teoria=teoria,
                               erro_relativo_pct=100 * (media - teoria) / teoria))
    df = pd.DataFrame(linhas)
    os.makedirs("results", exist_ok=True)
    df.to_csv("results/validation.csv", index=False)
    with pd.option_context("display.float_format", "{:.4f}".format, "display.width", 120):
        print(f"ρ = {rho}, E[S] = {cfg.servico_medio:.4f} s, {len(list(sementes))} sementes")
        print(df.to_string(index=False))
    return df


if __name__ == "__main__":
    main()
