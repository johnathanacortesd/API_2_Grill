"""Regresión del caso Unisimón 2026-09-23: «La Universidad de Atalaya».

Dos errores graves en una sola nota:
1. Subtema «Investigación sobre Carnaval 2027» (hecho ajeno) en vez del
   campus en Atalaya.
2. Tono Neutro en vez de Positivo (la marca participa en la creación del
   campus).

Causa 1: `unificar_subtemas_noticias_similares` unía grupos por señales
débiles (contexto compartido) y el canon por frecuencia sobrescribía el
subtema correcto de la minoría con el de la mayoría.
Causa 2: `unificar_tono_mismo_hecho` corría DESPUÉS de las guardas, así que
el voto por subtema revertía el Positivo que la guarda sí había detectado.
"""
import unittest

import analyzer_tono_tema as az

BOILERPLATE = ("la universidad simon bolivar emitio un comunicado oficial "
               "esta manana ante los medios regionales")

TITULO_ATALAYA = "La Universidad de Atalaya"
CTX_ATALAYA = (BOILERPLATE + " crearan un campus en la ciudadela atalaya")
SUB_ATALAYA = "Campus universitario en Atalaya"

TITULO_CARNAVAL = "Unisimón investiga las manifestaciones del Carnaval 2027"
CTX_CARNAVAL = (BOILERPLATE + " sobre la investigacion del carnaval 2027")
SUB_CARNAVAL = "Investigación sobre Carnaval 2027"


def _grupo(gid, titulo, contexto):
    return {'grupo': gid, 'titulo': titulo, 'texto': contexto,
            'contexto': contexto, 'contexto_marca': contexto}


class TestCasoAtalayaSubtema(unittest.TestCase):
    def test_hechos_distintos_no_son_mismo_hecho(self):
        self.assertFalse(az._subtemas_mismo_hecho(SUB_ATALAYA, SUB_CARNAVAL))

    def test_union_por_contexto_no_sobrescribe_subtema_ajeno(self):
        # Los tres grupos se unen por contexto compartido (boilerplate), pero
        # el subtema de Atalaya es otro hecho: debe conservarse.
        grupos = [_grupo(1, TITULO_ATALAYA, CTX_ATALAYA),
                  _grupo(2, TITULO_CARNAVAL, CTX_CARNAVAL),
                  _grupo(3, "Carnaval 2027: agenda cultural", CTX_CARNAVAL)]
        etiquetas = {1: {'sub_tema': SUB_ATALAYA, 'tono': 'Neutro'},
                     2: {'sub_tema': SUB_CARNAVAL, 'tono': 'Neutro'},
                     3: {'sub_tema': SUB_CARNAVAL, 'tono': 'Neutro'}}
        # Sanity: la señal débil sí los une (si no, el test no prueba nada).
        self.assertTrue(az._contextos_mismo_hecho(CTX_ATALAYA, CTX_CARNAVAL))
        az.unificar_subtemas_noticias_similares(grupos, etiquetas)
        self.assertEqual(etiquetas[1]['sub_tema'], SUB_ATALAYA)
        self.assertEqual(etiquetas[2]['sub_tema'], SUB_CARNAVAL)
        self.assertEqual(etiquetas[3]['sub_tema'], SUB_CARNAVAL)

    def test_mismo_hecho_si_se_unifica(self):
        # Control: variantes del mismo hecho sí se unifican al canon.
        grupos = [_grupo(1, "Inauguración del laboratorio", "texto uno"),
                  _grupo(2, "Inaguración del laboratorio", "texto dos")]
        etiquetas = {1: {'sub_tema': 'Inauguración del laboratorio',
                         'tono': 'Positivo'},
                     2: {'sub_tema': 'Inaguración del laboratorio',
                         'tono': 'Positivo'}}
        az.unificar_subtemas_noticias_similares(grupos, etiquetas)
        self.assertEqual(etiquetas[1]['sub_tema'],
                         etiquetas[2]['sub_tema'])


class TestCasoAtalayaTono(unittest.TestCase):
    TEXTO_REAL = (
        "La Universidad de Atalaya. Crearán un campus en esa ciudadela, junto "
        "a la escuela Antonio María Claret de unas seis hectáreas, con "
        "participación de las universidades públicas y privadas del "
        "departamento Norte de Santander como ESAP, Unipamplona, Universidad "
        "Francisco de Paula Santander, ISER, Unilibre Cúcuta, Unisimón, "
        "Universidad Santo Tomás, y otras establecidas con programas de "
        "pertinencia local y regional.")

    def _etiquetas_neutras(self):
        return {1: {'sub_tema': SUB_CARNAVAL, 'tono': 'Neutro'},
                2: {'sub_tema': SUB_CARNAVAL, 'tono': 'Neutro'},
                3: {'sub_tema': SUB_CARNAVAL, 'tono': 'Neutro'}}

    def test_guarda_detecta_participacion_en_campus(self):
        grupos = [_grupo(1, TITULO_ATALAYA, self.TEXTO_REAL)]
        etiquetas = {1: {'sub_tema': SUB_ATALAYA, 'tono': 'Neutro'}}
        az.aplicar_guarda_positiva(grupos, etiquetas,
                                   "Universidad Simón Bolívar",
                                   ["Unisimón", "Unisimon"], voceros=[])
        self.assertEqual(etiquetas[1]['tono'], 'Positivo')

    def test_orden_pipeline_voto_antes_que_guardas(self):
        # Orden vigente del pipeline: el voto por hecho corre ANTES que las
        # guardas deterministas, así el Positivo de la guarda no lo revierte
        # la mayoría del subtema. Con el orden antiguo este test daría Neutro.
        grupos = [_grupo(1, TITULO_ATALAYA, self.TEXTO_REAL),
                  _grupo(2, TITULO_CARNAVAL, CTX_CARNAVAL),
                  _grupo(3, "Carnaval 2027: agenda cultural", CTX_CARNAVAL)]
        etiquetas = self._etiquetas_neutras()
        az.unificar_tono_mismo_hecho(grupos, etiquetas)  # 1º: voto
        az.aplicar_guarda_positiva(grupos, etiquetas,  # 2º: guardas
                                   "Universidad Simón Bolívar",
                                   ["Unisimón", "Unisimon"], voceros=[])
        self.assertEqual(etiquetas[1]['tono'], 'Positivo')
        self.assertEqual(etiquetas[2]['tono'], 'Neutro')


if __name__ == '__main__':
    unittest.main()
