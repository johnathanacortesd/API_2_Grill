"""v4.68: port a Grill de Sec/Def — decreto sin crítica y fidelidad final.

La regla de decreto (Sec/Def v1.12/v1.8) y la fidelidad final
(Sec/Def v1.13/v1.9) llegan a Grill sin cambios de criterio: solo baja
Negativo a Neutro sin crítica, y solo separa la fila cuyo subtema final
no corresponde a su propio título/cuerpo.
"""
import unittest
from unittest.mock import patch

import analyzer_tono_tema as A


def _grupo(gid, titulo, texto):
    return {'grupo': gid, 'titulo': titulo, 'texto': texto, 'contexto': ''}


class TestReglaDecreto(unittest.TestCase):
    def test_decreto_negativo_sin_critica_baja_a_neutro(self):
        g = [_grupo(1, 'Nuevo decreto prohíbe las caravanas',
                    'El decreto fija un horario especial.')]
        et = {1: {'tono': 'Negativo'}}
        self.assertEqual(A.aplicar_regla_decreto(g, et), [1])
        self.assertEqual(et[1]['tono'], 'Neutro')

    def test_decreto_con_critica_respeta_negativo(self):
        g = [_grupo(2, 'Decreto genera polémica en el sector',
                    'El decreto fue cuestionado por gremios.')]
        et = {2: {'tono': 'Negativo'}}
        self.assertEqual(A.aplicar_regla_decreto(g, et), [])
        self.assertEqual(et[2]['tono'], 'Negativo')

    def test_decreto_no_toca_positivos_ni_sin_decreto(self):
        g = [_grupo(3, 'Decreto crea nuevo programa social',
                    'El programa beneficiará a miles.'),
             _grupo(4, 'Empresa anuncia despidos masivos',
                    'La empresa recortará personal.')]
        et = {3: {'tono': 'Positivo'}, 4: {'tono': 'Negativo'}}
        self.assertEqual(A.aplicar_regla_decreto(g, et), [])
        self.assertEqual(et[3]['tono'], 'Positivo')
        self.assertEqual(et[4]['tono'], 'Negativo')

    def test_resolucion_y_ordenanza_tambien_cuentan(self):
        g = [_grupo(5, 'Resolución fija nuevas tarifas',
                    'La resolución establece los valores.'),
             _grupo(6, 'Ordenanza regula el comercio',
                    'La ordenanza crea zonas especiales.')]
        et = {5: {'tono': 'Negativo'}, 6: {'tono': 'Negativo'}}
        self.assertEqual(sorted(A.aplicar_regla_decreto(g, et)), [5, 6])


class TestFidelidadFinal(unittest.TestCase):
    def _fila(self, titulo, cuerpo):
        return {'Título': titulo, 'Resumen - Aclaracion': cuerpo,
                'Contexto analizado': '', 'is_duplicate': False}

    def test_fila_con_subtema_ajeno_recupera_el_propio(self):
        rows = [self._fila('Feria Laboral de Cali abre inscripciones',
                           'La Feria Laboral de Cali abre inscripciones.'),
                self._fila('Concierto de rock en el estadio de Cali',
                           'Un concierto de rock en el estadio.')]
        et = {1: {'sub_tema': 'Concierto de rock en Cali',
                  'tono': 'Neutro'}}
        aj = A.aplicar_fidelidad_final_subtema(
            rows, {0: 1, 1: 1}, et, {}, {}, brand='', aliases=())
        self.assertIn('Feria', aj[0])
        self.assertNotIn(1, aj)

    def test_fila_fiel_no_se_toca_y_duplicada_se_salta(self):
        rows = [self._fila('Concierto de rock en el estadio de Cali',
                           'Un concierto de rock en el estadio.'),
                self._fila('Feria Laboral de Cali abre inscripciones',
                           'La Feria Laboral de Cali abre inscripciones.')]
        rows[1]['is_duplicate'] = True
        et = {1: {'sub_tema': 'Concierto de rock en Cali',
                  'tono': 'Neutro'}}
        aj = A.aplicar_fidelidad_final_subtema(
            rows, {0: 1, 1: 1}, et, {}, {}, brand='', aliases=())
        self.assertEqual(aj, {})

    def test_marcador_viejo_no_se_conserva(self):
        rows = [self._fila('Defensoría emite alerta temprana en Cauca',
                           'La Defensoría emitió la alerta temprana.')]
        et = {1: {'sub_tema': 'Hecho informativo', 'tono': 'Neutro'}}
        aj = A.aplicar_fidelidad_final_subtema(
            rows, {0: 1}, et, {}, {}, brand='', aliases=())
        self.assertTrue(aj[0])
        self.assertNotEqual(aj[0], 'Hecho informativo')


class TestJevParaleloSinApi(unittest.TestCase):
    """El pre-cálculo en paralelo no cambia resultados: con Jev mockeado
    (sin API real) el tema bueno queda y el fallo de Jev no tumba nada."""

    def _caso(self):
        cfg = {'typesafe_api_key': 'k', 'jev_confianza_min': 0.75}
        grupos = [{'grupo': 1, 'titulo': 'Feria Laboral de Cali',
                   'texto': 'La feria reunió empresas.', 'contexto': ''}]
        etiquetas = {1: {'sub_tema': 'Feria Laboral de Cali',
                         'tono': 'Neutro'}}
        temas = {1: 'Empleo en Cali'}
        return cfg, grupos, etiquetas, temas

    def test_veredicto_bueno_deja_el_tema(self):
        cfg, grupos, etiquetas, temas = self._caso()
        bueno = {'cubre': True, 'demasiado_especifico': False,
                 'confianza_cubre': 0.9, 'confianza_especifico': 0.9}
        with patch.object(A, '_tema_con_jev', return_value=bueno):
            out = A.corregir_temas_con_jev(cfg, grupos, etiquetas, temas,
                                           workers=2)
        self.assertEqual(out, [])
        self.assertEqual(temas[1], 'Empleo en Cali')

    def test_fallo_de_jev_no_tumba_la_corrida(self):
        cfg, grupos, etiquetas, temas = self._caso()
        with patch.object(A, '_tema_con_jev', side_effect=RuntimeError('x')):
            out = A.corregir_temas_con_jev(cfg, grupos, etiquetas, temas,
                                           workers=2)
        self.assertEqual(out, [])
        self.assertEqual(temas[1], 'Empleo en Cali')


if __name__ == '__main__':
    unittest.main()
