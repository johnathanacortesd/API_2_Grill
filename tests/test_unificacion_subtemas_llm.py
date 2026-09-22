"""Pruebas v4.14/v4.15: pase final de unificación de subtemas entre lotes.

v4.14:
- `_sanitizar_fusiones`: solo grupos válidos (>=2 índices distintos, en rango,
  sin repetir un índice en dos grupos); tolera índices 0-based y 1-based.
- `_canonico_de_fusion`: gana el más frecuente; en empate, el MÁS LARGO
  (más específico) — v4.15: el empate corto degradaba la especificidad.
- `unificar_subtemas_llm`: aplica la fusión del modelo a `etiquetas`,
  conserva el texto original del canónico (verbatim), NUNCA fusiona
  subtemas con tonos distintos (v4.15: evita que el voto de tono por subtema
  voltee un Positivo a Neutro) y no rompe si la llamada LLM falla.
- No llama al modelo si hay 0-1 subtemas únicos.

v4.15 (tono):
- `aplicar_guarda_positiva`: participación de la marca en reuniones /
  conversatorios / foros (verbo de participación + evento + actor en la
  misma oración) sube Neutro a Positivo; no toca Negativos; no aplica en
  tragedia sin acción de la marca.
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az


def _etiquetas(subs):
    return {i + 1: {'sub_tema': s, 'tono': 'Neutro'} for i, s in enumerate(subs)}


def _grupos(subs):
    return [{'grupo': i + 1, 'titulo': 'Titular %d' % (i + 1)} for i in range(len(subs))]


class TestSanitizarFusiones(unittest.TestCase):
    def test_grupos_validos(self):
        self.assertEqual(az._sanitizar_fusiones([[1, 2], [3, 4, 5]], 5),
                         [[0, 1], [2, 3, 4]])

    def test_fuera_de_rango_se_descarta(self):
        self.assertEqual(az._sanitizar_fusiones([[1, 9], [2, 3]], 5), [[1, 2]])

    def test_singleton_se_descarta(self):
        self.assertEqual(az._sanitizar_fusiones([[1], [2, 3]], 5), [[1, 2]])

    def test_indice_repetido_en_dos_grupos(self):
        # El índice 2 ya se usó: el segundo grupo queda en singleton y se cae.
        self.assertEqual(az._sanitizar_fusiones([[1, 2], [2, 3]], 5), [[0, 1]])

    def test_indices_base_cero_tolerados(self):
        # Si el modelo devuelve 0-based, se interpreta como tal en vez de
        # descartarse en silencio.
        self.assertEqual(az._sanitizar_fusiones([[0, 1]], 5), [[0, 1]])
        self.assertEqual(az._sanitizar_fusiones([[0, 4]], 5), [[0, 4]])

    def test_valores_no_enteros(self):
        self.assertEqual(az._sanitizar_fusiones([['a', 1, 2], None, 'x'], 5),
                         [[0, 1]])

    def test_vacia(self):
        self.assertEqual(az._sanitizar_fusiones([], 5), [])
        self.assertEqual(az._sanitizar_fusiones(None, 5), [])


class TestCanonicoDeFusion(unittest.TestCase):
    def test_gana_el_mas_frecuente(self):
        textos = ['Apertura del laboratorio', 'Inauguración del laboratorio']
        conteo = {az.nz('Apertura del laboratorio'): 1,
                  az.nz('Inauguración del laboratorio'): 4}
        self.assertEqual(az._canonico_de_fusion([0, 1], textos, conteo),
                         'Inauguración del laboratorio')

    def test_empate_gana_el_mas_especifico(self):
        # v4.15: en empate se conserva el subtema MÁS LARGO (más específico).
        # El empate corto degradaba la calidad («Conversatorio» ganaba a
        # «Participación en el conversatorio de salud mental»).
        textos = ['Conversatorio', 'Participación en el conversatorio de salud mental']
        conteo = {az.nz('Conversatorio'): 1,
                  az.nz('Participación en el conversatorio de salud mental'): 1}
        self.assertEqual(az._canonico_de_fusion([0, 1], textos, conteo),
                         'Participación en el conversatorio de salud mental')

    def test_conserva_verbatim(self):
        textos = ['Foro de Periodismo Científico', 'foro periodismo cientifico']
        conteo = {az.nz('Foro de Periodismo Científico'): 3,
                  az.nz('foro periodismo cientifico'): 1}
        self.assertEqual(az._canonico_de_fusion([0, 1], textos, conteo),
                         'Foro de Periodismo Científico')


class TestUnificarSubtemasLlm(unittest.TestCase):
    def test_fusiona_parafrasis(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio',
                'Obras en Sincelejo']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        payload = '{"fusiones": [[1, 2]]}'
        with patch.object(az, 'llamar_llm', return_value=payload) as m:
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertTrue(m.called)
        self.assertEqual(cambios, 1)
        # Empate de frecuencia (1-1): v4.15 gana el más específico (más largo).
        self.assertEqual(et[1]['sub_tema'], 'Inauguración del laboratorio')
        self.assertEqual(et[2]['sub_tema'], 'Inauguración del laboratorio')
        self.assertEqual(et[3]['sub_tema'], 'Obras en Sincelejo')

    def test_no_fusiona_tonos_distintos(self):
        # v4.15: fusionar subtemas con tonos distintos podía voltear un
        # Positivo a Neutro en el voto de tono por subtema. Se dejan como están.
        et = {1: {'sub_tema': 'Apertura del laboratorio', 'tono': 'Positivo'},
              2: {'sub_tema': 'Inauguración del laboratorio', 'tono': 'Neutro'}}
        gr = _grupos(['Apertura del laboratorio', 'Inauguración del laboratorio'])
        with patch.object(az, 'llamar_llm', return_value='{"fusiones": [[1, 2]]}'):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 0)
        self.assertEqual(et[1]['sub_tema'], 'Apertura del laboratorio')
        self.assertEqual(et[2]['sub_tema'], 'Inauguración del laboratorio')
        self.assertEqual(et[1]['tono'], 'Positivo')

    def test_fusiona_con_indices_base_cero(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        with patch.object(az, 'llamar_llm', return_value='{"fusiones": [[0, 1]]}'):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 1)
        self.assertEqual(et[1]['sub_tema'], 'Inauguración del laboratorio')

    def test_respeta_frecuencia_del_lote(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio',
                'Inauguración del laboratorio']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        with patch.object(az, 'llamar_llm', return_value='{"fusiones": [[1, 2]]}'):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 1)
        for gid in (1, 2, 3):
            self.assertEqual(et[gid]['sub_tema'], 'Inauguración del laboratorio')

    def test_sin_fusiones_no_cambia(self):
        subs = ['Apertura del laboratorio', 'Obras en Sincelejo']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        with patch.object(az, 'llamar_llm', return_value='{"fusiones": []}'):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 0)
        self.assertEqual(et[1]['sub_tema'], 'Apertura del laboratorio')

    def test_fallo_llm_no_rompe(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        with patch.object(az, 'llamar_llm', side_effect=RuntimeError('HTTP 500')):
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 0)
        self.assertEqual(et[1]['sub_tema'], 'Apertura del laboratorio')
        self.assertEqual(et[2]['sub_tema'], 'Inauguración del laboratorio')

    def test_un_subtema_no_llama(self):
        et = _etiquetas(['Solo uno'])
        gr = _grupos(['Solo uno'])
        with patch.object(az, 'llamar_llm') as m:
            cambios = az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso={})
        self.assertEqual(cambios, 0)
        m.assert_not_called()

    def test_suma_uso_cuando_hay_llamada(self):
        subs = ['Apertura del laboratorio', 'Inauguración del laboratorio']
        et = _etiquetas(subs)
        gr = _grupos(subs)
        uso = {'input': 0, 'output': 0, 'llamadas': 0}

        def fake(cfg, mensajes, json_mode=True, max_tokens=4000, uso=None, **kw):
            if uso is not None:
                uso['input'] += 10
                uso['llamadas'] += 1
            return '{"fusiones": []}'

        with patch.object(az, 'llamar_llm', side_effect=fake):
            az.unificar_subtemas_llm({'model': 'x'}, gr, et, uso=uso)
        self.assertEqual(uso['llamadas'], 1)
        self.assertEqual(uso['input'], 10)


class TestGuardaParticipacion(unittest.TestCase):
    """v4.15: participación de la marca en reuniones/conversatorios/foros
    (regla del cliente) → Neutro a Positivo."""

    BRAND = 'Universidad Simón Bolívar'

    def _grupo(self, titulo, contexto, tono='Neutro'):
        g = {'grupo': 1, 'titulo': titulo, 'contexto': contexto}
        et = {1: {'tono': tono, 'sub_tema': 'x'}}
        return g, et

    def test_asistio_a_conversatorio_es_positivo(self):
        g, et = self._grupo(
            'Universidad Simón Bolívar en conversatorio de salud mental',
            'La Universidad Simón Bolívar asistió al conversatorio de salud '
            'mental realizado ayer en la sede.')
        res = az.aplicar_guarda_positiva([g], et, self.BRAND, [])
        self.assertEqual(et[1]['tono'], 'Positivo')
        self.assertEqual(res, [1])

    def test_participo_en_reunion_es_positivo(self):
        g, et = self._grupo(
            'Reunión con empresarios',
            'La rectora de la Universidad Simón Bolívar participó en la '
            'reunión con empresarios del sector.')
        res = az.aplicar_guarda_positiva([g], et, self.BRAND, [])
        self.assertEqual(et[1]['tono'], 'Positivo')
        self.assertEqual(res, [1])

    def test_organizo_reunion_es_positivo(self):
        g, et = self._grupo(
            'Reunión gremial',
            'La Universidad Simón Bolívar organizó una reunión con '
            'empresarios del sector.')
        res = az.aplicar_guarda_positiva([g], et, self.BRAND, [])
        self.assertEqual(et[1]['tono'], 'Positivo')
        self.assertEqual(res, [1])

    def test_no_toca_negativo(self):
        g, et = self._grupo(
            'Cuestionan a la Universidad',
            'La Universidad Simón Bolívar asistió al conversatorio, pero fue '
            'cuestionada por los asistentes por incumplimientos.',
            tono='Negativo')
        res = az.aplicar_guarda_positiva([g], et, self.BRAND, [])
        self.assertEqual(et[1]['tono'], 'Negativo')
        self.assertEqual(res, [])

    def test_tragedia_sin_accion_no_sube(self):
        # Tragedia con experto de la casa citado = neutral (regla vigente);
        # la participación no la sube a Positivo.
        g, et = self._grupo(
            'Conversatorio sobre el accidente',
            'El rector de la Universidad Simón Bolívar participó en el '
            'conversatorio sobre el accidente fatal de ayer. Se registraron '
            '3 muertes.')
        res = az.aplicar_guarda_positiva([g], et, self.BRAND, [])
        self.assertEqual(et[1]['tono'], 'Neutro')
        self.assertEqual(res, [])

    def test_mencion_sin_participacion_no_sube(self):
        # Mencionar la marca junto a un conversatorio, sin verbo de
        # participación, no alcanza.
        g, et = self._grupo(
            'Conversatorio de salud mental',
            'En el conversatorio de salud mental se mencionó a la '
            'Universidad Simón Bolívar entre los asistentes.')
        res = az.aplicar_guarda_positiva([g], et, self.BRAND, [])
        self.assertEqual(et[1]['tono'], 'Neutro')
        self.assertEqual(res, [])


if __name__ == '__main__':
    unittest.main()
