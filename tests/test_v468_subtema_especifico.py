"""v4.68: 'Hecho informativo' no es un tema ni un subtema aceptado.

_subtema_especifico garantiza un rótulo específico: titular, si no
cuerpo/contexto/resumen, si no palabras distintivas. Aplica también
con titulares solo de preguntas y sin PKL subido. El tema terminal
(_tema_especifico_terminal) sigue la misma regla.
"""
import unittest

import analyzer_tono_tema as A


class TestSubtemaEspecifico(unittest.TestCase):
    def test_titulo_normal_no_cambia(self):
        r = A._subtema_especifico(
            'El Congreso aprobó la reforma pensional en segundo debate',
            ['El Congreso aprobó la reforma pensional en segundo debate.'])
        # v4.70: el verbo del titular se nominaliza (un subtema no empieza
        # con verbo conjugado).
        self.assertEqual(r, 'Aprobación de reforma pensional en segundo debate')

    def test_solo_preguntas_con_cuerpo(self):
        # Regla del usuario: "así venga con preguntas" va subtema
        # específico, no el marcador.
        r = A._subtema_especifico(
            '¿Cuántos años tiene? ¿Cómo inició?',
            ['Inició en la radio hace veinte años. '
             'Hoy dirige el programa matutino de la emisora.'])
        # v4.71: un subtema nunca empieza con un pretérito («Inició…»,
        # regla v4.70/Sec v1.34); el específico sale de la oración siguiente.
        self.assertTrue(r)
        self.assertNotEqual(r, 'Hecho informativo')
        self.assertFalse(r.startswith('Inició'))

    def test_titulo_vacio_con_cuerpo(self):
        r = A._subtema_especifico(
            '', ['La Alcaldía entregó el nuevo parque metropolitano '
                 'en el sur de la ciudad.'])
        self.assertTrue(r)
        self.assertNotEqual(r, 'Hecho informativo')
        self.assertIn('metropolitano', r.lower())

    def test_titulo_cola_llamado_con_cuerpo(self):
        r = A._subtema_especifico(
            'Feria Laboral de Cali: conozca cuándo será',
            ['La Feria Laboral de Cali reunirá cuarenta empresas '
             'con vacantes para jóvenes.'])
        self.assertIn('Feria', r)
        self.assertNotIn('Conozca', r)

    def test_sin_texto_terminal_no_es_marcador(self):
        self.assertEqual(A._subtema_especifico('', []),
                         'Contenido sin texto disponible')

    def test_nunca_vacio_ni_marcador(self):
        for titulo, textos in [
                ('', []),
                ('¿Quién? ¿Cómo?', []),
                ('Conozca cuándo será', ['']),
                (None, ['La obra costó doce mil millones.'])]:
            r = A._subtema_especifico(titulo, textos)
            self.assertTrue(r)
            self.assertNotEqual(r, 'Hecho informativo')


class TestTemaEspecificoTerminal(unittest.TestCase):
    def test_desde_evidencia(self):
        r = A._tema_especifico_terminal(
            ['Alertas Defensoría del Pueblo'],
            ['Defensoría emite alerta temprana'],
            ['La Defensoría emitió la alerta por riesgo en la zona.'])
        self.assertEqual(r, 'Alertas de Defensoría')

    def test_sin_evidencia(self):
        self.assertEqual(A._tema_especifico_terminal([], [], []),
                         'Contenido sin texto disponible')

    def test_una_sola_palabra_que_es_el_titular_no_se_copia(self):
        # El tema generaliza, no copia el titular: si la única palabra
        # distintiva ES el titular, cae al terminal absoluto.
        self.assertEqual(A._tema_especifico_terminal([], ['Pensiones'], []),
                         'Contenido sin texto disponible')


if __name__ == '__main__':
    unittest.main()
