"""Métricas de uma replicação: latência e espera por classe; preempções,
utilização e amostras pendentes."""

import numpy as np

from .news2 import NOMES_CLASSES, Classe


def calcular_metricas(resultado, aquecimento: float, fim_chegadas: float) -> dict:
    """Calcula as métricas de uma simulação.

    Só entram tarefas que CHEGARAM entre 'aquecimento' e 'fim_chegadas'.
    O período de aquecimento é descartado porque, no início, o sistema começa
    vazio e não representa o regime normal de funcionamento.
    """
    medidas = [t for t in resultado.tarefas if aquecimento <= t.chegada <= fim_chegadas]
    linha = {}

    for classe in Classe:
        nome = NOMES_CLASSES[classe]
        grupo = [t for t in medidas if t.classe == int(classe)]
        linha[f"{nome}_n"] = len(grupo)
        if not grupo:
            for chave in ("lat_media", "lat_p50", "lat_p95", "lat_p99", "lat_max",
                          "espera_media", "espera_max"):
                linha[f"{nome}_{chave}"] = float("nan")
            continue
        lat = np.array([t.latencia for t in grupo])
        esp = np.array([t.espera for t in grupo])

        linha[f"{nome}_lat_media"] = float(lat.mean())
        linha[f"{nome}_lat_p50"] = float(np.percentile(lat, 50))
        linha[f"{nome}_lat_p95"] = float(np.percentile(lat, 95))
        linha[f"{nome}_lat_p99"] = float(np.percentile(lat, 99))
        linha[f"{nome}_lat_max"] = float(lat.max())
        linha[f"{nome}_espera_media"] = float(esp.mean())
        linha[f"{nome}_espera_max"] = float(esp.max())

    n_medidas = max(len(medidas), 1)
    preempcoes_medidas = sum(t.preempcoes for t in medidas)
    linha["n_tarefas"] = len(medidas)
    linha["preempcoes"] = resultado.total_preempcoes
    linha["preempcoes_por_tarefa"] = preempcoes_medidas / n_medidas
    linha["tempo_troca_contexto"] = resultado.total_preempcoes * resultado.custo_preempcao
    linha["utilizacao_medida"] = (resultado.tempo_ocupado / resultado.tempo_final
                                  if resultado.tempo_final > 0 else float("nan"))
    linha["pendentes_horizonte"] = sum(1 for t in medidas if t.fim > fim_chegadas)
    # Latência geral (todas as classes juntas), útil para ver o "custo" global.
    if medidas:
        lat_todas = np.array([t.latencia for t in medidas])
        linha["geral_lat_media"] = float(lat_todas.mean())
        linha["geral_lat_p99"] = float(np.percentile(lat_todas, 99))
    else:
        linha["geral_lat_media"] = linha["geral_lat_p99"] = float("nan")
    return linha
