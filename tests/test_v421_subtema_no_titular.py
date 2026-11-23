"""Tests v4.21: el subtema nunca es el titular.

- validar() ya no marca copia_titular cuando el subtema es una etiqueta
  válida por sí misma (falsos positivos como «Estado de salud de Yamid
  Amat» dentro del titular largo).
- El titular copiado tal cual, la cita como etiqueta y la pregunta como
  etiqueta se reparan de forma determinista (sin gastar llamada LLM).
- _subtema_desde_titulo limpia marcas de titular-noticia (¡Atención!,
  ¿…?, citas tras los dos puntos) y recorta verbos iniciales.
"""
import unittest

import analyzer_tono_tema as A


def duros(problemas):
    return [p for p in problemas if not p.startswith('revisar_anclaje')]


class TestFalsosPositivosCopiaTitular(unittest.TestCase):
    def test_etiqueta_valida_dentro_del_titular_no_es_copia(self):
        # Caso real del dossier: la etiqueta aparece dentro del titular pero
        # es una etiqueta nominal válida; no debe ir a reparación LLM.
        pr = A.validar(
            "Estado de salud de Yamid Amat", "Positivo",
            ["Actualización sobre el estado de salud crítico del periodista "
             "Yamid Amat"])
        self.assertNotIn('copia_titular', duros(pr))

    def test_etiqueta_identica_a_titular_etiqueta_no_se_toca(self):
        # Si el titular ya era una etiqueta, no hay nada que reparar.
        self.assertEqual(
            A.reparar_subtema_determinista("Estado de salud de Yamid Amat",
                                           "Estado de salud de Yamid Amat"), '')

    def test_es_etiqueta_valida(self):
        self.assertTrue(A._es_etiqueta_valida("Estado de salud de Yamid Amat"))
        self.assertTrue(A._es_etiqueta_valida("Nacimiento de Gael en Santa Fe"))
        # Titular-noticia con interjección: no es etiqueta válida.
        self.assertFalse(A._es_etiqueta_valida(
            "¡Atención! Yamid Amat fue hospitalizado en Bogotá"))
        self.assertFalse(A._es_etiqueta_valida("¿Cuántos años tiene Yamid Amat?"))

    def test_validar_no_entra_en_recursion(self):
        # validar -> _es_etiqueta_valida -> validar(_con_copia=False) termina.
        pr = A.validar("Estado de salud de Yamid Amat", "Positivo",
                       ["Estado de salud de Yamid Amat"])
        self.assertIsInstance(pr, list)


class TestCopiaRealSeRepara(unittest.TestCase):
    def test_titular_verbatim_largo_se_marca(self):
        t = "¡Atención! Yamid Amat fue hospitalizado en Bogotá y permanece en UCI"
        pr = duros(A.validar(t, "Neutro", [t]))
        self.assertIn('copia_titular', pr)

    def test_titular_verbatim_se_repara_determinista(self):
        t = "¡Atención! Yamid Amat fue hospitalizado en Bogotá y permanece en UCI"
        # v4.67: lo que se derivaría del titular es un trozo verbatim (copia),
        # así que la reparación sin modelo devuelve '' y el caso pasa a la
        # reformulación LLM, que redacta el hecho sin recortar el titular.
        self.assertEqual(A.reparar_subtema_determinista(t, t), '')
        # Con un titular corto sí hay reparación limpia sin modelo.
        t2 = "¡Atención! Yamid Amat fue hospitalizado en Bogotá"
        nuevo = A.reparar_subtema_determinista(t2, t2)
        self.assertTrue(nuevo)
        self.assertNotIn('¡', nuevo)
        self.assertLessEqual(len(nuevo.split()), 7)
        self.assertEqual(duros(A.validar(nuevo, 'Neutro', [t2])), [])

    def test_cita_como_etiqueta_se_repara(self):
        # Caso real del dossier Fundación Santa Fe.
        s = '"Septiembre era el momento perfecto"'
        t = ('Lina Tejeiro revela detalles del nacimiento de su hijo Gael: '
             '"Septiembre era el momento perfecto"')
        # La derivación sigue tomando el hecho previo a la cita, sin verbo
        # inicial...
        self.assertEqual(A._subtema_desde_titulo(t),
                         "Detalles del nacimiento de su hijo Gael")
        # v4.67: lo que se derivaría del titular es un trozo verbatim (copia),
        # así que la reparación sin modelo devuelve '' y el caso pasa a la
        # reformulación LLM, que redacta el hecho sin recortar el titular.
        self.assertEqual(A.reparar_subtema_determinista(s, t), '')

    def test_pregunta_como_etiqueta_se_repara(self):
        s = "¿Cuántos años tiene Yamid Amat?"
        t = "¿Cuántos años tiene Yamid Amat? Inició en la radio a los 20"
        derivado = A._subtema_desde_titulo(t)
        self.assertNotIn('?', derivado)
        self.assertNotIn('¿', derivado)
        # v4.67: lo que se derivaría del titular es un trozo verbatim (copia),
        # así que la reparación sin modelo devuelve '' y el caso pasa a la
        # reformulación LLM, que redacta el hecho sin recortar el titular.
        self.assertEqual(A.reparar_subtema_determinista(s, t), '')

    def test_cita_parcial_en_etiqueta_nominal_se_descomilla(self):
        # v4.34 (regla del usuario: el subtema jamás lleva comillas): la cita
        # parcial informativa se conserva pero sin las comillas.
        s = "Lanzamiento de álbum 'Arriba La L'"
        t = "Ladrones supera la adversidad y consolida su sonido con 'Arriba La L'"
        self.assertEqual(A.reparar_subtema_determinista(s, t),
                         "Lanzamiento de álbum Arriba La L")

    def test_sin_titulo_no_hay_reparacion(self):
        self.assertEqual(A.reparar_subtema_determinista("Algo", ""), '')
        self.assertEqual(A.reparar_subtema_determinista("", "Título"), '')


class TestSubtemaDesdeTitulo(unittest.TestCase):
    def test_quita_interjeccion(self):
        r = A._subtema_desde_titulo(
            "¡Atención! Yamid Amat fue hospitalizado en Bogotá y permanece en UCI")
        self.assertNotIn('¡', r)
        self.assertFalse(r.lower().startswith('atención'))

    def test_ultima_hora(self):
        r = A._subtema_desde_titulo("Última hora: capturan a alias Cholo en Santa Marta")
        self.assertFalse(r.lower().startswith('última hora'))

    def test_prefiere_segmento_no_cita(self):
        r = A._subtema_desde_titulo(
            'Lina Tejeiro revela detalles del nacimiento de su hijo Gael: '
            '"Septiembre era el momento perfecto"')
        self.assertNotIn('Septiembre', r)

    def test_frase_destacada_sin_comillas_toma_el_hecho(self):
        # El titular trae la frase destacada sin comillas tras los dos
        # puntos: el hecho (primer segmento, con clase de evento) gana.
        r = A._subtema_desde_titulo(
            'Lina Tejeiro revela detalles del nacimiento de su hijo Gael: '
            'Septiembre era el momento perfecto')
        self.assertEqual(r, "Detalles del nacimiento de su hijo Gael")

    def test_dos_puntos_normal_toma_el_ultimo(self):
        # Sin frase destacada, el hecho sigue tras los dos puntos (v4.18).
        r = A._subtema_desde_titulo("Salud: el ministerio anunció nuevas medidas")
        # v1.29: un subtema nunca es «sujeto + verbo» (regla del usuario).
        self.assertEqual(r, "Nuevas medidas")

    def test_mantiene_comportamiento_dos_puntos_normal(self):
        # Regresión v4.18: sin cita, el hecho sigue tras los dos puntos.
        r = A._subtema_desde_titulo(
            "Política: así fue el ascenso político de Estefanel Gutiérrez")
        self.assertEqual(r, "Ascenso político de Estefanel Gutiérrez")

    def test_titular_pregunta(self):
        r = A._subtema_desde_titulo(
            "¿Cuántos años tiene Yamid Amat? Inició en la radio a los 20")
        self.assertNotIn('?', r)
        self.assertNotIn('¿', r)

    def test_solo_preguntas(self):
        # v1.14/v1.18 (regla del usuario): «Hecho informativo» no es un subtema
        # aceptado. Sin hecho, _subtema_desde_titulo devuelve '' y el respaldo
        # garantizado es _subtema_especifico (texto propio de la nota).
        self.assertEqual(A._subtema_desde_titulo("¿Quién? ¿Cómo?"), "")
        r = A._subtema_especifico("¿Quién? ¿Cómo?",
                                  ["La Alcaldía abrió la convocatoria de becas para jóvenes."])
        self.assertTrue(r)
        self.assertNotEqual(r, "Hecho informativo")

    def test_recorta_verbo_inicial_tras_corte(self):
        r = A._subtema_desde_titulo(
            "Lina Tejeiro revela detalles del nacimiento de su hijo Gael en Bogotá")
        self.assertFalse(r.split()[0].lower() in A._VERBO_INFO_SING)

    def test_vacio(self):
        # v1.14/v1.18 (regla del usuario): «Hecho informativo» no es un subtema
        # aceptado. Sin hecho, _subtema_desde_titulo devuelve '' y el respaldo
        # garantizado es _subtema_especifico (texto propio de la nota).
        self.assertEqual(A._subtema_desde_titulo(""), "")
        self.assertEqual(A._subtema_desde_titulo(None), "")
        self.assertTrue(A._subtema_especifico("", []))
        self.assertNotEqual(A._subtema_especifico("", []), "Hecho informativo")

    def test_conserva_siglas(self):
        r = A._subtema_desde_titulo(
            "LA MÚSICA CLÁSICA INDEPENDIENTE SE TOMA ESPACIOS NO CONVENCIONALES")
        self.assertLessEqual(len(r.split()), 7)
        self.assertTrue(r[0].isupper())


class TestIntegracionReparacion(unittest.TestCase):
    def test_solo_mecanicos_no_pasan_al_llm(self):
        # Los problemas mecánicos puros se resuelven sin el modelo: la lista
        # de pendientes para la vuelta LLM queda vacía.
        casos = [
            (1, '¡Atención! Yamid Amat fue hospitalizado en Bogotá',
             '¡Atención! Yamid Amat fue hospitalizado en Bogotá'),
            (2, 'Lina Tejeiro revela detalles: "Septiembre era el momento"',
             '"Septiembre era el momento"'),
            (3, 'Reunión de expertos en crimen',
             'Reunión de expertos en crimen'),  # subtema_vago: va al LLM
        ]
        fallos = []
        for gid, titulo, sub in casos:
            pr = duros(A.validar(sub, 'Neutro', [titulo]))
            fallos.append({'grupo': gid, 'titulo': titulo,
                           'sub_tema': sub, 'problemas': pr})
        # Sanidad: el caso 3 no es mecánico, los otros dos sí.
        self.assertTrue(all(p.split('(')[0] in A._PROBLEMAS_MECANICOS
                            for p in fallos[0]['problemas']))
        self.assertTrue(all(p.split('(')[0] in A._PROBLEMAS_MECANICOS
                            for p in fallos[1]['problemas']))
        pendientes = []
        reparados = {}
        for f in fallos:
            base = [p.split('(')[0] for p in f['problemas']]
            if base and all(b in A._PROBLEMAS_MECANICOS for b in base):
                nuevo = A.reparar_subtema_determinista(f['sub_tema'], f['titulo'])
                if nuevo:
                    reparados[f['grupo']] = nuevo
                    continue
            pendientes.append(f)
        # v4.67: la cita (grupo 2) se deriva en un trozo verbatim del
        # titular, que también es copia: va a la vuelta LLM con el vago.
        self.assertEqual([f['grupo'] for f in pendientes], [2, 3])
        self.assertEqual(set(reparados), {1})
        for g, nuevo in reparados.items():
            tit = [f['titulo'] for f in fallos if f['grupo'] == g][0]
            self.assertEqual(duros(A.validar(nuevo, 'Neutro', [tit])), [])


if __name__ == '__main__':
    unittest.main()
