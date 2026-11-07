"""Testes do NEWS2 contra a tabela oficial."""

from fogsched.news2 import (Classe, SinaisVitais, calcular_news2, classificar,
                            pontos_freq_respiratoria, pontos_pa_sistolica, pontos_pulso,
                            pontos_spo2_escala1, pontos_temperatura)


def normal(**alteracoes):
    """Paciente com todos os sinais normais; 'alteracoes' muda só o que for pedido."""
    base = dict(freq_respiratoria=16, spo2=97, oxigenio_suplementar=False, pa_sistolica=120,
                pulso=75, consciencia_alerta=True, temperatura=36.8)
    base.update(alteracoes)
    return SinaisVitais(**base)


def test_todos_normais_da_zero_e_estavel():
    r = calcular_news2(normal())
    assert r.total == 0
    assert classificar(r) == Classe.ESTAVEL


def test_limites_freq_respiratoria():
    # Cada par (valor, pontos esperados) testa uma borda da tabela.
    casos = [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3)]
    for valor, esperado in casos:
        assert pontos_freq_respiratoria(valor) == esperado, valor


def test_limites_spo2_escala1():
    for valor, esperado in [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (100, 0)]:
        assert pontos_spo2_escala1(valor) == esperado, valor


def test_limites_pa_sistolica():
    for valor, esperado in [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1),
                            (111, 0), (219, 0), (220, 3)]:
        assert pontos_pa_sistolica(valor) == esperado, valor


def test_limites_pulso():
    for valor, esperado in [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1),
                            (110, 1), (111, 2), (130, 2), (131, 3)]:
        assert pontos_pulso(valor) == esperado, valor


def test_limites_temperatura():
    for valor, esperado in [(35.0, 3), (35.1, 1), (36.0, 1), (36.1, 0), (38.0, 0),
                            (38.1, 1), (39.0, 1), (39.1, 2)]:
        assert pontos_temperatura(valor) == esperado, valor


def test_oxigenio_e_consciencia():
    assert calcular_news2(normal(oxigenio_suplementar=True)).total == 2
    assert calcular_news2(normal(consciencia_alerta=False)).total == 3


def test_parametro_isolado_com_3_vira_media():
    # Só a consciência alterada: total 3, mas é um "red score" -> MÉDIA.
    r = calcular_news2(normal(consciencia_alerta=False))
    assert r.total == 3 and r.tem_parametro_vermelho
    assert classificar(r) == Classe.MEDIA
    # Desligando a regra, cai para BAIXA (útil para análise de sensibilidade).
    assert classificar(r, vermelho_eleva_para_media=False) == Classe.BAIXA


def test_faixas_de_classe():
    # 1–4 sem vermelho -> BAIXA
    r = calcular_news2(normal(freq_respiratoria=22))           # 2 pontos
    assert r.total == 2 and classificar(r) == Classe.BAIXA
    # 5–6 -> MÉDIA
    r = calcular_news2(normal(freq_respiratoria=22, pulso=115, temperatura=38.5))  # 2+2+1
    assert r.total == 5 and classificar(r) == Classe.MEDIA
    # >= 7 -> CRÍTICA
    r = calcular_news2(normal(freq_respiratoria=26, spo2=90, oxigenio_suplementar=True))  # 3+3+2
    assert r.total == 8 and classificar(r) == Classe.CRITICA


def test_pior_caso_possivel_da_20():
    r = calcular_news2(SinaisVitais(freq_respiratoria=30, spo2=85, oxigenio_suplementar=True,
                                    pa_sistolica=80, pulso=140, consciencia_alerta=False,
                                    temperatura=34.0))
    # FR 3 + SpO2 3 + O2 2 + PAS 3 + pulso 3 + consciência 3 + temp 3 = 20
    assert r.total == 20
