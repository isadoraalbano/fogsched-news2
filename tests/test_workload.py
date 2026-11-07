"""Testes do gerador de carga: reprodutibilidade e coerência com os parâmetros."""

import numpy as np

from fogsched.workload import ConfigCarga, gerar_carga


def test_mesma_semente_gera_mesma_carga():
    cfg = ConfigCarga(duracao=900)
    a = gerar_carga(cfg, 42)
    b = gerar_carga(cfg, 42)
    assert a == b                       # TarefaSpec é comparável campo a campo


def test_sementes_diferentes_geram_cargas_diferentes():
    cfg = ConfigCarga(duracao=900)
    assert gerar_carga(cfg, 1) != gerar_carga(cfg, 2)


def test_carga_ordenada_e_dentro_do_horizonte():
    cfg = ConfigCarga(duracao=1200)
    tr = gerar_carga(cfg, 3)
    chegadas = [t.chegada for t in tr]
    assert chegadas == sorted(chegadas)
    assert 0 <= chegadas[0] and chegadas[-1] < cfg.duracao


def test_taxa_de_chegada_e_servico_medio():
    # Com N pacientes enviando a cada T segundos, chegam ~N/T amostras por segundo,
    # e o tempo médio de serviço observado fica perto de E[S] = ρ/λ.
    cfg = ConfigCarga(n_pacientes=200, intervalo_amostragem=60, duracao=7200, utilizacao=0.8)
    tr = gerar_carga(cfg, 5)
    taxa = len(tr) / cfg.duracao
    assert abs(taxa - cfg.taxa_chegada) / cfg.taxa_chegada < 0.03
    es = np.mean([t.servico for t in tr])
    assert abs(es - cfg.servico_medio) / cfg.servico_medio < 0.05


def test_rajada_aumenta_chegadas_na_janela():
    base = ConfigCarga(duracao=3600)
    com = ConfigCarga(duracao=3600, rajadas=[{"inicio": 1800, "duracao": 300, "fator": 3}])
    def na_janela(tr):
        return sum(1 for t in tr if 1800 <= t.chegada < 2100)
    assert na_janela(gerar_carga(com, 9)) > 2.2 * na_janela(gerar_carga(base, 9))


def test_mais_tempo_critico_gera_mais_tarefas_criticas():
    poucas = gerar_carga(ConfigCarga(proporcao_critica=0.01, duracao=3600), 11)
    muitas = gerar_carga(ConfigCarga(proporcao_critica=0.20, duracao=3600), 11)
    frac = lambda tr: np.mean([t.classe == 3 for t in tr])
    assert frac(muitas) > 3 * frac(poucas)


def test_modo_poisson_respeita_probabilidades():
    cfg = ConfigCarga(modo_chegada="poisson", duracao=20000, probs_classe_poisson=(0.4, 0.3, 0.2, 0.1))
    tr = gerar_carga(cfg, 13)
    frac = np.bincount([t.classe for t in tr], minlength=4) / len(tr)
    assert np.allclose(frac, [0.4, 0.3, 0.2, 0.1], atol=0.015)
