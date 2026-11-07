"""Nó fog executado no Raspberry Pi: recebe amostras, calcula o NEWS2, escalona
com as políticas de fogsched.policies, executa o estágio 2 em pedaços
(preempção cooperativa) e publica os alertas.

    taskset -c 2,3 python3 -m emulation.fog_node --broker localhost

No Pi, a rede e o trabalho rodam em DOIS PROCESSOS, um em cada núcleo. Em um
só processo, o GIL do Python faz a thread de trabalho (sempre ocupada sob
carga) atrasar a thread de rede, e as amostras ficam retidas no socket em vez
de entrar na fila do escalonador. Com dois processos, a chegada é registrada
sem depender do trabalho em andamento. Os instantes vêm de time.perf_counter,
que no Linux é o mesmo relógio (CLOCK_MONOTONIC) para os dois processos."""

import argparse
import multiprocessing
import os
import platform
import queue
import threading
import time
from collections import defaultdict, deque

from fogsched.model import Tarefa, TarefaSpec
from fogsched.news2 import SinaisVitais, calcular_news2, classificar
from fogsched.policies import criar_politica

from .common import (TOPICO_ALERTAS, TOPICO_AMOSTRAS, TOPICO_CONTROLE, TOPICO_RESPOSTA,
                    TransporteMQTT)
from .stage2 import calibrar, executar_em_pedacos, janela_para_matriz

TAMANHO_JANELA = 60   # amostras por paciente guardadas para o estágio 2


class LadoRede:
    """Recepção: marca a chegada, executa o estágio 1 (NEWS2) e entrega à caixa."""

    def __init__(self, transporte, entregar):
        self.transporte = transporte
        self.entregar = entregar            # função que põe um item na caixa do trabalho

    def ao_receber_amostra(self, topico, msg):
        chegada = time.perf_counter()      # primeira coisa: marcar o instante
        sv = SinaisVitais(**msg["sinais"])
        resultado = calcular_news2(sv)     # ESTÁGIO 1
        classe = int(classificar(resultado))
        self.entregar(("amostra", chegada, msg, resultado.total, classe))

    def ao_receber_controle(self, topico, msg):
        # Comandos também passam pela caixa: assim só o trabalho mexe no estado
        # do escalonador e não é preciso usar travas (locks).
        if msg.get("comando") == "ping":   # ping responde na hora, para medir a rede
            self.transporte.publicar(TOPICO_RESPOSTA, {"tipo": "pong", "t": msg["t"]})
            return
        self.entregar(("controle", time.perf_counter(), msg, None, None))

    def assinar(self):
        self.transporte.assinar(TOPICO_AMOSTRAS, self.ao_receber_amostra)
        self.transporte.assinar(TOPICO_CONTROLE, self.ao_receber_controle)


class CaixaPipe:
    """Caixa de entrada lida de um Pipe (versão em dois processos).

    O trabalho consulta a caixa a cada pedaço do estágio 2. Para não pagar uma
    chamada ao sistema operacional a cada consulta, a rede incrementa um
    contador em memória compartilhada a cada envio; o Pipe só é lido quando
    o contador mudou."""

    def __init__(self, conexao, contador):
        self.conexao = conexao
        self.contador = contador            # RawValue: amostras/comandos já enviados
        self.lidos = 0

    def get(self, timeout):
        if self.conexao.poll(timeout):
            self.lidos += 1
            return self.conexao.recv()
        raise queue.Empty

    def get_nowait(self):
        if self.contador.value == self.lidos:   # nada novo: sem chamada ao sistema
            raise queue.Empty
        self.lidos += 1
        return self.conexao.recv()


class NoFog:
    """Escalonador e estágio 2. Em um processo só (testes e modo local), usa o
    próprio transporte; no Pi, recebe a caixa e a função de publicar do
    processo de rede (ver executar_em_dois_processos)."""

    def __init__(self, transporte, verbose: bool = True, caixa=None, publicar=None):
        self.transporte = transporte
        self.verbose = verbose
        self.caixa = caixa if caixa is not None else queue.SimpleQueue()
        self.publicar = publicar if publicar is not None else transporte.publicar
        self.parar_evento = threading.Event()
        self.calibracao = calibrar()
        self._resetar(None, "nenhuma", 1.0)

    # ------------------------------------------------------------------
    # Estado de uma execução
    # ------------------------------------------------------------------
    def _resetar(self, cfg_politica, execucao, servico_medio):
        self.execucao = execucao
        self.politica = criar_politica(cfg_politica, servico_medio) if cfg_politica else None
        self.atual = None                   # tarefa no "servidor" (None = livre)
        self.historico = defaultdict(lambda: deque(maxlen=TAMANHO_JANELA))
        self.news2_anterior = {}
        self.t0 = time.perf_counter()       # zero do relógio desta execução
        self.tempo_ocupado = 0.0
        self.total_preempcoes = 0
        self.concluidas = 0
        self.vistas = set()                 # ids já recebidos (o QoS 1 pode duplicar)
        self.duplicadas = 0

    def agora(self) -> float:
        return time.perf_counter() - self.t0

    def log(self, *args):
        if self.verbose:
            print(time.strftime("%H:%M:%S"), *args, flush=True)

    # ------------------------------------------------------------------
    # TRABALHO
    # ------------------------------------------------------------------
    def _nova_tarefa(self, chegada, msg, news2, classe):
        spec = TarefaSpec(id=msg["id"], paciente=msg["paciente"], chegada=chegada,
                          servico=msg["servico"], classe=classe, news2=news2,
                          estado_paciente=msg.get("estado", "?"))
        tarefa = Tarefa.de_spec(spec)
        # Atributos extras usados só na emulação:
        hist = self.historico[spec.paciente]
        hist.append(msg["sinais"])
        tarefa.janela = janela_para_matriz(hist)
        tarefa.news2_anterior = self.news2_anterior.get(spec.paciente, news2)
        self.news2_anterior[spec.paciente] = news2
        tarefa.orcamento = msg["servico"]   # tempo de CPU a consumir
        tarefa.executado = 0.0
        tarefa.base = 0.0                   # executado até a última interrupção
        tarefa.retomada = None              # quando voltou ao processador (perf_counter)
        tarefa.visto = self.agora()         # quando o processo de trabalho a viu
        tarefa.execucao = None              # o gerador é criado no 1º despacho
        tarefa.t_plan = msg["t_plan"]
        tarefa.classe_esperada = msg.get("classe_esperada")
        return tarefa

    def _despachar(self):
        """Servidor livre e fila com tarefas: escolhe a próxima (a política decide)."""
        if self.atual is None and self.politica is not None and len(self.politica) > 0:
            agora = self.agora()
            self.atual = self.politica.retirar(agora)
            if self.atual.inicio_primeiro is None:
                self.atual.inicio_primeiro = agora
            if self.atual.execucao is None:
                self.atual.execucao = executar_em_pedacos(self.atual)
            self.atual.retomada = time.perf_counter()

    def _tratar_amostra(self, chegada, msg, news2, classe):
        if self.politica is None:
            return                          # chegou fora de uma execução: ignora
        if msg["id"] in self.vistas:        # entrega repetida pelo QoS 1: ignora
            self.duplicadas += 1
            return
        self.vistas.add(msg["id"])
        nova = self._nova_tarefa(chegada, msg, news2, classe)
        agora = self.agora()
        self.politica.inserir(nova, agora)
        if self.atual is None:
            self._despachar()
        elif self.politica.deve_preemptar(nova, self.atual, agora):
            # PREEMPÇÃO: a tarefa atual volta para a fila. O gerador dela
            # NÃO é descartado: guarda o progresso para retomar depois.
            self.atual.preempcoes += 1
            self.total_preempcoes += 1
            self.atual.base = self.atual.executado
            self.politica.inserir(self.atual, agora)
            self.atual = None
            self._despachar()

    def _tratar_controle(self, msg):
        cmd = msg.get("comando")
        if cmd == "preparar":
            ocupado = self.atual is not None or (self.politica is not None and len(self.politica) > 0)
            if ocupado and not msg.get("forcar", False):
                self.publicar(TOPICO_RESPOSTA, {"tipo": "ocupado",
                                                          "execucao": msg["execucao"]})
                return
            self._resetar(msg["politica"], msg["execucao"], msg["servico_medio"])
            self.log(f"preparado: {self.execucao} | política {self.politica.nome}")
            self.publicar(TOPICO_RESPOSTA, {
                "tipo": "pronto", "execucao": self.execucao, "politica": self.politica.nome,
                "calibracao": self.calibracao, "python": platform.python_version(),
                "plataforma": platform.platform()})
        elif cmd == "finalizar":
            self.log(f"finalizado: {self.execucao} | {self.concluidas} alertas | "
                     f"{self.total_preempcoes} preempções")
            self.publicar(TOPICO_RESPOSTA, {
                "tipo": "estatisticas", "execucao": self.execucao,
                "tempo_ocupado": self.tempo_ocupado, "duracao": self.agora(),
                "preempcoes": self.total_preempcoes, "concluidas": self.concluidas,
                "duplicadas": self.duplicadas,
                "pendentes": (len(self.politica) if self.politica else 0) + (self.atual is not None)})
            self.politica = None
        elif cmd == "desligar":
            self.parar_evento.set()

    def _esvaziar_caixa(self, bloquear: bool):
        try:
            item = self.caixa.get(timeout=0.05) if bloquear else self.caixa.get_nowait()
        except queue.Empty:
            return
        while True:
            tipo, chegada, msg, news2, classe = item
            if tipo == "amostra":           # chegada: perf_counter -> relógio da execução
                self._tratar_amostra(chegada - self.t0, msg, news2, classe)
            else:
                self._tratar_controle(msg)
            try:
                item = self.caixa.get_nowait()
            except queue.Empty:
                return

    def _concluir(self):
        t = self.atual
        t.fim = self.agora()               # instante do disparo do alerta (relógio do Pi)
        t.restante = 0.0
        self.tempo_ocupado += t.executado
        self.concluidas += 1
        self.atual = None
        self.publicar(TOPICO_ALERTAS, {
            "execucao": self.execucao, "id": t.id, "paciente": t.spec.paciente,
            "classe": t.classe, "news2": t.spec.news2,
            "classe_esperada": t.classe_esperada,
            "chegada_no": t.chegada, "visto_no": t.visto,
            "inicio_no": t.inicio_primeiro, "fim_no": t.fim,
            "executado": t.executado, "preempcoes": t.preempcoes,
            "alerta": t.resultado})

    def laco_de_trabalho(self):
        while not self.parar_evento.is_set():
            ocioso = self.atual is None and (self.politica is None or len(self.politica) == 0)
            self._esvaziar_caixa(bloquear=ocioso)   # 1. novidades (e preempção)
            self._despachar()                       # 2. servidor livre? pega a próxima
            if self.atual is None:
                continue
            try:
                next(self.atual.execucao)           # 3. executa UM pedaço
            except StopIteration:
                self._concluir()                    # 4. terminou: dispara o alerta
            except Exception as erro:               # erro no estágio 2: registra e segue,
                self.log(f"erro na amostra {self.atual.id}: {erro!r}")   # sem derrubar o nó
                self.atual.resultado = {"erro": repr(erro)}
                self._concluir()

    def iniciar(self, em_thread: bool = False):
        """Rede e trabalho no mesmo processo (testes e modo local, sem Pi)."""
        LadoRede(self.transporte, self.caixa.put).assinar()
        self.transporte.conectar()
        self.log(f"nó fog no ar | pedaço do estágio 2 ≈ {self.calibracao['pedaco_media_ms']:.3f} ms")
        if em_thread:
            th = threading.Thread(target=self.laco_de_trabalho, daemon=True, name="trabalho")
            th.start()
            return th
        try:
            self.laco_de_trabalho()
        except KeyboardInterrupt:
            pass
        finally:
            self.transporte.desconectar()


# ----------------------------------------------------------------------
# Dois processos (uso no Pi)
# ----------------------------------------------------------------------
def _fixar_nucleo(indice: int):
    """Prende o processo a um dos núcleos liberados pelo taskset (Linux)."""
    if hasattr(os, "sched_getaffinity"):
        nucleos = sorted(os.sched_getaffinity(0))
        if len(nucleos) >= 2:
            os.sched_setaffinity(0, {nucleos[indice]})


def _processo_trabalho(entrada, contador, saida, verbose):
    _fixar_nucleo(-1)                       # último núcleo liberado: só o trabalho
    no = NoFog(None, verbose=verbose, caixa=CaixaPipe(entrada, contador),
               publicar=lambda topico, msg: saida.send((topico, msg)))
    saida.send(("__calibracao__", no.calibracao))
    try:
        no.laco_de_trabalho()
    except KeyboardInterrupt:
        pass
    finally:
        saida.send(None)                    # avisa a rede para encerrar


def executar_em_dois_processos(transporte, verbose: bool = True, inicio=None, metodo=None):
    """Rede neste processo, trabalho num processo filho. 'inicio' (opcional)
    é um threading.Event sinalizado quando o nó está no ar."""
    if metodo is None:
        metodo = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    ctx = multiprocessing.get_context(metodo)
    amostras_rx, amostras_tx = ctx.Pipe(duplex=False)   # rede -> trabalho
    saida_rx, saida_tx = ctx.Pipe(duplex=False)         # trabalho -> rede (alertas)
    contador = ctx.RawValue("Q", 0)                     # itens já enviados ao trabalho
    trabalho = ctx.Process(target=_processo_trabalho,
                           args=(amostras_rx, contador, saida_tx, verbose),
                           name="fog-trabalho", daemon=True)
    trabalho.start()
    _fixar_nucleo(0)                        # primeiro núcleo liberado: rede
    _, calibracao = saida_rx.recv()

    trava = threading.Lock()                # o paho e o envio usam o Pipe em threads distintas
    def entregar(item):
        with trava:
            amostras_tx.send(item)          # primeiro o item, depois o contador:
            contador.value += 1             # quando o trabalho vê o contador, o item já está no Pipe

    LadoRede(transporte, entregar).assinar()
    transporte.conectar()
    if inicio is not None:
        inicio.set()
    if verbose:
        print(time.strftime("%H:%M:%S"), f"nó fog no ar (rede e trabalho em processos separados) | "
              f"pedaço do estágio 2 ≈ {calibracao['pedaco_media_ms']:.3f} ms", flush=True)
    try:
        while True:
            item = saida_rx.recv()          # alertas e respostas vindos do trabalho
            if item is None:
                break
            transporte.publicar(*item)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        transporte.desconectar()
        trabalho.terminate()


def main():
    ap = argparse.ArgumentParser(description="Escalonador do nó fog (Raspberry Pi).")
    ap.add_argument("--broker", default="localhost", help="endereço do broker MQTT")
    ap.add_argument("--porta", type=int, default=1883)
    ap.add_argument("--silencioso", action="store_true")
    a = ap.parse_args()
    executar_em_dois_processos(TransporteMQTT(a.broker, a.porta, nome="no-fog"),
                               verbose=not a.silencioso)


if __name__ == "__main__":
    main()
