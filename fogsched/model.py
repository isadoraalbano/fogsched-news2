"""Estruturas de dados da tarefa: TarefaSpec (imutável, compartilhada entre
políticas) e Tarefa (cópia de trabalho usada em cada simulação)."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TarefaSpec:
    """Descrição imutável de uma tarefa (gerada pelo módulo de carga)."""

    id: int                     # identificador único da tarefa
    paciente: int               # qual paciente enviou a amostra
    chegada: float              # instante de chegada ao nó fog (segundos)
    servico: float              # tempo de processamento necessário (segundos)
    classe: int                 # classe de prioridade (0 a 3), vinda do NEWS2
    news2: Optional[int]        # escore NEWS2 total (None no modo de validação)
    estado_paciente: str        # estado "real" do paciente no gerador
                                # (estavel/deteriorando/critico), só para análise


@dataclass
class Tarefa:
    """Cópia mutável de uma tarefa, usada durante UMA simulação."""

    spec: TarefaSpec
    restante: float                      # quanto de serviço ainda falta
    inicio_primeiro: Optional[float] = None  # quando começou a ser atendida
    fim: Optional[float] = None          # quando terminou (alerta disparado)
    preempcoes: int = 0                  # quantas vezes foi interrompida
    seq: int = 0                         # contador de desempate nas filas

    # Atalhos para não precisar escrever tarefa.spec.xxx o tempo todo.
    @property
    def id(self) -> int:
        return self.spec.id

    @property
    def chegada(self) -> float:
        return self.spec.chegada

    @property
    def classe(self) -> int:
        return self.spec.classe

    @property
    def servico(self) -> float:
        return self.spec.servico

    @classmethod
    def de_spec(cls, spec: TarefaSpec) -> "Tarefa":
        """Cria uma cópia de trabalho a partir da descrição imutável."""
        return cls(spec=spec, restante=spec.servico)

    # Métricas derivadas, calculadas depois que a tarefa termina.
    @property
    def latencia(self) -> float:
        """Tempo total no sistema: da chegada até o disparo do alerta."""
        return self.fim - self.chegada

    @property
    def espera(self) -> float:
        """Tempo em que a tarefa ficou esperando (latência menos serviço).

        Inclui o tempo extra gasto com trocas de contexto das preempções.
        """
        return self.latencia - self.servico


@dataclass
class ResultadoSimulacao:
    """Tudo o que uma simulação devolve para o cálculo de métricas."""

    tarefas: list                 # lista de Tarefa (todas terminadas)
    tempo_ocupado: float          # tempo total em que o servidor trabalhou
    total_preempcoes: int         # quantas preempções ocorreram
    custo_preempcao: float        # custo (s) cobrado a cada preempção
    tempo_final: float            # instante em que a última tarefa terminou
    segmentos: list = field(default_factory=list)
    # 'segmentos' (opcional) registra cada trecho de execução como
    # (inicio, fim, id_tarefa). Serve para os testes verificarem que o
    # servidor nunca executa duas tarefas ao mesmo tempo.
