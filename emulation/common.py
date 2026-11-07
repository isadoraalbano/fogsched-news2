"""Tópicos MQTT, formato das mensagens e transportes (MQTT real e local, para testes)."""

import heapq
import itertools
import json
import random
import threading
import time
import uuid

TOPICO_AMOSTRAS = "hospital/amostras"
TOPICO_ALERTAS = "hospital/alertas"
TOPICO_CONTROLE = "hospital/controle"
TOPICO_RESPOSTA = "hospital/controle/resposta"

# QoS (qualidade de serviço) do MQTT:
#   0 = "no máximo uma vez" (pode perder);
#   1 = "pelo menos uma vez" (o broker confirma o recebimento).
# Usamos 1 em tudo: um alerta perdido seria grave e invalidaria a medição.
QOS = 1


def codificar(dados: dict) -> bytes:
    """dict -> bytes (JSON). É o que viaja pela rede."""
    return json.dumps(dados, separators=(",", ":")).encode("utf-8")


def decodificar(carga: bytes) -> dict:
    """bytes (JSON) -> dict."""
    return json.loads(carga.decode("utf-8"))


# ---------------------------------------------------------------------------
# Transporte real: MQTT com paho-mqtt
# ---------------------------------------------------------------------------
class TransporteMQTT:
    """Cliente MQTT. Cada mensagem recebida chama funcao(topico, dados).

    A paho-mqtt cria uma THREAD própria de rede (loop_start): ela fica
    lendo o socket e chama as funções de recebimento. Por isso as funções
    registradas em 'assinar' rodam nessa thread, não na principal.
    """

    def __init__(self, host: str, porta: int = 1883, nome: str = "cliente"):
        try:
            import paho.mqtt.client as mqtt   # importado aqui: só quem usa MQTT precisa da biblioteca
        except ImportError as erro:
            raise SystemExit("Biblioteca paho-mqtt não encontrada. Instale com:\n"
                             "  Raspberry Pi:  sudo apt install python3-paho-mqtt\n"
                             "  Notebook:      pip install paho-mqtt") from erro
        self._mqtt = mqtt
        self.host, self.porta = host, porta
        client_id = f"{nome}-{uuid.uuid4().hex[:6]}"
        # A versão 2 da paho exige escolher a "versão da API de callbacks";
        # a versão 1 não conhece esse parâmetro. Tentamos a 2 e caímos na 1.
        try:
            self._cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:
            self._cliente = mqtt.Client(client_id=client_id)
        # Permite muitas mensagens "em voo" (enviadas e ainda não confirmadas);
        # o padrão (20) atrasaria os envios em rajadas.
        self._cliente.max_inflight_messages_set(1000)
        self._assinaturas = {}
        self._conectado = threading.Event()
        self._cliente.on_connect = self._ao_conectar
        self._cliente.on_message = self._ao_receber

    def _ao_conectar(self, cliente, userdata, flags, *resto):
        # (*resto absorve a diferença de assinatura entre paho 1.x e 2.x.)
        # Assinar aqui garante que, se a conexão cair e voltar, as
        # assinaturas são refeitas automaticamente.
        for topico in self._assinaturas:
            cliente.subscribe(topico, qos=QOS)
        self._conectado.set()

    def _ao_receber(self, cliente, userdata, msg):
        funcao = self._assinaturas.get(msg.topic)
        if funcao is not None:
            funcao(msg.topic, decodificar(msg.payload))

    def assinar(self, topico: str, funcao) -> None:
        self._assinaturas[topico] = funcao
        if self._conectado.is_set():
            self._cliente.subscribe(topico, qos=QOS)

    def conectar(self, timeout: float = 10.0) -> None:
        self._cliente.connect(self.host, self.porta, keepalive=30)
        self._cliente.loop_start()   # inicia a thread de rede
        if not self._conectado.wait(timeout):
            raise ConnectionError(f"não conectou ao broker {self.host}:{self.porta}")
        time.sleep(0.2)              # dá tempo de as assinaturas serem confirmadas

    def publicar(self, topico: str, dados: dict) -> None:
        self._cliente.publish(topico, codificar(dados), qos=QOS)

    def desconectar(self) -> None:
        self._cliente.loop_stop()
        self._cliente.disconnect()


# ---------------------------------------------------------------------------
# Transporte de teste: "broker" dentro do próprio programa
# ---------------------------------------------------------------------------
class BrokerLocal:
    """Imita um broker: entrega cada mensagem aos assinantes após um atraso.

    O atraso (em segundos) é sorteado entre atraso_min e atraso_max, imitando
    o tempo de rede. Uma thread de entrega faz o papel da thread de rede da
    paho. As mensagens passam por JSON (codificar/decodificar), como no MQTT.
    """

    def __init__(self, atraso_min: float = 0.001, atraso_max: float = 0.003, semente: int = 1):
        self.atraso_min, self.atraso_max = atraso_min, atraso_max
        self._rng = random.Random(semente)
        self._assinantes = {}              # topico -> lista de funções
        self._pendentes = []               # heap: (instante_entrega, ordem, topico, bytes)
        self._ordem = itertools.count()
        self._ultima = {}                  # topico -> instante da última entrega agendada
        self._cond = threading.Condition()
        self._ativo = True
        self._thread = threading.Thread(target=self._entregar, daemon=True, name="broker-local")
        self._thread.start()

    def assinar(self, topico, funcao):
        with self._cond:
            self._assinantes.setdefault(topico, []).append(funcao)

    def publicar(self, topico, dados):
        atraso = self._rng.uniform(self.atraso_min, self.atraso_max)
        with self._cond:
            # Mantém a ordem por tópico (como o MQTT com QoS 1 na prática):
            # uma mensagem nunca é entregue antes de uma anterior do mesmo envio.
            entrega = max(time.perf_counter() + atraso, self._ultima.get(topico, 0.0))
            self._ultima[topico] = entrega
            heapq.heappush(self._pendentes, (entrega, next(self._ordem), topico, codificar(dados)))
            self._cond.notify()

    def _entregar(self):
        while True:
            with self._cond:
                while self._ativo and (not self._pendentes or
                                       self._pendentes[0][0] > time.perf_counter()):
                    espera = (self._pendentes[0][0] - time.perf_counter()) if self._pendentes else None
                    self._cond.wait(timeout=espera)
                if not self._ativo:
                    return
                _, _, topico, carga = heapq.heappop(self._pendentes)
                funcoes = list(self._assinantes.get(topico, []))
            for funcao in funcoes:
                funcao(topico, decodificar(carga))

    def fechar(self):
        with self._cond:
            self._ativo = False
            self._cond.notify()


class TransporteLocal:
    """Mesma interface do TransporteMQTT, mas usando um BrokerLocal."""

    def __init__(self, broker: BrokerLocal):
        self.broker = broker

    def conectar(self, timeout: float = 10.0):
        pass

    def assinar(self, topico, funcao):
        self.broker.assinar(topico, funcao)

    def publicar(self, topico, dados):
        self.broker.publicar(topico, dados)

    def desconectar(self):
        pass
