"""Testes da emulação com transporte local (sem Raspberry Pi e sem broker)."""

import multiprocessing
import sys
import threading

import pytest

from emulation.testbench import Bancada
from emulation.common import BrokerLocal, TransporteLocal
from emulation.make_trace import gerar_trace
from emulation.fog_node import NoFog, executar_em_dois_processos
from fogsched.workload import ConfigCarga
from fogsched.news2 import SinaisVitais, calcular_news2, classificar

NORMAL = dict(freq_respiratoria=16.0, spo2=97.0, oxigenio_suplementar=False, pa_sistolica=120.0,
              pulso=75.0, consciencia_alerta=True, temperatura=36.8)
GRAVE = dict(NORMAL, freq_respiratoria=26.0, spo2=90.0, oxigenio_suplementar=True)  # NEWS2 = 8


def _amostra(i, t, servico, sinais, classe, news2):
    return {"id": i, "paciente": i, "t_plan": t, "servico": servico,
            "classe_esperada": classe, "news2_esperado": news2, "estado": "teste", "sinais": sinais}


def _trace_manual():
    """Estável chega em 0 s e precisa de 0,4 s de CPU; crítico chega em 0,1 s (0,05 s de CPU)."""
    return {"semente": 0, "servico_medio": 0.2, "taxa_chegada": 2.0,
            "config": {"duracao": 1.0, "aquecimento": 0.0},
            "amostras": [_amostra(0, 0.0, 0.4, NORMAL, 0, 0),
                         _amostra(1, 0.1, 0.05, GRAVE, 3, 8)]}


def _rodar(politica, dois_processos=False):
    sys.setswitchinterval(0.0005)
    broker = BrokerLocal(atraso_min=0.0005, atraso_max=0.001)
    if dois_processos:                  # como no Pi: rede aqui, trabalho num processo filho
        pronto = threading.Event()
        threading.Thread(target=executar_em_dois_processos,
                         args=(TransporteLocal(broker), False, pronto, "fork"), daemon=True).start()
        assert pronto.wait(30)
    else:
        no = NoFog(TransporteLocal(broker), verbose=False)
        no.iniciar(em_thread=True)
    bancada = Bancada(TransporteLocal(broker), espera_ativa=False, verbose=False)
    linhas, info = bancada.executar("teste", _trace_manual(), politica, folga=5.0)
    if dois_processos:
        TransporteLocal(broker).publicar("hospital/controle", {"comando": "desligar"})
    else:
        no.parar_evento.set()
    broker.fechar()
    return {l["id"]: l for l in linhas}, info


def test_trace_tem_sinais_coerentes_com_o_news2():
    cfg = ConfigCarga(n_pacientes=30, intervalo_amostragem=3.0, duracao=30.0, utilizacao=0.9)
    for a in gerar_trace(cfg, 7)["amostras"]:
        r = calcular_news2(SinaisVitais(**a["sinais"]))
        assert r.total == a["news2_esperado"]
        assert int(classificar(r)) == a["classe_esperada"]


def test_critico_interrompe_estavel_em_tempo_real():
    r, info = _rodar({"nome": "Proposta", "tipo": "proposta", "tempo_por_nivel_rel": 50,
                      "teto": {0: 1.9, 1: 2.5, 2: 2.9}})
    estavel, critico = r[0], r[1]
    assert info["perdidos"] == 0
    assert critico["classe"] == 3                       # o Pi calculou o NEWS2 certo
    assert critico["fim_no"] < estavel["fim_no"]        # o crítico terminou primeiro
    assert critico["fim_no"] - critico["chegada_no"] < 0.15
    assert estavel["preempcoes"] == 1                   # o estável foi interrompido uma vez...
    assert estavel["executado"] >= 0.4                  # ...e retomou até completar seu trabalho


def test_sem_preempcao_critico_espera():
    r, _ = _rodar({"nome": "Prioridade NP", "tipo": "prioridade_np"})
    assert r[1]["fim_no"] > r[0]["fim_no"]              # o crítico esperou o estável terminar
    assert r[0]["preempcoes"] == 0


@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # fork com threads do teste
@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(),
                    reason="o modo de dois processos é o do Pi (Linux)")
def test_dois_processos_como_no_pi():
    r, info = _rodar({"nome": "Proposta", "tipo": "proposta", "tempo_por_nivel_rel": 50,
                      "teto": {0: 1.9, 1: 2.5, 2: 2.9}}, dois_processos=True)
    assert info["perdidos"] == 0
    assert r[1]["fim_no"] < r[0]["fim_no"] and r[0]["preempcoes"] == 1
    assert r[1]["fim_no"] - r[1]["chegada_no"] < 0.15
