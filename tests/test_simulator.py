"""Testes do simulador e das políticas: invariantes, equivalências entre
políticas e cenários calculados à mão."""

from fogsched.workload import ConfigCarga, gerar_carga
from fogsched.model import TarefaSpec
from fogsched.policies import (FIFO, PreemptivaComAging, PrioridadeNaoPreemptiva,
                                PrioridadePreemptiva)
from fogsched.simulator import simular


def spec(i, chegada, servico, classe):
    """Atalho para criar uma tarefa de teste."""
    return TarefaSpec(id=i, paciente=i, chegada=chegada, servico=servico, classe=classe,
                      news2=None, estado_paciente="teste")


def fins(resultado):
    """Dicionário id -> instante de término."""
    return {t.id: t.fim for t in resultado.tarefas}


def todas_as_politicas():
    return [FIFO(), PrioridadeNaoPreemptiva(), PrioridadePreemptiva(),
            PreemptivaComAging(alfa=0.05), PreemptivaComAging(alfa=0.05, classe_minima_preempcao=2)]


def carga_realista(utilizacao=0.95, semente=1):
    return gerar_carga(ConfigCarga(utilizacao=utilizacao, duracao=1800), semente)


# ---------------------------------------------------------------------------
# 1) Invariantes
# ---------------------------------------------------------------------------
def test_invariantes_em_carga_realista():
    trace = carga_realista()
    custo = 0.01
    for pol in todas_as_politicas():
        r = simular(trace, pol, custo_preempcao=custo, registrar_segmentos=True)
        # Toda tarefa terminou, com término depois de chegada + serviço.
        assert all(t.fim is not None for t in r.tarefas), pol.nome
        assert all(t.fim >= t.chegada + t.servico - 1e-9 for t in r.tarefas), pol.nome
        # Conservação do trabalho: o servidor trabalhou o total de serviço mais
        # as trocas de contexto. Só críticos preemptam na Proposta e na
        # Prioridade P, e um crítico em troca de contexto nunca é preemptado; ali
        # a igualdade é exata. Com classe mínima 2, uma troca pode
        # ser interrompida por outra preempção e só a parte ocorrida conta.
        servico = sum(t.servico for t in trace)
        esperado = servico + r.total_preempcoes * custo
        if getattr(pol, "classe_minima_preempcao", 3) < 3:
            assert servico - 1e-6 <= r.tempo_ocupado <= esperado + 1e-6, pol.nome
        else:
            assert abs(r.tempo_ocupado - esperado) < 1e-6 * esperado, pol.nome
        assert r.tempo_ocupado <= r.tempo_final + 1e-6, pol.nome
        # O servidor nunca executa dois trechos ao mesmo tempo.
        segs = sorted(r.segmentos)
        for (i1, f1, _), (i2, _, _) in zip(segs, segs[1:]):
            assert i2 >= f1 - 1e-9, pol.nome
        # Soma dos trechos de cada tarefa = o seu serviço (a troca de contexto
        # ocupa o processador, mas não é trabalho de nenhuma tarefa).
        por_tarefa = {}
        for ini, fim, tid in r.segmentos:
            por_tarefa[tid] = por_tarefa.get(tid, 0.0) + (fim - ini)
        for t in r.tarefas:
            assert abs(por_tarefa[t.id] - t.servico) < 1e-7, pol.nome
        # Políticas não preemptivas nunca interrompem.
        if not pol.preemptiva:
            assert r.total_preempcoes == 0, pol.nome


def test_servidor_nunca_fica_ocioso_com_fila():
    # Propriedade "conservadora de trabalho": com uma única tarefa longa e várias
    # curtas chegando, o último término é igual ao trabalho total (sem buracos).
    trace = [spec(0, 0.0, 5.0, 0)] + [spec(i, 0.5 * i, 1.0, i % 4) for i in range(1, 8)]
    trabalho = sum(t.servico for t in trace)
    for pol in todas_as_politicas():
        r = simular(trace, pol)
        assert abs(r.tempo_final - trabalho) < 1e-9, pol.nome


# ---------------------------------------------------------------------------
# 2) Degeneração
# ---------------------------------------------------------------------------
def test_proposta_sem_aging_e_sem_preempcao_igual_a_np():
    trace = carga_realista(1.05, 2)
    a = simular(trace, PrioridadeNaoPreemptiva())
    b = simular(trace, PreemptivaComAging(alfa=0.0, preemptiva=False))
    assert fins(a) == fins(b)


def test_proposta_sem_aging_igual_a_preemptiva():
    trace = carga_realista(1.05, 3)
    for classe_min in (2, 3):
        a = simular(trace, PrioridadePreemptiva(classe_minima_preempcao=classe_min), custo_preempcao=0.01)
        b = simular(trace, PreemptivaComAging(alfa=0.0, classe_minima_preempcao=classe_min),
                    custo_preempcao=0.01)
        assert fins(a) == fins(b), classe_min
        assert a.total_preempcoes == b.total_preempcoes


def test_classe_unica_todas_viram_fifo():
    # Se todas as tarefas têm a mesma classe, prioridade não muda nada.
    trace = [spec(t.id, t.chegada, t.servico, 1) for t in carga_realista(0.9, 4)]
    ref = fins(simular(trace, FIFO()))
    for pol in (PrioridadeNaoPreemptiva(), PrioridadePreemptiva(), PreemptivaComAging(alfa=0.1)):
        assert fins(simular(trace, pol)) == ref, pol.nome


# ---------------------------------------------------------------------------
# 3) Propriedades da Proposta (cenários montados à mão)
# ---------------------------------------------------------------------------
def test_critico_interrompe_estavel_imediatamente():
    # Estável chega em 0 e precisa de 10 s; crítico chega em 2 s.
    trace = [spec(0, 0.0, 10.0, 0), spec(1, 2.0, 1.0, 3)]
    r_np = simular(trace, PrioridadeNaoPreemptiva())
    r_pa = simular(trace, PreemptivaComAging(alfa=0.01))
    assert fins(r_np)[1] == 11.0       # NP: crítico espera o estável terminar
    assert fins(r_pa)[1] == 3.0        # Proposta: crítico começa na hora
    assert fins(r_pa)[0] == 11.0       # estável retoma de onde parou


def test_custo_da_preempcao_atrasa_o_alerta_que_preempta():
    # Estável de 10 s em t = 0; crítico de 1 s em t = 2; troca de contexto de 0,5 s.
    # O crítico só começa depois da troca (2,5 s) e termina em 3,5 s; o estável
    # retoma em 3,5 s com 8 s restantes e termina em 11,5 s.
    trace = [spec(0, 0.0, 10.0, 0), spec(1, 2.0, 1.0, 3)]
    r = simular(trace, PreemptivaComAging(alfa=0.0), custo_preempcao=0.5)
    assert fins(r)[1] == 3.5
    assert fins(r)[0] == 11.5
    assert abs(r.tempo_ocupado - 11.5) < 1e-12


def test_media_nao_interrompe_quando_so_critico_pode():
    trace = [spec(0, 0.0, 10.0, 0), spec(1, 2.0, 1.0, 2)]
    r = simular(trace, PreemptivaComAging(alfa=0.0, classe_minima_preempcao=3))
    assert r.total_preempcoes == 0
    r2 = simular(trace, PreemptivaComAging(alfa=0.0, classe_minima_preempcao=2))
    assert r2.total_preempcoes == 1


def test_aging_evita_inanicao_do_estavel():
    # Um estável chega em t=0 junto com um fluxo de tarefas médias que chegam
    # mais rápido do que o servidor processa (uma a cada 0,5 s, cada uma com
    # 1 s de serviço), formando uma fila de médias que dura até t≈302.
    # Sem aging, o estável só é atendido quando as médias acabam; com aging,
    # ele sobe de prioridade até o teto e, como é mais antigo, passa à frente.
    trace = [spec(0, 0.0, 1.0, 2), spec(1, 0.0, 1.0, 0)]
    trace += [spec(i, 0.5 * (i - 1), 1.0, 2) for i in range(2, 302)]
    sem = fins(simular(trace, PrioridadeNaoPreemptiva()))[1]
    com = fins(simular(trace, PreemptivaComAging(alfa=0.1, teto=2.9)))[1]
    assert sem > 300
    # Com alfa = 0,1 nível/s, o estável chega ao teto (2,9) em 29 s.
    assert com < 40


def test_estavel_envelhecido_nunca_passa_a_frente_de_critico():
    # Estável esperou 1000 s (prioridade no teto 2,9); crítico chega depois.
    # Quando o servidor libera, o crítico deve ser escolhido primeiro.
    trace = [spec(0, 0.0, 1000.0, 2),   # ocupa o servidor
             spec(1, 0.0, 1.0, 0),       # estável, envelhece até o teto
             spec(2, 500.0, 1.0, 3)]     # crítico
    pol = PreemptivaComAging(alfa=1.0, teto=2.9, classe_minima_preempcao=4)  # sem preempção
    r = fins(simular(trace, pol))
    assert r[2] < r[1]


def test_estavel_no_teto_so_e_interrompido_por_critico():
    # Estável começa depois de envelhecer até o teto; uma média chega e NÃO o
    # interrompe (2 < 2,9), mas um crítico sim.
    trace = [spec(0, 0.0, 100.0, 3), spec(1, 0.0, 50.0, 0),
             spec(2, 120.0, 1.0, 2), spec(3, 130.0, 1.0, 3)]
    r = simular(trace, PreemptivaComAging(alfa=0.1, teto=2.9, classe_minima_preempcao=2))
    t = {x.id: x for x in r.tarefas}
    assert t[1].preempcoes == 1        # só o crítico (id 3) o interrompeu
    assert t[3].fim == 131.0


def test_teto_por_classe():
    pol = PreemptivaComAging(alfa=1.0, teto={0: 1.9, 1: 2.4})
    estavel = TarefaSpec(0, 0, 0.0, 1.0, 0, None, "t")
    from fogsched.model import Tarefa
    assert pol.prioridade_efetiva(Tarefa.de_spec(estavel), 100.0) == 1.9
    media = TarefaSpec(1, 1, 0.0, 1.0, 2, None, "t")
    assert pol.prioridade_efetiva(Tarefa.de_spec(media), 100.0) == 2.9   # padrão
