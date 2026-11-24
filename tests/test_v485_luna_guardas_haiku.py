# -*- coding: utf-8 -*-
"""v4.85: el motor OpenAI Luna usa dos guardas del motor Claude Haiku.

1. Tono por ROL: Luna declara el rol del cliente en la nota y el tono debe
   corresponder a ese rol; si no, la fila vuelve al modelo con el motivo, y si
   sigue sin cuadrar, ante la duda queda Neutro.
2. Validador de subtema de Haiku: sus motivos (verbo conjugado, copia, largo,
   la entidad como tema) van a la vuelta de reparación con el modelo.
"""
import json
import os
import re
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import analyzer_tono_tema as az  # noqa: E402

CFG = {'brand': 'Marca', 'aliases': ['La Marca'], 'voceros': ['Ana Gómez'], 'api_key': 'k',
       'model': 'gpt-6-luna', 'criterio': None}


def _grupos():
    return [
        {'grupo': 1, 'n': 1, 'titulo': 'Concejales cuestionan contrato de la Marca',
         'titulos_alt': [], 'texto': 'Concejales cuestionaron el contrato de la Marca por sobrecostos.',
         'contexto': 'Concejales cuestionaron el contrato de la Marca por sobrecostos.'},
        {'grupo': 2, 'n': 1, 'titulo': 'La Marca entrega 300 viviendas en Soacha',
         'titulos_alt': [], 'texto': 'La Marca entregó 300 viviendas a familias de Soacha.',
         'contexto': 'La Marca entregó 300 viviendas a familias de Soacha.'},
    ]


class FakeLuna:
    """Responde al lote con etiquetas fijas y registra lo que se le pide."""

    def __init__(self, lote, reparacion=None):
        self.lote, self.reparacion, self.pedidos = lote, reparacion, []

    def __call__(self, cfg, mensajes, uso=None, **kw):
        user = mensajes[-1]['content']
        self.pedidos.append((mensajes[0]['content'], user))
        if user.startswith('Corrige SOLO'):
            ids = [int(x) for x in re.findall(r'GRUPO id=(\d+)', user)]
            r = self.reparacion(ids) if self.reparacion else []
            return json.dumps({'resultados': r})
        return json.dumps({'resultados': self.lote})

    def reparaciones(self):
        return [u for _s, u in self.pedidos if u.startswith('Corrige SOLO')]


def _correr(fake, cfg=None, votos=1):
    with patch.object(az, 'llamar_llm', side_effect=fake):
        return az.etiquetar_grupos(dict(CFG, **(cfg or {})), _grupos(), votos=votos, workers=1)


BIEN = {'id': 2, 'sub_tema': 'Entrega de viviendas en Soacha', 'rol': 'gestion', 'tono': 'Positivo'}


def test_prompt_pide_el_rol():
    p = az.prompt_sistema(dict(CFG))
    assert 'rol' in p and 'gestion|logro|critica|afectacion|equilibrada|referencial' in p
    assert 'gestion y logro = Positivo' in p


def test_rol_incoherente_vuelve_al_modelo_y_se_corrige():
    lote = [{'id': 1, 'sub_tema': 'Cuestionamiento a contrato de la Marca', 'rol': 'critica',
             'tono': 'Positivo'}, BIEN]
    fake = FakeLuna(lote, reparacion=lambda ids: [
        {'id': 1, 'sub_tema': 'Cuestionamientos por sobrecostos en contrato', 'rol': 'critica',
         'tono': 'Negativo'}] if 1 in ids else [])
    et = _correr(fake)
    rep = fake.reparaciones()
    assert rep and 'rol_tono' in rep[0] and 'CONTEXTO DE LA MARCA' in rep[0]
    assert "corresponde a tono Negativo, no a Positivo" in rep[0]
    assert et[1]['tono'] == 'Negativo' and et[1]['rol'] == 'critica'
    assert et[2]['tono'] == 'Positivo'
    assert az.ultimo_resumen()['luna_guardas_haiku']['rol_tono_a_revisar'] == 1


def test_rol_que_sigue_sin_cuadrar_queda_neutro():
    lote = [{'id': 1, 'sub_tema': 'Cuestionamiento a contrato por sobrecostos', 'rol': 'critica',
             'tono': 'Positivo'}, BIEN]
    et = _correr(FakeLuna(lote))      # la reparación no devuelve nada
    assert et[1]['tono'] == 'Neutro'
    assert az.ultimo_resumen()['luna_guardas_haiku']['tono_incoherente_a_neutro'] == 1


def test_positivo_sin_rol_pide_el_rol():
    lote = [{'id': 1, 'sub_tema': 'Cuestionamiento a contrato por sobrecostos', 'tono': 'Negativo'},
            {'id': 2, 'sub_tema': 'Entrega de viviendas en Soacha', 'tono': 'Neutro'}]
    fake = FakeLuna(lote)
    _correr(fake)
    rep = ' '.join(fake.reparaciones())
    assert 'falta el rol' in rep and 'GRUPO id=1' in rep
    assert 'GRUPO id=2' not in rep          # un Neutro sin rol no se devuelve


def test_forma_de_haiku_va_a_la_reparacion():
    lote = [{'id': 1, 'sub_tema': 'Datos de la Marca', 'rol': 'critica', 'tono': 'Negativo'}, BIEN]
    fake = FakeLuna(lote, reparacion=lambda ids: [
        {'id': 1, 'sub_tema': 'Cuestionamientos por sobrecostos en contrato', 'rol': 'critica',
         'tono': 'Negativo'}] if 1 in ids else [])
    et = _correr(fake)
    rep = ' '.join(fake.reparaciones())
    assert 'forma(' in rep and 'entidad' in rep
    assert et[1]['sub_tema'] == 'Cuestionamientos por sobrecostos en contrato'


def test_con_pkl_de_tono_no_se_revisa_el_rol():
    lote = [{'id': 1, 'sub_tema': 'Cuestionamiento a contrato por sobrecostos', 'rol': 'critica',
             'tono': 'Positivo'}, BIEN]
    fake = FakeLuna(lote)
    et = _correr(fake, cfg={'_tono_pkl': True})
    assert not any('rol_tono' in r for r in fake.reparaciones())
    assert et[1]['tono'] == 'Positivo'      # lo reemplaza el PKL más adelante


def test_se_puede_apagar():
    lote = [{'id': 1, 'sub_tema': 'Cuestionamiento a contrato por sobrecostos', 'rol': 'critica',
             'tono': 'Positivo'}, BIEN]
    fake = FakeLuna(lote)
    et = _correr(fake, cfg={'luna_guardas_haiku': False})
    assert not fake.reparaciones() and et[1]['tono'] == 'Positivo'


def test_voto_conserva_el_rol_coherente():
    por = [{1: {'sub_tema': 'x', 'tono': 'Negativo', 'rol': 'critica'}},
           {1: {'sub_tema': 'x', 'tono': 'Negativo', 'rol': 'referencial'}}]
    assert az._voto_mayoria(por, [1])[1]['rol'] == 'critica'
