"""Diagnóstico da bancada (~1 min): broker, nó fog, preempção e mini-carga.

    python -m emulation.diagnostics --broker <IP do Pi>
    python -m emulation.diagnostics --local"""

import argparse
import sys
import threading
import time

import numpy as np

from fogsched.workload import ConfigCarga
from fogsched.policies import criar_politica
from fogsched.simulator import simular

from .testbench import Bancada
from .common import TransporteLocal, TransporteMQTT
from .make_trace import gerar_trace, trace_para_specs

TOPICO_DIAGNOSTICO = "hospital/diagnostico"
PROPOSTA = {"nome": "Proposta", "tipo": "proposta", "tempo_por_nivel_rel": 50,
            "teto": {0: 1.9, 1: 2.5, 2: 2.9}, "classe_minima_preempcao": 3}
NP = {"nome": "Prioridade NP", "tipo": "prioridade_np"}
FIFO = {"nome": "FIFO", "tipo": "fifo"}

NORMAL = dict(freq_respiratoria=16.0, spo2=97.0, oxigenio_suplementar=False,
              pa_sistolica=120.0, pulso=75.0, consciencia_alerta=True, temperatura=36.8)
GRAVE = dict(NORMAL, freq_respiratoria=26.0, spo2=90.0, oxigenio_suplementar=True)  # NEWS2 = 8


def ok(texto):
    print(f"  [ OK ]   {texto}", flush=True)


def falhou(texto, dicas=()):
    print(f"  [FALHOU] {texto}", flush=True)
    for d in dicas:
        print(f"           - {d}")
    print("\nDiagnóstico interrompido. Corrija o item acima e rode de novo.")
    sys.exit(1)


def titulo(n, texto):
    print(f"\n{n}. {texto}", flush=True)


# ---------------------------------------------------------------------------
def etapa_broker(transporte, recebido: threading.Event, local: bool):
    titulo(1, "Conexão com o broker MQTT")
    try:
        bancada = Bancada(transporte, espera_ativa=not local, verbose=False)
    except (ConnectionError, OSError) as erro:
        falhou(f"não conectou ao broker ({erro})", [
            "o IP passado em --broker é o que 'hostname -I' mostra no Pi?",
            "o notebook e o Pi estão na mesma rede? teste:  ping <IP do Pi>",
            "o Mosquitto está ativo? no Pi:  systemctl status mosquitto",
            "o Mosquitto aceita a rede? rode de novo no Pi:  sudo bash pi/install_pi.sh"])
    transporte.publicar(TOPICO_DIAGNOSTICO, {"oi": 1})
    if not recebido.wait(3.0):
        falhou("conectou, mas uma mensagem de teste não voltou pelo broker",
               ["confira se não há firewall ou outro broker no caminho"])
    ok("o notebook publica e recebe mensagens pelo broker")
    return bancada


def etapa_no_fog(bancada):
    titulo(2, "Nó fog (fog_node.py) respondendo")
    try:
        rede = bancada.medir_rtt(n=10)
    except ConnectionError:
        falhou("o nó fog não respondeu ao ping", [
            "no Pi, rode:  cd ~/fogpi && taskset -c 2,3 python3 -m emulation.fog_node --broker localhost",
            "ele deve mostrar 'nó fog no ar'; deixe essa janela aberta"])
    ok(f"o nó fog respondeu; ida e volta pela rede: mediana {rede['rtt_mediana_ms']:.1f} ms, "
       f"p95 {rede['rtt_p95_ms']:.1f} ms")
    if rede["rtt_mediana_ms"] > 20 or rede["rtt_p95_ms"] > 50:
        print("  [AVISO]  rede lenta para a emulação (o normal numa rede local é poucos ms).")
        print("           Causa comum: economia de energia do Wi-Fi do Pi. No Pi, rode:")
        print("               sudo iw wlan0 set power_save off")
        print("           ou ligue o Pi por cabo de rede, e rode o diagnóstico de novo.")


def _amostra(i, t, servico, sinais, classe, news2):
    return {"id": i, "paciente": i, "t_plan": t, "servico": servico,
            "classe_esperada": classe, "news2_esperado": news2, "estado": "diag", "sinais": sinais}


def etapa_preempcao(bancada):
    titulo(3, "Preempção real (2 amostras feitas à mão)")
    print("           estável chega em 0 s e precisa de 800 ms de CPU;")
    print("           crítico (NEWS2 8) chega em 300 ms e precisa de 50 ms.")
    trace = {"semente": 0, "servico_medio": 0.2, "taxa_chegada": 2.0,
             "config": {"duracao": 1.0, "aquecimento": 0.0},
             "amostras": [_amostra(0, 0.0, 0.8, NORMAL, 0, 0), _amostra(1, 0.3, 0.05, GRAVE, 3, 8)]}
    res = {}
    for pol in (PROPOSTA, NP):
        linhas, info = bancada.executar(f"diag-preempcao-{pol['nome']}", trace, pol, folga=5.0)
        if info["perdidos"]:
            falhou(f"{pol['nome']}: {info['perdidos']} alerta(s) não voltaram",
                   ["veja a janela do fog_node.py no Pi: apareceu algum erro?"])
        res[pol["nome"]] = {l["id"]: l for l in linhas}

    print("\n           política        crítico chega   crítico termina   estável termina   interrupções")
    for nome, r in res.items():
        zero = r[0]["chegada_no"]          # instantes contados a partir da chegada do estável
        print(f"           {nome:<15} {1e3 * (r[1]['chegada_no'] - zero):>9.0f} ms "
              f"{1e3 * (r[1]['fim_no'] - zero):>13.0f} ms {1e3 * (r[0]['fim_no'] - zero):>13.0f} ms "
              f"{r[0]['preempcoes']:>10}")
    print("           (tempos contados a partir da chegada do estável ao Pi; o esperado é o crítico")
    print("            chegar em ~300 ms; Proposta ~350 e ~850 ms; Prioridade NP ~850 e ~800 ms)\n")

    p, n = res["Proposta"], res["Prioridade NP"]
    if p[1]["classe"] != 3:
        falhou(f"o Pi calculou classe {p[1]['classe']} para a amostra crítica (esperado 3)")
    ok("o Pi calculou o NEWS2 da amostra crítica corretamente (classe crítica)")

    # A preempção só pode ser avaliada se o crítico chegou ao Pi com o estável
    # em execução. Se a rede atrasou ou agrupou as mensagens, o crítico chega
    # antes de o estável começar ou depois de ele terminar, e não há o que
    # interromper: o problema é a rede, não o escalonador.
    for nome, r in res.items():
        intervalo = r[1]["chegada_no"] - r[0]["chegada_no"]
        if not (r[0]["inicio_no"] < r[1]["chegada_no"] < r[0]["fim_no"]):
            falhou(f"{nome}: o crítico não chegou ao Pi enquanto o estável executava "
                   f"(chegou {1e3 * intervalo:.0f} ms depois do estável; o esperado é ~300 ms)", [
                       "a rede atrasou ou agrupou as mensagens, então não houve o que interromper",
                       "ligue o Pi por cabo de rede ou desligue a economia de energia do Wi-Fi do Pi:",
                       "    sudo iw wlan0 set power_save off",
                       "depois rode o diagnóstico de novo"])

    if not (p[1]["fim_no"] < p[0]["fim_no"] and p[0]["preempcoes"] == 1):
        falhou("com a Proposta, o crítico chegou com o estável em execução e NÃO o interrompeu", [
            "confira se o Pi está com a versão mais recente do pi_package.zip "
            "(gere de novo no notebook, copie e descompacte no Pi)",
            "reinicie o fog_node.py no Pi depois de descompactar"])
    ok("Proposta: o crítico interrompeu o estável e terminou primeiro")
    if p[0]["executado"] < 0.8:
        falhou("o estável não completou seu processamento depois de retomar")
    ok(f"Proposta: o estável retomou e completou seus 800 ms "
       f"(executou {1e3 * p[0]['executado']:.0f} ms)")
    if not n[1]["fim_no"] > n[0]["fim_no"]:
        falhou("com a Prioridade NP o crítico não esperou o estável (deveria esperar)")
    ok("Prioridade NP: o crítico esperou o estável terminar, como esperado")


def _p99_criticas(lats):
    return 1e3 * float(np.percentile(lats, 99)) if len(lats) else float("nan")


def etapa_mini_carga(bancada, duracao: float):
    titulo(4, f"Mini-carga: {duracao:.0f} s de envios, 150 pacientes, ρ = 0,95 (FIFO e Proposta)")
    aq = 0.25 * duracao
    cfg = ConfigCarga(n_pacientes=150, intervalo_amostragem=6.0, duracao=duracao,
                      aquecimento=aq, utilizacao=0.95)
    trace = gerar_trace(cfg, 2026)
    n = len(trace["amostras"])
    print(f"           {n} amostras; tempo médio de processamento {1e3 * trace['servico_medio']:.0f} ms")
    tabela = {}
    for pol in (FIFO, PROPOSTA):
        print(f"           rodando {pol['nome']}... (~{duracao + 5:.0f} s)", flush=True)
        linhas, info = bancada.executar(f"diag-carga-{pol['nome']}", trace, pol, folga=20.0)
        recebidas = [l for l in linhas if l.get("t_recebido") is not None]
        divergentes = sum(1 for l in recebidas if l["classe"] != l["classe_esperada"])
        crit = [l for l in recebidas if l["classe"] == 3 and l["t_plan"] >= aq]
        sim = simular(trace_para_specs(trace), criar_politica(pol, trace["servico_medio"]))
        sim_crit = [t.latencia for t in sim.tarefas if t.classe == 3 and t.chegada >= aq]
        tabela[pol["nome"]] = {
            "recebidos": len(recebidas), "divergentes": divergentes, "n_crit": len(crit),
            "sim": _p99_criticas(sim_crit),
            "interno": _p99_criticas([l["fim_no"] - l["chegada_no"] for l in crit]),
            "e2e": _p99_criticas([l["t_recebido"] - l["t_envio"] for l in crit]),
            "preempcoes": sum(l["preempcoes"] for l in recebidas)}

    print("\n           p99 da latência dos alertas críticos (ms)")
    print("           política    alertas      simulado   Pi interno   ponta a ponta   preempções")
    for nome, t in tabela.items():
        print(f"           {nome:<10} {t['recebidos']:>4}/{n:<6} {t['sim']:>9.0f} {t['interno']:>11.0f}"
              f" {t['e2e']:>14.0f} {t['preempcoes']:>12}")
    print(f"           ({tabela['Proposta']['n_crit']} alertas críticos medidos: números só indicativos)\n")

    for nome, t in tabela.items():
        if t["recebidos"] < n:
            falhou(f"{nome}: {n - t['recebidos']} alertas não voltaram ao posto",
                   ["rede instável? prefira cabo", "veja se o Pi mostrou algum erro"])
        if t["divergentes"]:
            falhou(f"{nome}: o NEWS2 do Pi diferiu do esperado em {t['divergentes']} amostras",
                   ["o Pi e o notebook estão com a mesma versão do projeto?"])
    ok("todos os alertas voltaram ao posto e o NEWS2 do Pi bateu em todas as amostras")
    f, p = tabela["FIFO"], tabela["Proposta"]
    if p["interno"] < f["interno"]:
        ok(f"a Proposta reduziu a p99 dos críticos frente ao FIFO "
           f"({f['interno']:.0f} -> {p['interno']:.0f} ms no Pi)")
    else:
        print("  [AVISO]  nesta amostra curta a Proposta não ficou abaixo do FIFO; "
              "rode de novo com --duracao 40")
    dif = abs(p["interno"] - p["sim"]) / p["sim"] if p["sim"] > 0 else float("nan")
    print(f"           Pi interno × simulado (Proposta): diferença de {100 * dif:.0f}%")


def main():
    ap = argparse.ArgumentParser(description="Diagnóstico rápido da bancada de emulação.")
    ap.add_argument("--broker", help="IP do Raspberry Pi")
    ap.add_argument("--porta", type=int, default=1883)
    ap.add_argument("--local", action="store_true", help="sem Pi: tudo neste computador")
    ap.add_argument("--duracao", type=float, default=20.0, help="segundos da mini-carga")
    ap.add_argument("--sem-carga", action="store_true", help="pula a etapa 4")
    a = ap.parse_args()
    if not a.local and not a.broker:
        ap.error("informe --broker <IP do Pi> (ou --local para testar sem o Pi)")
    sys.setswitchinterval(0.0005)

    inicio = time.perf_counter()
    print("Diagnóstico da bancada" + (" (modo local, sem Pi)" if a.local else f" | broker {a.broker}"))
    recebido = threading.Event()
    if a.local:
        from .common import BrokerLocal
        from .fog_node import NoFog
        broker = BrokerLocal(atraso_min=0.001, atraso_max=0.003)
        NoFog(TransporteLocal(broker), verbose=False).iniciar(em_thread=True)
        transporte = TransporteLocal(broker)
    else:
        transporte = TransporteMQTT(a.broker, a.porta, nome="diagnostico")
    transporte.assinar(TOPICO_DIAGNOSTICO, lambda topico, msg: recebido.set())

    bancada = etapa_broker(transporte, recebido, a.local)
    etapa_no_fog(bancada)
    etapa_preempcao(bancada)
    if not a.sem_carga:
        etapa_mini_carga(bancada, a.duracao)
    print(f"\nTudo certo ({time.perf_counter() - inicio:.0f} s). Próximo passo: o teste rápido "
          "(configs/quick.yaml).")
    transporte.desconectar()


if __name__ == "__main__":
    main()
