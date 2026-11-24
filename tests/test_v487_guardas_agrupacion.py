"""v4.87: guardas de agrupación restauradas (se perdieron en v4.74).

Mismo molde de titular con otro lugar o persona («…becas en Cartagena» /
«…becas en Medellín», «…sede en Chía» / «…sede en Usaquén») no es la misma
noticia: no se agrupa en `construir_grupos`, ni en el grafo de evidencia
(agrupación estricta), ni en los pases de títulos similares o de asunto del
titular. La misma noticia reescrita por otro medio sí se une.
"""
import os
import sys
import types
import unittest

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, 'tools'))
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az  # noqa: E402

KM = {'titulo': 'Título'}
MARCA = 'Fundación Santa Fe de Bogotá'
ALIAS = ['FSFB', 'Fundación Santa Fe']


def fila(titulo, cuerpo):
    return {'Título': titulo, 'CuerpoEs': cuerpo}


class TestConflictoNombrePropio(unittest.TestCase):
    EXCL = az._tokens_marca('Fundación Santa Fe', ['FSFB'])

    def test_otra_ciudad_es_conflicto(self):
        self.assertTrue(az._conflicto_nombre_propio(
            'La Fundación Santa Fe abre becas de enfermería en Cartagena',
            'La convocatoria en Cartagena tiene 40 cupos.',
            'La Fundación Santa Fe abre becas de enfermería en Medellín',
            'La convocatoria en Medellín tiene 25 cupos.', self.EXCL))

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


class TestConstruirGrupos(unittest.TestCase):
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
                 self._cuerpo('Cartagena', 40, 'Andrea Gómez') + ' Más detalles.'),
            fila('Fundación Santa Fe abre convocatoria de becas para enfermería en Medellín',
                 self._cuerpo('Medellín', 25, 'Paola Cárdenas')),
        ]
        excl = az.tokens_marca('Fundación Santa Fe', ['FSFB'], [])
        for estricta in (False, True):
            _g, mapa = az.construir_grupos(rows, KM, estricta=estricta, excluir_similitud=excl)
            self.assertEqual(mapa[0], mapa[1])
            self.assertNotEqual(mapa[0], mapa[2])

    def test_nota_puente_no_encadena_otra_ciudad(self):
        # La nota «…en el norte de Bogotá» no nombra Usaquén en el titular:
        # sin la guarda entre bloques encadenaba Chía con Usaquén.
        cuerpo_chia = ('La Fundación Santa Fe de Bogotá inauguró una nueva sede de urgencias en el '
                       'municipio de Chía. La sede atenderá a pacientes de Sabana Centro con 30 camas.')
        cuerpo_usa = ('La Fundación Santa Fe de Bogotá inauguró una nueva sede de urgencias en la '
                      'localidad de Usaquén. La sede tiene 60 camas de observación y un helipuerto.')
        rows = [
            fila('Abre la nueva sede de urgencias de la FSFB en Chía', cuerpo_chia),
            fila('Abre la nueva sede de urgencias de la FSFB en Chía', cuerpo_chia + ' Más.'),
            fila('Abre la nueva sede de urgencias de la FSFB en el norte de Bogotá', cuerpo_usa),
            fila('Fundación Santa Fe inaugura nueva sede de urgencias en Usaquén', cuerpo_usa + ' Fin.'),
        ]
        excl = az.tokens_marca(MARCA, ALIAS, [])
        _g, mapa = az.construir_grupos(rows, KM, estricta=True, excluir_similitud=excl)
        self.assertEqual(mapa[0], mapa[1])
        self.assertEqual(mapa[2], mapa[3])
        self.assertNotEqual(mapa[0], mapa[2])

    def test_titulares_distintos_no_son_molde(self):
        self.assertFalse(az.conflicto_entre_bloques(
            ['Alertan por intimidaciones a mujeres cerca de Unisimón'], 'x',
            ['Policía y Gaula blindan dos universidades por acoso a mujeres'], 'y'))
        self.assertTrue(az.conflicto_entre_bloques(
            ['Fundación abre becas de enfermería en Cartagena'], 'convocatoria en Cartagena',
            ['Fundación abre becas de enfermería en Medellín'], 'convocatoria en Medellín'))


class TestGrafoEvidencia(unittest.TestCase):
    def _g(self, gid, titulo, texto):
        return {'grupo': gid, 'titulo': titulo, 'titulos_alt': [], 'texto': texto,
                'contexto': texto}

    def test_mismo_molde_otra_ciudad_sin_arista(self):
        g = [self._g(1, 'Fundación abre becas de enfermería en Cartagena',
                     'La convocatoria en Cartagena tiene 40 cupos.'),
             self._g(2, 'Fundación abre becas de enfermería en Medellín',
                     'La convocatoria en Medellín tiene 25 cupos.')]
        self.assertEqual(az.grafo_evidencia_grupos(g, excluir={'fundacion'})[1], set())

    def test_puente_no_encadena_componentes(self):
        vec = {1: {2}, 2: {1, 3}, 3: {2}}
        g = [self._g(1, 'Fundación abre becas de enfermería en Cartagena', 'Cartagena'),
             self._g(2, 'Fundación abre becas de enfermería', 'convocatoria nacional'),
             self._g(3, 'Fundación abre becas de enfermería en Medellín', 'Medellín')]
        comps = az.componentes_evidencia(vec, grupos=g)
        self.assertFalse(any({1, 3} <= set(c) for c in comps), comps)


class TestBancoDePruebas(unittest.TestCase):
    def test_banco_sin_partidas_ni_fusiones(self):
        import banco_agrupacion as banco
        for estricta in (False, True):
            for semilla in (1, 2, 3):
                r = banco.correr(semilla, estricta=estricta)
                self.assertEqual(r['sub']['partidas'], 0, (estricta, r))
                self.assertEqual(r['sub']['fusiones'], 0, (estricta, r))
                self.assertEqual(r['tono']['partidas'], 0, (estricta, r))


if __name__ == '__main__':
    unittest.main()
