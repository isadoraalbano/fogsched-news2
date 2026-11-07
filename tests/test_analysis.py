"""Testes das funções estatísticas da análise."""

import numpy as np

from fogsched.analysis import holm, rank_bisserial
from fogsched.metrics import calcular_metricas
from fogsched.model import TarefaSpec
from fogsched.policies import PrioridadePreemptiva
from fogsched.simulator import simular


def test_holm_exemplo_conhecido():
    # p = [0,01; 0,04; 0,03] -> ordenados 0,01 (×3), 0,03 (×2), 0,04 (×1)
    ajustados = holm([0.01, 0.04, 0.03])
    assert np.allclose(ajustados, [0.03, 0.06, 0.06])


def test_rank_bisserial_extremos():
    assert rank_bisserial(np.array([-1.0, -2.0, -3.0])) == -1.0   # sempre menor
    assert rank_bisserial(np.array([1.0, 2.0, 3.0])) == 1.0       # sempre maior
    assert rank_bisserial(np.array([0.0, 0.0])) == 0.0



def test_metricas_de_um_cenario_calculado_a_mao():
    # Estável (10 s) chega em 0; crítico (1 s) chega em 2 e a interrompe.
    # Crítico: latência 1, espera 0. Estável: termina em 11, espera 1.
    trace = [TarefaSpec(0, 0, 0.0, 10.0, 0, None, "t"), TarefaSpec(1, 1, 2.0, 1.0, 3, None, "t")]
    m = calcular_metricas(simular(trace, PrioridadePreemptiva()), aquecimento=0.0, fim_chegadas=5.0)
    assert m["critica_lat_p99"] == 1.0 and m["critica_espera_max"] == 0.0
    assert m["estavel_lat_max"] == 11.0 and abs(m["estavel_espera_max"] - 1.0) < 1e-12
    assert m["preempcoes_por_tarefa"] == 0.5
    assert m["pendentes_horizonte"] == 1          # a estável ainda não tinha terminado em t = 5
