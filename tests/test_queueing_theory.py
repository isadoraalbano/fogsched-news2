"""Validação do simulador contra M/M/1, Cobham e prioridade preemptiva."""

import numpy as np

from fogsched.workload import ConfigCarga, gerar_carga
from fogsched.policies import FIFO, PrioridadeNaoPreemptiva, PrioridadePreemptiva
from fogsched.simulator import simular
from fogsched.queueing_theory import (mm1_tempo_resposta, prioridade_nao_preemptiva,
                                   prioridade_preemptiva_resume)

PROBS = (0.40, 0.30, 0.20, 0.10)   # classes 0, 1, 2, 3
RHO = 0.7


def _simular_respostas(politica_cls, sementes=(1, 2, 3), duracao=60000.0, **kw):
    """Tempo médio de resposta por classe, agregando várias sementes."""
    cfg = ConfigCarga(modo_chegada="poisson", n_pacientes=60, intervalo_amostragem=60.0,
                      utilizacao=RHO, duracao=duracao, probs_classe_poisson=PROBS)
    soma = np.zeros(4)
    cont = np.zeros(4)
    for s in sementes:
        r = simular(gerar_carga(cfg, s), politica_cls(**kw))
        for t in r.tarefas:
            if t.chegada > 0.1 * duracao:          # descarta o aquecimento
                soma[t.classe] += t.latencia
                cont[t.classe] += 1
    return soma / cont, cfg


def _ordenar_teoria(cfg):
    """Taxas por classe, da MAIS prioritária (3) para a menos (0)."""
    lam = cfg.taxa_chegada
    return [lam * PROBS[c] for c in (3, 2, 1, 0)], 1.0 / cfg.servico_medio


def test_fifo_bate_com_mm1():
    medias, cfg = _simular_respostas(FIFO)
    geral = float(np.average(medias, weights=PROBS))
    teoria = mm1_tempo_resposta(cfg.taxa_chegada, 1.0 / cfg.servico_medio)
    assert abs(geral - teoria) / teoria < 0.04, (geral, teoria)


def test_prioridade_nao_preemptiva_bate_com_cobham():
    medias, cfg = _simular_respostas(PrioridadeNaoPreemptiva)
    lams, mu = _ordenar_teoria(cfg)
    teoria = dict(zip((3, 2, 1, 0), prioridade_nao_preemptiva(lams, mu)))
    for classe in (3, 2, 1, 0):
        erro = abs(medias[classe] - teoria[classe]) / teoria[classe]
        assert erro < 0.05, (classe, medias[classe], teoria[classe])


def test_prioridade_preemptiva_bate_com_formula():
    # classe_minima_preempcao=1: qualquer classe maior interrompe uma menor,
    # que é a hipótese da fórmula clássica.
    medias, cfg = _simular_respostas(PrioridadePreemptiva, classe_minima_preempcao=1)
    lams, mu = _ordenar_teoria(cfg)
    teoria = dict(zip((3, 2, 1, 0), prioridade_preemptiva_resume(lams, mu)))
    for classe in (3, 2, 1, 0):
        erro = abs(medias[classe] - teoria[classe]) / teoria[classe]
        assert erro < 0.05, (classe, medias[classe], teoria[classe])
