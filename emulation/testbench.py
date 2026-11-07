"""Bancada executada no notebook: monitores virtuais e posto de enfermagem no mesmo
processo (um único relógio), conduzindo todas as execuções de um YAML.

    python -m emulation.testbench --config emulation/configs/short.yaml --broker <IP do Pi>"""

import argparse
import csv
import json
import os
import random
import statistics
import threading
import time

import yaml

from .common import (TOPICO_ALERTAS, TOPICO_AMOSTRAS, TOPICO_CONTROLE, TOPICO_RESPOSTA,
                    TransporteMQTT)
from .make_trace import config_carga, gerar_trace

COLUNAS_CSV = ["id", "paciente", "t_plan", "classe_esperada", "news2_esperado", "servico",
               "t_envio", "t_recebido", "classe", "news2", "chegada_no", "visto_no",
               "inicio_no", "fim_no", "executado", "preempcoes"]


def esperar_ate(instante: float, espera_ativa: bool) -> None:
    """Dorme até o instante (relógio perf_counter).

    time.sleep pode acordar alguns milissegundos atrasado (no Windows, até
    ~15 ms). Com espera_ativa, dorme até 2 ms antes e termina "girando" num
    laço, o que dá precisão de microssegundos à custa de um núcleo ocupado.
    """
    falta = instante - time.perf_counter()
    if espera_ativa:
        if falta > 0.002:
            time.sleep(falta - 0.002)
        while time.perf_counter() < instante:
            pass
    elif falta > 0:
        time.sleep(falta)


class Bancada:
    def __init__(self, transporte, espera_ativa: bool = True, verbose: bool = True):
        self.transporte = transporte
        self.espera_ativa = espera_ativa
        self.verbose = verbose
        self._trava = threading.Lock()
        self._alertas = {}                  # id -> (t_recebido, mensagem)
        self._respostas = []                # mensagens de controle recebidas
        self._novidade = threading.Condition(self._trava)
        self._execucao = None
        transporte.assinar(TOPICO_ALERTAS, self._ao_receber_alerta)
        transporte.assinar(TOPICO_RESPOSTA, self._ao_receber_resposta)
        transporte.conectar()

    def log(self, *args):
        if self.verbose:
            print(time.strftime("%H:%M:%S"), *args, flush=True)

    # ---------------- POSTO DE ENFERMAGEM (thread de rede) ----------------
    def _ao_receber_alerta(self, topico, msg):
        t = time.perf_counter()             # primeira coisa: marcar o instante
        with self._novidade:
            # Guarda só a primeira entrega de cada alerta (o QoS 1 pode duplicar).
            if msg.get("execucao") == self._execucao and msg["id"] not in self._alertas:
                self._alertas[msg["id"]] = (t, msg)
                self._novidade.notify_all()

    def _ao_receber_resposta(self, topico, msg):
        t = time.perf_counter()
        with self._novidade:
            self._respostas.append((t, msg))
            self._novidade.notify_all()

    def _esperar_resposta(self, condicao, timeout: float):
        limite = time.perf_counter() + timeout
        with self._novidade:
            while True:
                for i, (t, msg) in enumerate(self._respostas):
                    if condicao(msg):
                        self._respostas.pop(i)
                        return t, msg
                falta = limite - time.perf_counter()
                if falta <= 0:
                    return None, None
                self._novidade.wait(falta)

    # ---------------- Medição da rede "vazia" ----------------
    def medir_rtt(self, n: int = 30) -> dict:
        """Tempo de ida e volta (RTT) de uma mensagem pequena, sem carga."""
        rtts = []
        for _ in range(n):
            t = time.perf_counter()
            self.transporte.publicar(TOPICO_CONTROLE, {"comando": "ping", "t": t})
            t_volta, _ = self._esperar_resposta(lambda m, t=t: m.get("tipo") == "pong" and m.get("t") == t, 2.0)
            if t_volta is not None:
                rtts.append(t_volta - t)
            time.sleep(0.02)
        if not rtts:
            raise ConnectionError("o nó fog não respondeu ao ping: fog_node.py está rodando?")
        rtts.sort()
        return {"rtt_mediana_ms": 1e3 * statistics.median(rtts),
                "rtt_p95_ms": 1e3 * rtts[int(0.95 * (len(rtts) - 1))],
                "rtt_min_ms": 1e3 * rtts[0], "rtt_n": len(rtts)}

    # ---------------- Uma execução (uma política sobre um trace) ----------------
    def executar(self, nome: str, trace: dict, cfg_politica: dict, folga: float = 30.0):
        amostras = trace["amostras"]
        rede = self.medir_rtt()
        with self._novidade:
            self._execucao = nome
            self._alertas = {}
        for tentativa in range(30):
            self.transporte.publicar(TOPICO_CONTROLE, {
                "comando": "preparar", "execucao": nome, "politica": cfg_politica,
                "servico_medio": trace["servico_medio"], "forcar": tentativa >= 20})
            _, resp = self._esperar_resposta(
                lambda m: m.get("execucao") == nome and m.get("tipo") in ("pronto", "ocupado"), 5.0)
            if resp and resp["tipo"] == "pronto":
                break
            time.sleep(2.0)                 # o Pi ainda está esvaziando a fila anterior
        else:
            raise TimeoutError("o nó fog não ficou pronto")

        # MONITORES: uma thread reproduz o trace no tempo certo.
        envios = {}
        t0 = time.perf_counter() + 0.5      # meio segundo de folga para começar

        def monitores():
            for a in amostras:
                esperar_ate(t0 + a["t_plan"], self.espera_ativa)
                envios[a["id"]] = time.perf_counter() - t0
                self.transporte.publicar(TOPICO_AMOSTRAS, {
                    k: a[k] for k in ("id", "paciente", "t_plan", "servico",
                                      "classe_esperada", "estado", "sinais")})

        th = threading.Thread(target=monitores, name="monitores", daemon=True)
        th.start()
        th.join()
        duracao = trace["config"]["duracao"]
        # Espera todos os alertas. Em sobrecarga a fila demora a esvaziar,
        # então o limite cresce com a duração da execução.
        limite = time.perf_counter() + folga + 0.5 * duracao
        with self._novidade:
            while len(self._alertas) < len(amostras) and time.perf_counter() < limite:
                self._novidade.wait(1.0)
            alertas = dict(self._alertas)
            self._execucao = None
        self.transporte.publicar(TOPICO_CONTROLE, {"comando": "finalizar"})
        _, est = self._esperar_resposta(lambda m: m.get("tipo") == "estatisticas"
                                        and m.get("execucao") == nome, 10.0)

        linhas = []
        for a in amostras:
            linha = {"id": a["id"], "paciente": a["paciente"], "t_plan": a["t_plan"],
                     "classe_esperada": a["classe_esperada"], "news2_esperado": a["news2_esperado"],
                     "servico": a["servico"], "t_envio": envios.get(a["id"])}
            if a["id"] in alertas:
                t_rec, msg = alertas[a["id"]]
                linha["t_recebido"] = t_rec - t0
                for k in ("classe", "news2", "chegada_no", "visto_no", "inicio_no",
                          "fim_no", "executado", "preempcoes"):
                    linha[k] = msg[k]
            linhas.append(linha)
        perdidos = len(amostras) - len(alertas)
        self.log(f"{nome}: {len(alertas)}/{len(amostras)} alertas"
                 + (f" ({perdidos} NÃO chegaram)" if perdidos else "")
                 + f" | RTT mediana {rede['rtt_mediana_ms']:.1f} ms")
        return linhas, {"rede": rede, "no_fog": resp, "estatisticas": est, "perdidos": perdidos}


def gravar_csv(caminho: str, linhas: list) -> None:
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_CSV)
        w.writeheader()
        w.writerows(linhas)


def rodar_experimento(cfg: dict, bancada: Bancada, pasta_saida: str = None) -> str:
    """Executa todos os cenários × replicações × políticas de um YAML."""
    saida = pasta_saida or cfg["saida"]
    os.makedirs(os.path.join(saida, "runs"), exist_ok=True)
    os.makedirs(os.path.join(saida, "traces"), exist_ok=True)
    politicas = [p for p in cfg["politicas"] if p.get("padrao", True)]
    meta_caminho = os.path.join(saida, "metadata.json")
    metadados = {"config": cfg, "execucoes": {}}
    if os.path.exists(meta_caminho):        # permite continuar um experimento interrompido
        with open(meta_caminho, encoding="utf-8") as f:
            metadados["execucoes"] = json.load(f).get("execucoes", {})
    embaralhar = random.Random(cfg.get("semente_base", 2026))

    for cen in cfg["cenarios"]:
        carga = {**cfg["carga"], **cen.get("carga", {})}
        cc = config_carga(carga)
        for rep in range(cfg["replicacoes"]):
            semente = cfg.get("semente_base", 2026) + 1000 * rep
            trace = gerar_trace(cc, semente)
            nome_trace = f"{cen['nome']}_rep{rep:02d}"
            with open(os.path.join(saida, "traces", nome_trace + ".json"), "w", encoding="utf-8") as f:
                json.dump(trace, f)
            ordem = list(politicas)
            embaralhar.shuffle(ordem)
            for pol in ordem:
                nome = f"{nome_trace}__{pol['nome']}"
                arquivo = os.path.join(saida, "runs", nome.replace(" ", "_") + ".csv")
                if os.path.exists(arquivo) and nome in metadados["execucoes"]:
                    bancada.log(f"{nome}: já existe, pulando")
                    continue
                linhas, info = bancada.executar(nome, trace, pol)
                gravar_csv(arquivo, linhas)
                metadados["execucoes"][nome] = {
                    "cenario": cen["nome"], "parametro": cen.get("parametro", "utilizacao"),
                    "valor": cen.get("valor", cc.utilizacao), "replicacao": rep,
                    "politica": pol["nome"], "politica_cfg": pol, "trace": nome_trace + ".json",
                    "arquivo": os.path.basename(arquivo), **info}
                with open(meta_caminho, "w", encoding="utf-8") as f:
                    json.dump(metadados, f, indent=1, default=str)
                time.sleep(cfg.get("pausa_entre_execucoes", 3.0))
    return saida


def main():
    ap = argparse.ArgumentParser(description="Bancada de emulação (notebook).")
    ap.add_argument("--config", default="emulation/configs/short.yaml")
    ap.add_argument("--broker", help="IP do Raspberry Pi (sobrepõe o YAML)")
    ap.add_argument("--porta", type=int)
    ap.add_argument("--sem-espera-ativa", action="store_true",
                    help="não ocupar um núcleo do notebook para enviar no tempo exato")
    a = ap.parse_args()
    with open(a.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    host = a.broker or cfg["broker"]["host"]
    porta = a.porta or cfg["broker"].get("porta", 1883)
    bancada = Bancada(TransporteMQTT(host, porta, nome="bancada"),
                      espera_ativa=not a.sem_espera_ativa)
    total = len(cfg["cenarios"]) * cfg["replicacoes"] * len([p for p in cfg["politicas"]
                                                             if p.get("padrao", True)])
    dur = cfg["carga"]["duracao"]
    bancada.log(f"{total} execuções de ~{dur:.0f} s (+ esvaziamento): "
                f"cerca de {total * (dur + 20) / 3600:.1f} h")
    saida = rodar_experimento(cfg, bancada)
    bancada.log(f"pronto. Analise com:  python -m emulation.analyze {saida}")


if __name__ == "__main__":
    main()
