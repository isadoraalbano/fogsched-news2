"""Políticas de escalonamento: FIFO, Prioridade NP, Prioridade P, Proposta
(preempção + envelhecimento limitado por classe).

Interface comum: inserir(), retirar() e deve_preemptar()."""

import heapq
import itertools
import math
from abc import ABC, abstractmethod

from .news2 import Classe

N_CLASSES = 4


class Politica(ABC):
    """Interface comum a todas as políticas."""

    nome = "base"
    preemptiva = False

    def __init__(self):
        self._contador = itertools.count()  # gera 0, 1, 2, ... para desempate

    @abstractmethod
    def inserir(self, tarefa, agora: float) -> None:
        """Coloca uma tarefa na fila (chegada nova ou tarefa interrompida)."""

    @abstractmethod
    def retirar(self, agora: float):
        """Remove e devolve a próxima tarefa a ser executada."""

    @abstractmethod
    def __len__(self) -> int:
        """Quantas tarefas estão esperando."""

    def deve_preemptar(self, chegando, executando, agora: float) -> bool:
        """Por padrão, nenhuma preempção (políticas não preemptivas)."""
        return False


# ---------------------------------------------------------------------------
# 1) FIFO — First In, First Out (primeiro a chegar, primeiro a ser atendido)
# ---------------------------------------------------------------------------
class FIFO(Politica):
    nome = "FIFO"

    def __init__(self):
        super().__init__()
        self._heap = []

    def inserir(self, tarefa, agora):
        # A chave é o instante de chegada: quem chegou antes sai antes.
        heapq.heappush(self._heap, (tarefa.chegada, next(self._contador), tarefa))

    def retirar(self, agora):
        return heapq.heappop(self._heap)[2]

    def __len__(self):
        return len(self._heap)


# ---------------------------------------------------------------------------
# Base comum às políticas por classe: UMA fila (heap) por classe NEWS2,
# cada uma ordenada por instante de chegada (mais antiga primeiro).
# ---------------------------------------------------------------------------
class _FilasPorClasse(Politica):

    def __init__(self):
        super().__init__()
        self._filas = [[] for _ in range(N_CLASSES)]
        self._n = 0

    def inserir(self, tarefa, agora):
        # Uma tarefa interrompida volta com sua chegada ORIGINAL como chave,
        # então retoma à frente das tarefas mais novas da mesma classe.
        heapq.heappush(self._filas[tarefa.classe], (tarefa.chegada, next(self._contador), tarefa))
        self._n += 1

    def _retirar_da_classe(self, classe):
        self._n -= 1
        return heapq.heappop(self._filas[classe])[2]

    def __len__(self):
        return self._n


# ---------------------------------------------------------------------------
# 2) Prioridade Não Preemptiva
# ---------------------------------------------------------------------------
class PrioridadeNaoPreemptiva(_FilasPorClasse):
    """Atende a maior classe disponível; empate pela ordem de chegada.

    Se um paciente crítico chega enquanto um estável está sendo processado,
    o crítico ESPERA o estável terminar (não há interrupção).
    """

    nome = "Prioridade NP"

    def retirar(self, agora):
        for classe in range(N_CLASSES - 1, -1, -1):  # de 3 (crítica) até 0
            if self._filas[classe]:
                return self._retirar_da_classe(classe)
        raise IndexError("fila vazia")


# ---------------------------------------------------------------------------
# 3) Prioridade Preemptiva (sem aging) — usada como ABLAÇÃO
# ---------------------------------------------------------------------------
class PrioridadePreemptiva(PrioridadeNaoPreemptiva):
    """Prioridade por classe com preempção, sem envelhecimento (ablação).

    classe_minima_preempcao: classe mínima de uma chegada para interromper (3 = só críticas).
    max_preempcoes: limite de interrupções por tarefa (None = sem limite).
    """

    nome = "Prioridade P"
    preemptiva = True

    def __init__(self, classe_minima_preempcao: int = int(Classe.CRITICA),
                 max_preempcoes=None):
        super().__init__()
        self.classe_minima_preempcao = classe_minima_preempcao
        self.max_preempcoes = max_preempcoes

    def deve_preemptar(self, chegando, executando, agora):
        if chegando.classe < self.classe_minima_preempcao:
            return False
        if self.max_preempcoes is not None and executando.preempcoes >= self.max_preempcoes:
            return False
        return chegando.classe > executando.classe


# ---------------------------------------------------------------------------
# 4) PROPOSTA: Preemptiva com Envelhecimento (aging) Limitado
# ---------------------------------------------------------------------------
class PreemptivaComAging(_FilasPorClasse):
    """Algoritmo proposto: preempção por críticos + envelhecimento limitado.

    p(t) = min(classe + alfa * (agora - chegada), teto[classe]); críticos têm p = 3.
    Com tetos abaixo de 3, uma amostra envelhecida nunca ultrapassa um alerta
    crítico. Como a prioridade depende da idade, a amostra mais antiga de cada
    classe é sempre a de maior p, e basta comparar o topo das quatro filas.

    Uma chegada preempta se sua classe >= classe_minima_preempcao e seu p for
    maior que o da tarefa em execução + margem.
    Casos particulares: alfa = 0 sem preempção equivale à Prioridade NP;
    alfa = 0 com preempção, à Prioridade P.
    """

    nome = "Proposta"

    def __init__(self, alfa: float, teto: float = 2.9,
                 classe_minima_preempcao: int = int(Classe.CRITICA),
                 margem: float = 0.0, max_preempcoes=None, preemptiva: bool = True):
        super().__init__()
        if alfa < 0:
            raise ValueError("alfa não pode ser negativo")
        self.alfa = alfa
        # O teto pode ser um único número (vale para todas as classes não
        # críticas) ou um dicionário {classe: teto}, permitindo, por exemplo,
        # que estáveis subam no máximo até 1,9 e médias até 2,9.
        if isinstance(teto, dict):
            # Classes não informadas ficam com o teto padrão 2,9.
            self.teto = {c: 2.9 for c in range(int(Classe.CRITICA))}
            self.teto.update({int(k): float(v) for k, v in teto.items()})
        else:
            self.teto = {c: float(teto) for c in range(int(Classe.CRITICA))}
        for classe, valor in self.teto.items():
            # Regra central da proposta: nenhum teto pode alcançar a classe
            # crítica, e nenhuma classe pode ter teto abaixo da própria base.
            if not (classe <= valor < int(Classe.CRITICA)):
                raise ValueError(f"teto da classe {classe} deve estar em [{classe}, 3)")
        self.classe_minima_preempcao = classe_minima_preempcao
        self.margem = margem
        self.max_preempcoes = max_preempcoes
        self.preemptiva = preemptiva

    def prioridade_efetiva(self, tarefa, agora: float) -> float:
        """Calcula p_efetiva da tarefa no instante 'agora'."""
        if tarefa.classe == int(Classe.CRITICA):
            return float(Classe.CRITICA)
        idade = agora - tarefa.chegada
        return min(tarefa.classe + self.alfa * idade, self.teto[tarefa.classe])

    def retirar(self, agora):
        melhor_classe = None
        melhor_chave = None
        for classe in range(N_CLASSES):
            fila = self._filas[classe]
            if not fila:
                continue
            chegada, seq, tarefa = fila[0]  # a mais antiga desta classe
            p = self.prioridade_efetiva(tarefa, agora)
            # Critério: maior prioridade efetiva; empate -> chegada mais antiga;
            # novo empate -> ordem de inserção. Usamos -p para "maior primeiro".
            chave = (-p, chegada, seq)
            if melhor_chave is None or chave < melhor_chave:
                melhor_chave, melhor_classe = chave, classe
        if melhor_classe is None:
            raise IndexError("fila vazia")
        return self._retirar_da_classe(melhor_classe)

    def deve_preemptar(self, chegando, executando, agora):
        if not self.preemptiva:
            return False
        if chegando.classe < self.classe_minima_preempcao:
            return False
        if self.max_preempcoes is not None and executando.preempcoes >= self.max_preempcoes:
            return False
        p_chegando = self.prioridade_efetiva(chegando, agora)
        p_executando = self.prioridade_efetiva(executando, agora)
        return p_chegando > p_executando + self.margem


# ---------------------------------------------------------------------------
# Fábrica: cria a política a partir de um dicionário de configuração (YAML)
# ---------------------------------------------------------------------------
def criar_politica(cfg: dict, servico_medio: float) -> Politica:
    """Cria uma política a partir da configuração do experimento.

    O parâmetro de aging é informado no YAML como 'tempo_por_nivel_rel':
    quantos TEMPOS MÉDIOS DE SERVIÇO uma amostra leva para subir um nível de
    prioridade. Assim o aging escala junto com a carga. Convertemos para
    alfa (níveis por segundo) com:  alfa = 1 / (tempo_por_nivel_rel × E[S]).
    """
    tipo = cfg["tipo"]
    if tipo == "fifo":
        pol = FIFO()
    elif tipo == "prioridade_np":
        pol = PrioridadeNaoPreemptiva()
    elif tipo == "prioridade_p":
        pol = PrioridadePreemptiva(cfg.get("classe_minima_preempcao", 3),
                                   cfg.get("max_preempcoes"))
    elif tipo == "proposta":
        tpn = cfg.get("tempo_por_nivel_rel", 50.0)
        alfa = 0.0 if (tpn is None or math.isinf(float(tpn))) else 1.0 / (float(tpn) * servico_medio)
        pol = PreemptivaComAging(alfa=alfa, teto=cfg.get("teto", 2.9),
                                 classe_minima_preempcao=cfg.get("classe_minima_preempcao", 3),
                                 margem=cfg.get("margem", 0.0),
                                 max_preempcoes=cfg.get("max_preempcoes"),
                                 preemptiva=cfg.get("preemptiva", True))
    else:
        raise ValueError(f"tipo de política desconhecido: {tipo}")
    pol.nome = cfg.get("nome", pol.nome)
    return pol
