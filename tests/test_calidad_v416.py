"""Pruebas v4.16: freno anti-encadenamiento, Negativo calibrado, contexto mínimo."""
import unittest

from rapidfuzz import fuzz

import analyzer_tono_tema as az


def _filas(titulos):
    return [{'Título': t, 'CuerpoEs': 'cuerpo unico %d zzzqqq' % i,
             'Contexto analizado': ''}
            for i, t in enumerate(titulos)]


def _grupo_de(grupos, idx):
    for g in grupos:
        if idx in g['idxs']:
            return g['grupo']
    return None


class TestFrenoEncadenamiento(unittest.TestCase):
    # Hechos distintos que solo comparten palabras omnipresentes del dossier
    # (terremoto/cali/hoteles/tras/emergencia, df>8). El código viejo los
    # fusionaba (fuzz>=0.92 y >=3 palabras compartidas); v4.16 exige puente
    # distintivo.
    X = "Terremoto en Cali: hoteles afectados tras la emergencia"
    Y = "Terremoto en Cali: hoteles cerrados tras la emergencia"
    FILLERS = [
        "Terremoto en Cali: hoteles tras la emergencia anuncian descuentos solidarios",
        "Hoteles de Cali tras la emergencia del terremoto lanzan campaña",
        "Terremoto en Cali: tras la emergencia hoteles piden créditos",
        "Cali tras el terremoto: hoteles superan la emergencia con protocolos",
        "Hoteles en Cali tras la emergencia del terremoto activan plan",
        "Terremoto en Cali: ocupación hotelera tras la emergencia cae",
        "Tras la emergencia del terremoto hoteles de Cali reabren",
        "Hoteles de Cali: tras el terremoto la emergencia continúa",
        "Terremoto en Cali: gremios hoteleros tras la emergencia unidos",
        "Cali: hoteles tras la emergencia del terremoto refuerzan seguridad",
        "Emergencia en Cali tras el terremoto: hoteles solidarios",
    ]

    def _ctit(self, t):
        return {w for w in az.words(t) if w not in az.GENERIC_TITULO}

    def test_puente_generico_no_encadena(self):
        # 1) el par cumple la condición VIEJA de fusión (documenta la regresión)
        cx, cy = self._ctit(self.X), self._ctit(self.Y)
        s1, s2 = ' '.join(sorted(cx)), ' '.join(sorted(cy))
        viejo = max(fuzz.ratio(s1, s2), fuzz.token_sort_ratio(s1, s2),
                    fuzz.token_set_ratio(s1, s2)) / 100
        self.assertGreaterEqual(len(cx & cy), 3)
        self.assertGreaterEqual(viejo, 0.92,
                                'el par debe haber fusionado con el código viejo')
        # 2) con v4.16 quedan separados: el puente es solo genérico
        grupos, _ = az.construir_grupos(_filas([self.X, self.Y] + self.FILLERS), {})
        self.assertNotEqual(_grupo_de(grupos, 0), _grupo_de(grupos, 1),
                            'hechos distintos unidos solo por genéricos deben separarse')

    def test_puente_distintivo_si_une(self):
        # Mismo hecho con palabras distintivas compartidas (caso ANATO real):
        # la fusión legítima no debe romperse.
        a = "ANATO pide reducir el IVA y dar créditos para reactivar el turismo"
        b = "ANATO propone IVA reducido y créditos para reactivar turismo"
        grupos, _ = az.construir_grupos(_filas([a, b]), {})
        self.assertEqual(_grupo_de(grupos, 0), _grupo_de(grupos, 1),
                         'mismo hecho con puente distintivo debe seguir unido')

    def test_traslado_no_se_mezcla_con_afectados(self):
        # Réplica del caso real 'Traslado de adultos mayores' vs hoteles dañados.
        traslado = [
            "Adultos mayores damnificados serán trasladados de albergues a hoteles en Cali tras el terremoto",
            "Traslado de adultos mayores de albergues a hoteles en Cali por el terremoto",
            "Alcaldía de Cali traslada adultos mayores damnificados a hoteles tras el terremoto",
            "De albergues a hoteles: inicia el traslado de adultos mayores en Cali tras el terremoto",
        ]
        afectados = [
            "36 hoteles de Cali resultaron afectados tras el terremoto",
            "Hoteles de Cali afectados por el terremoto: balance de daños",
            "Terremoto en Cali deja 36 hoteles afectados y daños millonarios",
        ]
        rows = _filas(traslado + afectados + self.FILLERS)
        grupos, _ = az.construir_grupos(rows, {})
        fams = {}
        for gi, t in enumerate(traslado + afectados):
            fams[gi] = _grupo_de(grupos, gi)
        g_traslado = {fams[i] for i in range(4)}
        g_afectados = {fams[4 + i] for i in range(3)}
        self.assertTrue(g_traslado & g_afectados == set(),
                        'traslado y hoteles dañados no deben compartir grupo')
        # la familia legítima sigue junta
        mayor = max(g_traslado, key=lambda g: sum(1 for i in range(4) if fams[i] == g))
        self.assertGreaterEqual(sum(1 for i in range(4) if fams[i] == mayor), 3,
                                'la familia de traslado debe conservarse')


def _etiquetas(tonos):
    return {i + 1: {'tono': t} for i, t in enumerate(tonos)}


class TestNegativoCalibrado(unittest.TestCase):
    def test_mencion_incidental_baja_a_neutro(self):
        # Caso real: fotomultas/Cotelco, único Negativo del dossier.
        grupos = [{'grupo': 1,
                   'titulo': "La ministra Noguera está casada con un narcotraficante: Carrillo arremete",
                   'contexto': "Carlos Carrillo responde con una frase demoledora sobre las "
                               "fotomultas. Se menciona a Cotelco entre los gremios invitados.",
                   'texto': ''}]
        et = _etiquetas(['Negativo'])
        bajados = az.aplicar_regla_negativo_sin_blanco(grupos, et, 'Cotelco', [])
        self.assertEqual(et[1]['tono'], 'Neutro')
        self.assertEqual(bajados, [1])

    def test_critica_a_la_marca_se_mantiene(self):
        grupos = [{'grupo': 1,
                   'titulo': "Cuestionan a Cotelco por cobros excesivos",
                   'contexto': "Expositores cuestionaron a Cotelco por los cobros excesivos "
                               "en la feria de turismo.",
                   'texto': ''}]
        et = _etiquetas(['Negativo'])
        bajados = az.aplicar_regla_negativo_sin_blanco(grupos, et, 'Cotelco', [])
        self.assertEqual(et[1]['tono'], 'Negativo')
        self.assertEqual(bajados, [])

    def test_marca_protagonista_se_mantiene(self):
        grupos = [{'grupo': 1,
                   'titulo': "Cotelco reporta caída del 40% en reservas",
                   'contexto': "Cotelco reportó una caída del 40% en las reservas. "
                               "Cotelco atribuyó la baja a la recesión económica.",
                   'texto': ''}]
        et = _etiquetas(['Negativo'])
        bajados = az.aplicar_regla_negativo_sin_blanco(grupos, et, 'Cotelco', [])
        self.assertEqual(et[1]['tono'], 'Negativo')
        self.assertEqual(bajados, [])

    def test_no_toca_neutro_ni_positivo(self):
        grupos = [{'grupo': 1, 'titulo': "Nota cualquiera", 'contexto': "Cotelco.",
                   'texto': ''}]
        et = _etiquetas(['Neutro'])
        az.aplicar_regla_negativo_sin_blanco(grupos, et, 'Cotelco', [])
        self.assertEqual(et[1]['tono'], 'Neutro')


class TestContextoMinimo(unittest.TestCase):
    TIT = "18 países se encuentran en Bogotá para el IV Mundial de Tejo"
    TEXTO = ("El tejo, símbolo de la cultura popular colombiana, vuelve a ser punto "
             "de encuentro internacional con el IV Mundial de Tejo el 2 de octubre en "
             "Bogotá. La competencia reunirá delegaciones de 18 países en Tejo La Embajada.")

    def test_fragmento_se_completa(self):
        ctx = az._contexto_minimo_util(
            "Edwin Bernal, director ejecutivo de Cotelco.", self.TIT, self.TEXTO)
        self.assertIn("Mundial de Tejo", ctx)
        self.assertGreaterEqual(len(ctx), 140)

    def test_contexto_util_se_conserva(self):
        bueno = ("Cotelco participó en el conversatorio sobre turismo con 200 asistentes. "
                 "El gremio presentó sus propuestas de reactivación y el director "
                 "ejecutivo explicó el plan de trabajo para el segundo semestre del año.")
        self.assertEqual(az._contexto_minimo_util(bueno, self.TIT, self.TEXTO), bueno)

    def test_sin_texto_devuelve_titulo(self):
        ctx = az._contexto_minimo_util("Cotelco.", self.TIT, "")
        self.assertIn("Mundial de Tejo", ctx)


class TestContextoExactoCalibrado(unittest.TestCase):
    TEXTO = (
        "El turismo en el Valle creció 12% este año. "
        "Cotelco presentó sus propuestas de reactivación ante la asamblea. "
        "Los hoteleros esperan una buena temporada de fin de año. "
        "Según Cotelco, la ocupación llegó al 80% en agosto. "
        "El gremio seguirá trabajando en promoción internacional."
    )

    def test_extrae_solo_oraciones_mencion(self):
        ctx = az._contexto_exacto_marca(self.TEXTO, "Título", "Cotelco", [])
        self.assertIn("Cotelco presentó sus propuestas", ctx)
        self.assertIn("Según Cotelco, la ocupación", ctx)
        self.assertNotIn("creció 12%", ctx)
        self.assertNotIn("buena temporada", ctx)
        self.assertNotIn("promoción internacional", ctx)

    def test_verbatim_con_tildes(self):
        ctx = az._contexto_exacto_marca(self.TEXTO, "T", "Cotelco", [])
        self.assertIn("reactivación", ctx)  # tilde conservada

    def test_dedup_misma_oracion(self):
        txt = "Cotelco abre convocatoria. Cotelco abre convocatoria. Otro tema."
        ctx = az._contexto_exacto_marca(txt, "T", "Cotelco", [])
        self.assertEqual(ctx.count("Cotelco abre convocatoria"), 1)

    def _transcript_largo(self, n=20):
        return " ".join(
            "El periodista %d entrevistó a Cotelco sobre la ocupación hotelera "
            "y las proyecciones del sector para la temporada." % i for i in range(n))

    def test_tope_radiodifusion(self):
        txt = self._transcript_largo()
        for medio in ["Radio", "Televisión", "Aire", "Cable", "AM", "FM", "TV"]:
            ctx = az._contexto_exacto_marca(txt, "T", "Cotelco", [],
                                            tipo_medio=medio)
            self.assertLessEqual(len(ctx), 1200, "medio %s" % medio)
            self.assertIn("Cotelco", ctx)

    def test_tope_general(self):
        txt = self._transcript_largo(30)
        for medio in ["Prensa", "Internet", "Revistas", ""]:
            ctx = az._contexto_exacto_marca(txt, "T", "Cotelco", [],
                                            tipo_medio=medio)
            self.assertLessEqual(len(ctx), 2000, "medio %s" % medio)

    def test_oraciones_completas_no_corte_a_media_palabra(self):
        txt = self._transcript_largo(30)
        ctx = az._contexto_exacto_marca(txt, "T", "Cotelco", [],
                                        tipo_medio="Radio")
        # cada oración del extracto es una oración completa del original
        originales = [o.strip() for o in txt.split(". ") ]
        for pedazo in [p.strip() for p in ctx.split(". ") if p.strip()]:
            self.assertTrue(
                any(o.startswith(pedazo[:40]) for o in originales),
                "fragmento cortado: %r" % pedazo[:60])

    def test_sin_mencion_devuelve_titulo(self):
        ctx = az._contexto_exacto_marca("Texto sin la marca.", "Mi titular",
                                        "Cotelco", [])
        self.assertEqual(ctx, "Mi titular")

    def test_alias_y_vocero_cuentan_como_mencion(self):
        txt = "El gremio hotelero habló. Asotelco firmó el convenio. Fin."
        ctx = az._contexto_exacto_marca(txt, "T", "Cotelco", ["Asotelco"])
        self.assertIn("Asotelco firmó el convenio", ctx)
        self.assertNotIn("El gremio hotelero habló", ctx)


if __name__ == '__main__':
    unittest.main()
