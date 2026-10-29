"""v4.63: el subtema es el hecho principal de la noticia completa.

Caso real Comfenalco 2026-10-29 (Dossier_comfenalco_20261029_2202.xlsx):
el modelo devolvió «Conozca cuándo será» para la Feria Laboral de Cali y
validar() lo dejó pasar porque _es_etiqueta_valida lo veía como etiqueta
bien formada. Además, cuatro notas sobre la misma feria (segunda edición,
4 de noviembre, coliseo de Ciudad Córdoba, 400 vacantes) quedaron en 3
grupos porque los titulares comparten pocas palabras.
"""
import unittest

import analyzer_tono_tema as A

TIT_FERIA = ("Más de 400 vacantes estarán disponibles en la Feria Laboral "
              "de Cali: conozca cuándo será")


class TestSubtemaSinHecho(unittest.TestCase):
    def test_colas_de_llamado_se_marcan(self):
        casos = [
            ("Conozca cuándo será", TIT_FERIA),
            ("Conozca cuándo y dónde",
             "Cali tendrá una nueva feria laboral con cientos de vacantes: "
             "conozca cuándo y dónde"),
            ("Así puede participar",
             "Feria Laboral Más Empleos Cali tendrá su segunda edición en "
             "el coliseo de Ciudad Córdoba; así puede participar"),
            ("Qué llevar y quiénes pueden asistir",
             "Feria laboral Cali, 4 de noviembre: qué llevar y quiénes "
             "pueden asistir"),
        ]
        for sub, tit in casos:
            self.assertTrue(A._es_subtema_sin_hecho(sub), sub)
            pr = A.validar(sub, 'Positivo', [tit])
            self.assertIn('subtema_sin_hecho', pr, sub)
            self.assertIn('copia_titular', pr, sub)
        # subtema_sin_hecho es independiente del titular: una llamada es
        # mediocre aunque el titular sea otro.
        self.assertIn('subtema_sin_hecho',
                      A.validar("Conozca cuándo será", 'Positivo',
                                ["Otro titular cualquiera"]))

    def test_lista_de_nombres_sin_hecho_se_marca(self):
        sub = "Comfenalco Valle en Restrepo, Buenaventura y Cali"
        self.assertTrue(A._es_subtema_sin_hecho(sub))
        pr = A.validar(
            sub, 'Positivo',
            ["Jornadas de empleo de Comfenalco Valle en Restrepo, "
             "Buenaventura y Cali"])
        self.assertIn('subtema_sin_hecho', pr)
        self.assertIn('copia_titular', pr)

    def test_recortes_con_hecho_no_se_marcan(self):
        # Regla estrecha: solo llamadas y listas de nombres. Un recorte
        # con sustantivo del hecho sigue siendo síntesis válida y NO se
        # toca (lección v4.62: Yamid / Conexión no se rompen).
        for sub, tit in [
            ("Convocatoria laboral en Restrepo",
             "Comfenalco Valle de la Gente abre convocatoria laboral para "
             "ayudantes de construcción y operadores de maquinaria en Restrepo"),
            ("Estado de salud de Yamid Amat",
             "Actualización sobre el estado de salud de Yamid Amat en clínica"),
            ("Conexión entre educación media y superior",
             "La sustancial conexión entre la educación media y la superior"),
            ("Premio Mujer Cafam",
             "El Valle del Cauca busca a su representante para el Premio "
             "Mujer Cafam 2027"),
            ("Feria laboral con 1.350 vacantes",
             "SENA anuncia feria de empleo con 1.350 vacantes en Barranquilla"),
        ]:
            self.assertFalse(A._es_subtema_sin_hecho(sub), sub)
            self.assertNotIn('subtema_sin_hecho',
                             A.validar(sub, 'Positivo', [tit]), sub)
            self.assertNotIn('copia_titular',
                             A.validar(sub, 'Positivo', [tit]), sub)


class TestDesdeTituloSinLlamado(unittest.TestCase):
    def test_cola_tras_dos_puntos_usa_el_hecho_previo(self):
        r = A._subtema_desde_titulo(TIT_FERIA)
        self.assertNotIn('Conozca', r)
        self.assertIn('Feria', r)
        self.assertFalse(A._es_subtema_sin_hecho(r))

    def test_titulo_sin_hecho_final_cae_a_hecho_informativo(self):
        r = A._subtema_desde_titulo(
            "Jornadas de empleo de Comfenalco Valle en Restrepo, "
            "Buenaventura y Cali")
        self.assertEqual(r, 'Hecho informativo')

    def test_reparacion_determinista_del_caso_reportado(self):
        r = A.reparar_subtema_determinista("Conozca cuándo será", TIT_FERIA)
        self.assertTrue(r)
        self.assertNotIn('Conozca', r)
        self.assertIn('Feria', r)


class TestEventoFechado(unittest.TestCase):
    def _fila(self, titulo, cuerpo):
        return {'Título': titulo, 'Resumen - Aclaracion': cuerpo,
                'Contexto analizado': '', 'is_duplicate': False}

    def test_misma_feria_en_un_solo_grupo_y_controles_solos(self):
        cuerpo = ("Más de 50 empresas participarán en la segunda edición "
                  "de la Feria Laboral Más Empleos Cali, que se realizará "
                  "el miércoles 4 de noviembre en el coliseo de Ciudad "
                  "Córdoba. La jornada ofrecerá más de 400 vacantes.")
        filas = [
            self._fila("Más de 400 vacantes estarán disponibles en la Feria "
                       "Laboral de Cali: conozca cuándo será", cuerpo),
            self._fila("Cali tendrá una nueva feria laboral con cientos de "
                       "vacantes: conozca cuándo y dónde", cuerpo),
            self._fila("Feria laboral Cali, 4 de noviembre: qué llevar y "
                       "quiénes pueden asistir", cuerpo),
            self._fila("Feria Laboral Más Empleos Cali tendrá su segunda "
                       "edición en el coliseo de Ciudad Córdoba; así puede "
                       "participar", cuerpo),
            # Controles: mismo día, otro evento y otra sede -> solos.
            self._fila("Concierto de rock en el estadio de Cali el 4 de "
                       "noviembre",
                       "El 4 de noviembre se realizará un concierto de rock "
                       "en el estadio Pascual Guerrero de Cali con bandas "
                       "locales."),
            self._fila("SENA anuncia feria de empleo con 1.350 vacantes en "
                       "Barranquilla",
                       "El SENA realizará una feria de empleo en "
                       "Barranquilla con 1.350 vacantes para jóvenes."),
        ]
        grupos, mapa = A.construir_grupos(filas, {})
        self.assertEqual(len(grupos), 3)
        gids_feria = {mapa[i] for i in range(4)}
        self.assertEqual(len(gids_feria), 1)
        self.assertNotIn(mapa[4], gids_feria)
        self.assertNotIn(mapa[5], gids_feria)


class TestPromptHechoPrincipal(unittest.TestCase):
    def test_prompt_lote_envia_cuerpo_y_no_prioriza_solo_la_marca(self):
        g = {'grupo': 1, 'n': 1, 'titulo': TIT_FERIA,
             'texto': 'Cuerpo de la noticia sobre la feria.',
             'contexto': 'Las empresas pueden inscribirse por Comfenalco.'}
        msg = A.prompt_lote([g], [])
        self.assertIn('CUERPO DE LA NOTICIA', msg)
        self.assertIn('Cuerpo de la noticia sobre la feria.', msg)
        self.assertNotIn('fuente PRIORITARIA', msg)
        self.assertIn('cola de llamado', msg)

    def test_prompt_reparacion_prohibe_colas(self):
        msg = A.prompt_reparacion([{'grupo': 1, 'titulo': TIT_FERIA,
                                    'texto': 'Cuerpo.',
                                    'sub_tema': 'Conozca cuándo será',
                                    'problemas': ['subtema_sin_hecho']}])
        self.assertIn('cola de llamado', msg)


class TestPuertaFinalSinHecho(unittest.TestCase):
    def test_sin_api_key_la_llamada_cae_a_hecho_informativo(self):
        grupos = [{'grupo': 1, 'titulo': TIT_FERIA}]
        etiquetas = {1: {'sub_tema': 'Conozca cuándo será',
                         'tono': 'Positivo'}}
        rows = [{'Título': TIT_FERIA, 'Medio': 'Elpais.com.co'}]
        A.reparar_copias_finales({}, grupos, etiquetas, rows, {0: 1}, {})
        self.assertEqual(etiquetas[1]['sub_tema'], 'Hecho informativo')


if __name__ == '__main__':
    unittest.main()
