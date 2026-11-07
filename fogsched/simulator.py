"""Simulador de eventos discretos de um nó fog com um servidor.

Eventos: chegada e término. Preempção do tipo retomar (preemptive-resume),
avaliada nas chegadas. Cada preempção custa um tempo fixo de troca de contexto,
pago pelo processador antes de a tarefa que preemptou começar a executar."""

import math

from .model import ResultadoSimulacao, Tarefa

# Tolerância numérica para comparar números decimais (erros de arredondamento).
EPS = 1e-12


def simular(trace: list, politica, custo_preempcao: float = 0.0,
            registrar_segmentos: bool = False) -> ResultadoSimulacao:
    """Executa uma simulação completa.

    Parâmetros
    ----------
    trace: lista de TarefaSpec ordenada por chegada (gerada por workload.py).
    politica: objeto de policies.py (FIFO, Proposta etc.). Deve estar vazio.
    custo_preempcao: duração da troca de contexto a cada interrupção; atrasa o
        início da tarefa que preemptou.
    registrar_segmentos: se True, guarda cada trecho de execução (para testes).

    As chegadas acontecem até o fim do trace; depois disso o simulador continua
    até TODAS as tarefas terminarem ("esvaziamento"). Assim toda tarefa tem uma
    latência definida, inclusive as que sofreram inanição.
    """
    tarefas = [Tarefa.de_spec(s) for s in trace]   # cópias de trabalho
    n = len(tarefas)

    i = 0                    # índice da próxima chegada
    agora = 0.0              # relógio da simulação
    atual = None             # tarefa no servidor (None = servidor livre)
    inicio_atual = 0.0       # quando o trecho atual de execução começou
    tempo_ocupado = 0.0      # soma do tempo em que o servidor trabalhou
    total_preempcoes = 0
    segmentos = []

    def despachar(atraso: float = 0.0):
        """Se o servidor está livre e há fila, coloca a próxima tarefa para rodar.

        atraso: tempo de troca de contexto antes de a tarefa começar (preempção)."""
        nonlocal atual, inicio_atual
        if atual is None and len(politica) > 0:
            atual = politica.retirar(agora)
            inicio_atual = agora + atraso
            if atual.inicio_primeiro is None:
                atual.inicio_primeiro = inicio_atual

    while i < n or atual is not None or len(politica) > 0:
        prox_chegada = tarefas[i].chegada if i < n else math.inf
        prox_termino = inicio_atual + atual.restante if atual is not None else math.inf

        if prox_termino <= prox_chegada:
            # ---------------- EVENTO: TÉRMINO ----------------
            # Regra de desempate: se término e chegada coincidem, o término é
            # tratado primeiro (a tarefa já tinha concluído o trabalho).
            agora = prox_termino
            tempo_ocupado += atual.restante
            if registrar_segmentos:
                segmentos.append((inicio_atual, agora, atual.id))
            atual.restante = 0.0
            atual.fim = agora            # aqui o alerta seria disparado
            atual = None
            despachar()
        else:
            # ---------------- EVENTO: CHEGADA ----------------
            agora = prox_chegada
            nova = tarefas[i]
            i += 1
            politica.inserir(nova, agora)

            if atual is None:
                despachar()
            elif politica.deve_preemptar(nova, atual, agora):
                # Interrompe a tarefa em execução. Se ela ainda estava na troca de
                # contexto de uma preempção anterior (inicio_atual > agora), a parte
                # da troca que não chegou a ocorrer é devolvida.
                executado = agora - inicio_atual
                if executado < 0.0:
                    tempo_ocupado += executado
                    executado = 0.0
                tempo_ocupado += executado
                if registrar_segmentos and executado > EPS:
                    segmentos.append((inicio_atual, agora, atual.id))
                atual.restante = max(0.0, atual.restante - executado)
                atual.preempcoes += 1
                total_preempcoes += 1
                politica.inserir(atual, agora)   # volta para a fila
                atual = None
                # A troca de contexto ocupa o processador antes da próxima tarefa.
                tempo_ocupado += custo_preempcao
                despachar(atraso=custo_preempcao)  # escolhe a mais prioritária

    return ResultadoSimulacao(tarefas=tarefas, tempo_ocupado=tempo_ocupado,
                              total_preempcoes=total_preempcoes,
                              custo_preempcao=custo_preempcao,
                              tempo_final=agora, segmentos=segmentos)
