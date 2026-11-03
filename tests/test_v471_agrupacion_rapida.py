"""v4.71: agrupación estricta de Secretaría + guardas nuevas + rendimiento.

- Mismo molde de titular con otro lugar/persona no es la misma noticia.
- La cola de llamado del titular no cuenta para comparar ni es subtema.
- Un subtema no empieza con pretérito con tilde ni es solo persona/tiempo.
- Las cachés de texto no cambian resultados (copias defensivas).
- Agrupación: misma noticia reescrita se une; mismo tipo en otra ciudad no.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az  # noqa: E402

KM = {'titulo': 'Título'}
MARCA = az._tokens_marca('Fundación Santa Fe', ['FSFB'])


def fila(titulo, cuerpo):
    return {'Título': titulo, 'CuerpoEs': cuerpo}


class TestConflictoNombrePropio(unittest.TestCase):
    def test_otra_ciudad_es_conflicto(self):
        self.assertTrue(az._conflicto_nombre_propio(
            'La Fundación Santa Fe abre becas de enfermería en Cartagena',
            'La convocatoria en Cartagena tiene 40 cupos.',
            'La Fundación Santa Fe abre becas de enfermería en Medellín',
            'La convocatoria en Medellín tiene 25 cupos.', MARCA))

    def test_misma_noticia_no_es_conflicto(self):
        # Un medio nombra el barrio en el titular; el otro lo trae en el cuerpo.
        self.assertFalse(az._conflicto_nombre_propio(
            'Alcaldía entrega colegio en Bosa',
            'El nuevo colegio de Bosa atenderá a 1.200 estudiantes.',
            'Alcaldía entrega nuevo colegio en Bogotá',
            'La sede, ubicada en Bosa, atenderá a 1.200 estudiantes en Bogotá.', ()))

    def test_sin_nombres_no_hay_conflicto(self):
        self.assertFalse(az._conflicto_nombre_propio(
            'Inauguran sede de urgencias', 'x', 'Abren sede de urgencias', 'y', ()))

    def test_titulo_en_mayusculas_sin_senal(self):
        self.assertEqual(az._nombres_propios_titular('ALCALDÍA ENTREGA COLEGIO EN BOSA'), set())
        self.assertEqual(az._nombres_propios_titular('Alcaldía Entrega Nuevo Colegio En Bosa'), set())


class TestColaLlamado(unittest.TestCase):
    def test_titulo_sin_cola(self):
        t = 'Fundación Santa Fe advierte aumento de dengue en Cali: lo que dijo Ricardo Salazar'
        self.assertEqual(az._titulo_sin_cola(t),
                         'Fundación Santa Fe advierte aumento de dengue en Cali')
        self.assertEqual(az._titulo_sin_cola('Hecho: esto se sabe'), 'Hecho')
        self.assertEqual(az._titulo_sin_cola('Sin cola'), 'Sin cola')

    def test_asi_fue_no_es_cola(self):
        t = ('De las calles de un barrio pobre del suroccidente de Barranquilla '
             'a una curul en el Congreso: así fue el ascenso político de Estefanel Gutiérrez')
        self.assertEqual(az._titulo_sin_cola(t), t)

    def test_subtema_no_es_la_persona_citada(self):
        t = 'La Fundación Santa Fe advierte aumento de casos de dengue en Cartagena: lo que dijo Ricardo Salazar'
        self.assertEqual(az._subtema_desde_titulo(t), 'Aumento de casos de dengue en Cartagena')


class TestFormaSubtema(unittest.TestCase):
    def test_preterito_con_tilde_es_verbo_inicial(self):
        for s in ('Informó este miércoles', 'Conoció el pasado jueves'):
            self.assertTrue(any(p.startswith('verbo_inicial') for p in az.validar(s, 'Neutro', [])), s)

    def test_sin_hecho(self):
        for s in ('Ricardo Salazar', 'Fin de semana', 'Años en Bucaramanga'):
            self.assertTrue(az._es_subtema_sin_hecho(s), s)
        for s in ('Metro de Bogotá', 'Línea 137', 'Estado de salud de Yamid Amat',
                  'Universidad de Atalaya', 'Inauguración de sede en Cali'):
            self.assertFalse(az._es_subtema_sin_hecho(s), s)

    def test_respaldo_no_devuelve_cola_de_cuerpo(self):
        tit = 'La Fundación Santa Fe advierte aumento de casos de dengue en Cartagena: esto se sabe'
        cuerpo = ('La FSFB advirtió un aumento de 445 casos de dengue grave y recomendó '
                  'eliminar criaderos en Cartagena, según informó este miércoles.')
        r = az._subtema_especifico(tit, [cuerpo])
        self.assertTrue(r)
        self.assertFalse(r.lower().startswith(('informó', 'conoció', 'según')))


class TestCaches(unittest.TestCase):
    def test_words_devuelve_copia(self):
        a = az.words('Hola mundo')
        a.append('x')
        self.assertEqual(az.words('Hola mundo'), ['hola', 'mundo'])

    def test_contenido_discriminante_devuelve_copia(self):
        a = az._contenido_discriminante('Entrega del parque en Bosa')
        a.add('zzz')
        self.assertNotIn('zzz', az._contenido_discriminante('Entrega del parque en Bosa'))

    def test_nz_no_str(self):
        self.assertEqual(az.nz(None), '')
        self.assertEqual(az.nz(12), '12')
        self.assertEqual(az.nz('Ávila  Ñ!'), 'avila n')

    def test_limpiar_caches(self):
        az.nz('texto para la caché')
        az.limpiar_caches_texto()
        self.assertEqual(az._nz_str.cache_info().currsize, 0)


class TestAgrupacion(unittest.TestCase):
    def _cuerpo(self, ciudad, cupos, persona):
        return ('La Fundación Santa Fe abrió una convocatoria de %d becas para estudiantes '
                'de enfermería en %s. %s, vocera de la institución, explicó los requisitos '
                'del proceso y las fechas de inscripción para este semestre en %s.'
                % (cupos, ciudad, persona, ciudad)) * 2

    def test_misma_noticia_se_une_otra_ciudad_no(self):
        rows = [
            fila('Fundación Santa Fe abre convocatoria de becas para enfermería en Cartagena',
                 self._cuerpo('Cartagena', 40, 'Andrea Gómez')),
            fila('La Fundación Santa Fe abre convocatoria de becas para enfermería en Cartagena',
                 self._cuerpo('Cartagena', 40, 'Andrea Gómez')[::-1][::-1] + ' Más detalles.'),
            fila('Fundación Santa Fe abre convocatoria de becas para enfermería en Medellín',
                 self._cuerpo('Medellín', 25, 'Paola Cárdenas')),
        ]
        grupos, mapa = az.construir_grupos(rows, KM, estricta=True,
                                           excluir_similitud=MARCA)
        self.assertEqual(mapa[0], mapa[1])
        self.assertNotEqual(mapa[0], mapa[2])


if __name__ == '__main__':
    unittest.main()
