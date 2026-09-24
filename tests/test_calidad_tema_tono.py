"""Pruebas de calidad de temas/subtemas/tono (mejoras 2026-09-20).

Cubren los cinco frentes detectados al comparar la salida de la app con el
análisis manual del dossier de Unisimón:
  P1. clustering de familias: no unir asuntos distintos por stems genéricos
      ("inteligencia artificial", "congreso", "internacional") ni por encadenamiento.
  P2. temas: nunca empezar con preposición; nunca quedar idénticos al subtema.
  P3. subtemas: no copiar el titular en el mismo orden; no ser vagos.
  P4. tono: el vocero citado como fuente experta es Positivo.
  P5. tono: un hecho = un tono (reconciliación entre grupos del mismo hecho).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyzer_tono_tema as az


class TestTemas(unittest.TestCase):
    def test_tema_no_empieza_con_preposicion(self):
        self.assertIn('empieza_preposicion',
                      az.problemas_calidad_tema('Para Carnaval 2027'))
        self.assertIn('empieza_preposicion',
                      az.problemas_calidad_tema('De cuidado territorial'))
        self.assertNotIn('empieza_preposicion',
                         az.problemas_calidad_tema('Carnaval 2027'))
        self.assertNotIn('empieza_preposicion',
                         az.problemas_calidad_tema('Cuidado territorial'))

    def test_reparar_inicio_preposicional(self):
        self.assertEqual(az._reparar_inicio_preposicional('Para Carnaval 2027'),
                         'Carnaval 2027')
        self.assertEqual(az._reparar_inicio_preposicional('De cuidado territorial'),
                         'Cuidado territorial')
        # Sin preposición inicial: no hay nada que reparar.
        self.assertEqual(az._reparar_inicio_preposicional('Salud y prevención'), '')

    def test_asegurar_tema_repara_preposicion(self):
        t = az._asegurar_tema_texto(
            'Para Carnaval 2027', ['Diana Acosta'],
            ['Carnaval de Barranquilla 2027: Diana Acosta revela qué viene'])
        self.assertEqual(t, 'Carnaval 2027')

    def test_tema_distinto_de_subtema(self):
        self.assertFalse(az._tema_distinto_de_subtema(
            'Conversatorio sobre Alzheimer', 'Conversatorio sobre Alzheimer'))
        self.assertFalse(az._tema_distinto_de_subtema(
            'Economía circular en cocina', 'Economía circular en cocina'))
        self.assertTrue(az._tema_distinto_de_subtema(
            'Salud y bienestar', 'Conversatorio sobre Alzheimer'))


class TestSubtemas(unittest.TestCase):
    def test_copia_titular_titular_largo_verbatim(self):
        # v4.21: el titular copiado tal cual (largo, no es etiqueta válida)
        # sí se marca como copia_titular.
        t = ('La medicina que viene será multimodal y personalizada, '
             'coinciden los expertos')
        pr = az.validar(t, 'Neutro', [t])
        self.assertIn('copia_titular', pr)

    def test_recorte_nominal_valido_no_es_copia(self):
        # v4.21: un recorte en el mismo orden del titular que ya es una
        # etiqueta nominal válida no se marca (antes generaba 46 falsos
        # positivos en el dossier Fundación Santa Fe, p. ej. «Estado de
        # salud de Yamid Amat»).
        pr = az.validar(
            'Medicina multimodal y personalizada', 'Neutro',
            ['La medicina que viene será multimodal y personalizada, '
             'coinciden los expertos'])
        self.assertNotIn('copia_titular', pr)

    def test_copia_titular_mismo_orden_etiqueta_valida(self):
        # v4.21: «Conexión entre educación media y superior» es una etiqueta
        # válida por sí misma; que siga el orden del titular no la invalida.
        pr = az.validar(
            'Conexión entre educación media y superior', 'Neutro',
            ['La sustancial conexión entre la educación media y la superior'])
        self.assertNotIn('copia_titular', pr)

    def test_reformulacion_valida_no_es_copia(self):
        # Mismo hecho, otro orden y otras palabras: reformulación legítima.
        pr = az.validar(
            'Desafíos de la criminología con IA', 'Positivo',
            ['Criminología frente a la IA: expertos advierten sobre los nuevos '
             'desafíos'])
        self.assertNotIn('copia_titular', pr)

    def test_nombre_de_evento_no_es_copia(self):
        # Los nombres de evento se repiten tal cual por naturaleza.
        pr = az.validar(
            'Cumbre internacional de criminología', 'Positivo',
            ['Cumbre internacional de criminología reúne a 50 académicos en la '
             'Universidad Simón Bolívar'])
        self.assertNotIn('copia_titular', pr)

    def test_subtema_vago(self):
        pr = az.validar(
            'Reunión de expertos en crimen', 'Positivo',
            ['Barranquilla reúne expertos que buscan respuestas frente a la '
             'evolución del crimen'])
        self.assertIn('subtema_vago', pr)

    def test_subtema_con_objeto_no_es_vago(self):
        pr = az.validar(
            'Cumbre internacional de criminología', 'Positivo',
            ['Cumbre internacional de criminología reúne a 50 académicos'])
        self.assertNotIn('subtema_vago', pr)


class TestClusteringFamilias(unittest.TestCase):
    def test_pisa_no_se_une_a_criminologia(self):
        # El puente "inteligencia artificial" (mención al pasar) no une.
        items = [
            {'sub_tema': 'Retroceso en resultados PISA',
             'evidencia': ('Retroceso en resultados PISA Pruebas PISA evidencian '
                           'retroceso educativo y preocupación por uso de '
                           'inteligencia artificial')},
            {'sub_tema': 'Avances y controles en IA',
             'evidencia': ('Avances y controles en IA Inteligencia artificial '
                           'requiere avances con controles éticos')},
            {'sub_tema': 'Cumbre internacional de criminología',
             'evidencia': ('Cumbre internacional de criminología Cumbre reúne '
                           'a 50 académicos de criminología en la universidad')},
            {'sub_tema': 'Reunión de expertos en crimen',
             'evidencia': ('Reunión de expertos en crimen Barranquilla reúne '
                           'expertos que buscan respuestas frente a la evolución '
                           'del crimen')},
        ]
        fams = az.cluster_familias_subtema(items)
        fam_de = {it['sub_tema']: i for i, f in enumerate(fams) for it in f}
        self.assertNotEqual(fam_de['Retroceso en resultados PISA'],
                            fam_de['Cumbre internacional de criminología'])
        self.assertNotEqual(fam_de['Avances y controles en IA'],
                            fam_de['Cumbre internacional de criminología'])
        # La cumbre sí se une con la reunión de expertos (núcleo: crimen).
        self.assertEqual(fam_de['Cumbre internacional de criminología'],
                         fam_de['Reunión de expertos en crimen'])

    def test_juventud_no_se_une_a_suicidio(self):
        # "joven" une a la familia de juventud; el suicidio queda aparte.
        items = [
            {'sub_tema': 'Desafíos para la juventud',
             'evidencia': ('Desafíos para la juventud Desafíos para la juventud '
                           'en Barranquilla: empleo y educación, los puntos a '
                           'tener en cuenta')},
            {'sub_tema': 'Alto desempleo juvenil en Barranquilla',
             'evidencia': ('Alto desempleo juvenil en Barranquilla Desempleo '
                           'juvenil llega al 18,8 por ciento: miles de jóvenes '
                           'fuera del empleo formal')},
            {'sub_tema': 'Semana de prevención del suicidio',
             'evidencia': ('Semana de prevención del suicidio Asociación de '
                           'psiquiatría promueve prevención del suicidio')},
        ]
        fams = az.cluster_familias_subtema(items)
        fam_de = {it['sub_tema']: i for i, f in enumerate(fams) for it in f}
        self.assertEqual(fam_de['Desafíos para la juventud'],
                         fam_de['Alto desempleo juvenil en Barranquilla'])
        self.assertNotEqual(fam_de['Desafíos para la juventud'],
                            fam_de['Semana de prevención del suicidio'])

    def test_congreso_no_une_eventos_distintos(self):
        # "congreso"/"internacional" son genéricos: no unen.
        items = [
            {'sub_tema': 'Cumbre internacional de criminología',
             'evidencia': ('Cumbre internacional de criminología Cumbre de '
                           'criminología reúne académicos')},
            {'sub_tema': 'Congreso de innovación en psicología',
             'evidencia': ('Congreso de innovación en psicología Congreso '
                           'internacional de innovación en psicología en Cúcuta')},
        ]
        fams = az.cluster_familias_subtema(items)
        fam_de = {it['sub_tema']: i for i, f in enumerate(fams) for it in f}
        self.assertNotEqual(fam_de['Cumbre internacional de criminología'],
                            fam_de['Congreso de innovación en psicología'])


class TestGuardaPositivaVocero(unittest.TestCase):
    def test_vocero_citado_como_fuente_experta(self):
        grupos = [{'grupo': 1,
                   'titulo': 'Rector advierte sobre pantallas',
                   'contexto': ('La utilización masiva de las pantallas y el mal '
                                'uso de la inteligencia artificial por parte de '
                                'los niños han limitado las capacidades de los '
                                'estudiantes, consideró el rector de la '
                                'Universidad Simón Bolívar, José Consuegra.'),
                   'texto': ''}]
        etiquetas = {1: {'sub_tema': 'Impacto de IA en educación',
                         'tono': 'Neutro'}}
        out = az.aplicar_guarda_positiva(
            grupos, etiquetas, 'Universidad Simón Bolívar', ['Unisimón'],
            voceros=['José Consuegra'])
        self.assertEqual(etiquetas[1]['tono'], 'Positivo')
        self.assertEqual(out, [1])

    def test_revelo_no_dispara_la_guarda(self):
        # "reveló" introduce hallazgos (a veces alarmantes), no voz experta.
        grupos = [{'grupo': 2,
                   'titulo': 'Muerte súbita en deportistas',
                   'contexto': ('El investigador docente de la Universidad '
                                'Simón Bolívar reveló que ahora es más común '
                                'encontrar muertes súbitas en los deportistas.'),
                   'texto': ''}]
        etiquetas = {2: {'sub_tema': 'Muerte súbita en deportistas',
                         'tono': 'Neutro'}}
        out = az.aplicar_guarda_positiva(
            grupos, etiquetas, 'Universidad Simón Bolívar', ['Unisimón'])
        self.assertEqual(etiquetas[2]['tono'], 'Neutro')
        self.assertEqual(out, [])


class TestUnificarTonoMismoHecho(unittest.TestCase):
    def test_mayoria_gana(self):
        grupos = [{'grupo': 1}, {'grupo': 2}, {'grupo': 3}]
        etiquetas = {1: {'sub_tema': 'Congreso de suicidología', 'tono': 'Positivo'},
                     2: {'sub_tema': 'Congreso de suicidología', 'tono': 'Neutro'},
                     3: {'sub_tema': 'Congreso de suicidología', 'tono': 'Neutro'}}
        n = az.unificar_tono_mismo_hecho(grupos, etiquetas)
        self.assertEqual([etiquetas[g]['tono'] for g in (1, 2, 3)],
                         ['Neutro', 'Neutro', 'Neutro'])
        self.assertEqual(n, 1)

    def test_empate_baja_a_neutro(self):
        grupos = [{'grupo': 1}, {'grupo': 2}]
        etiquetas = {1: {'sub_tema': 'Foro de periodismo', 'tono': 'Positivo'},
                     2: {'sub_tema': 'Foro de periodismo', 'tono': 'Neutro'}}
        az.unificar_tono_mismo_hecho(grupos, etiquetas)
        self.assertEqual(etiquetas[1]['tono'], 'Neutro')
        self.assertEqual(etiquetas[2]['tono'], 'Neutro')

    def test_negativo_no_se_toca(self):
        grupos = [{'grupo': 1}, {'grupo': 2}]
        etiquetas = {1: {'sub_tema': 'Denuncia contra la entidad', 'tono': 'Negativo'},
                     2: {'sub_tema': 'Denuncia contra la entidad', 'tono': 'Neutro'}}
        az.unificar_tono_mismo_hecho(grupos, etiquetas)
        self.assertEqual(etiquetas[1]['tono'], 'Negativo')
        self.assertEqual(etiquetas[2]['tono'], 'Neutro')


class TestReglaTragedia(unittest.TestCase):
    CTX_TRAGEDIA_EXPERTO = ('La muerte de Juliana Ávila enluta a Barranquilla. '
                            '"Es más común encontrar muertes súbitas en deportistas '
                            'jóvenes", consideró el investigador docente de la '
                            'Universidad Simón Bolívar.')

    def test_tragedia_experto_no_sube_a_positivo(self):
        # Sin la regla, "consideró el investigador docente de la Universidad"
        # dispararía la guarda positiva. Con tragedia: queda Neutro.
        grupos = [{'grupo': 1, 'titulo': 'Muerte de Juliana Ávila',
                   'contexto': self.CTX_TRAGEDIA_EXPERTO, 'texto': ''}]
        etiquetas = {1: {'sub_tema': 'Muerte de Juliana Ávila', 'tono': 'Neutro'}}
        az.aplicar_guarda_positiva(
            grupos, etiquetas, 'Universidad Simón Bolívar', ['Unisimón'])
        self.assertEqual(etiquetas[1]['tono'], 'Neutro')

    def test_tragedia_experto_baja_positivo_a_neutro(self):
        # Si el LLM lo marcó Positivo, el tope determinista lo baja.
        grupos = [{'grupo': 1, 'titulo': 'Muerte de Juliana Ávila',
                   'contexto': self.CTX_TRAGEDIA_EXPERTO, 'texto': ''}]
        etiquetas = {1: {'sub_tema': 'Muerte de Juliana Ávila', 'tono': 'Positivo'}}
        bajados = az.aplicar_regla_tragedia(
            grupos, etiquetas, 'Universidad Simón Bolívar', ['Unisimón'])
        self.assertEqual(etiquetas[1]['tono'], 'Neutro')
        self.assertEqual(bajados, [1])

    def test_tragedia_con_accion_de_la_marca_se_respeta(self):
        # La marca actúa frente a la tragedia (dona): sigue Positivo.
        grupos = [{'grupo': 2, 'titulo': 'Muerte de Juliana Ávila',
                   'contexto': ('La muerte de Juliana Ávila enluta a Barranquilla. '
                                'La Universidad Simón Bolívar donó ayudas a la '
                                'familia y anunció acompañamiento psicosocial.'),
                   'texto': ''}]
        etiquetas = {2: {'sub_tema': 'Muerte de Juliana Ávila', 'tono': 'Positivo'}}
        bajados = az.aplicar_regla_tragedia(
            grupos, etiquetas, 'Universidad Simón Bolívar', ['Unisimón'])
        self.assertEqual(etiquetas[2]['tono'], 'Positivo')
        self.assertEqual(bajados, [])

    def test_tragedia_no_toca_negativo(self):
        grupos = [{'grupo': 3, 'titulo': 'Denuncian negligencia tras muerte',
                   'contexto': self.CTX_TRAGEDIA_EXPERTO, 'texto': ''}]
        etiquetas = {3: {'sub_tema': 'Muerte de Juliana Ávila', 'tono': 'Negativo'}}
        az.aplicar_regla_tragedia(
            grupos, etiquetas, 'Universidad Simón Bolívar', ['Unisimón'])
        self.assertEqual(etiquetas[3]['tono'], 'Negativo')


class TestEstiloMuse(unittest.TestCase):
    def test_ejemplos_buenos_pasan_el_gate(self):
        # Los ejemplos que le mostramos al modelo deben ser impecables.
        from catalogo_tono_tema import TEMAS_EJEMPLO_BUENOS
        malos = [t for t in TEMAS_EJEMPLO_BUENOS
                 if not az.tema_frase_natural(t)]
        self.assertEqual(malos, [])

    def test_ejemplos_buenos_son_unicos(self):
        from catalogo_tono_tema import TEMAS_EJEMPLO_BUENOS
        self.assertEqual(len(set(TEMAS_EJEMPLO_BUENOS)), len(TEMAS_EJEMPLO_BUENOS))


if __name__ == '__main__':
    unittest.main()
