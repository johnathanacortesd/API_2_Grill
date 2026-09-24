"""v4.18: ajustes tras auditoría del dossier real Unisimón (2026-09-23).

- F1: en empate, el subtema ganador es el MÁS LARGO (más específico), igual
  que el criterio v4.15 para el pase LLM. El empate-corto amplificaba un voto
  cruzado del modelo (un voto ajeno y corto le ganaba al correcto).
- F2: `reparar_subtemas_ajenos`: si el subtema de un grupo describe mejor el
  titular de OTRO grupo que el contenido propio, se revierte a un rótulo
  honesto del propio titular. Casos reales: Atalaya->Carnaval, Estefanel->
  Diana Acosta, IA->medicina, psicología->educación médica.
- F3: la guarda positiva detecta participios pasivos («organizado por la
  Universidad…», actor después del verbo).
- F4: la guarda positiva no sube cuando la marca aparece como alma máter
  («su formación en la Universidad…, donde participó…»: participó la persona).
"""
import unittest

from analyzer_tono_tema import (
    _voto_mayoria,
    _subtema_desde_titulo,
    aplicar_guarda_positiva,
    reparar_subtemas_ajenos,
    unificar_subtemas_noticias_similares,
)

BRAND = "Universidad Simón Bolívar"
ALIASES = ["Unisimón"]


def _g(gid, titulo, contexto="", texto=""):
    return {"grupo": gid, "titulo": titulo, "titulos_alt": [],
            "texto": texto, "contexto": contexto,
            "contexto_marca": contexto}


class TestVotoMayoriaEmpateLargo(unittest.TestCase):
    def test_empate_elige_mas_largo(self):
        out = _voto_mayoria([
            {1: {"sub_tema": "Investigación sobre Carnaval 2027", "tono": "Neutro"}},
            {1: {"sub_tema": "Creación de la Universidad de Atalaya", "tono": "Neutro"}},
        ], [1])
        self.assertEqual(out[1]["sub_tema"], "Creación de la Universidad de Atalaya")

    def test_mayoria_sigue_ganando(self):
        out = _voto_mayoria([
            {1: {"sub_tema": "Corto", "tono": "Neutro"}},
            {1: {"sub_tema": "Corto", "tono": "Neutro"}},
        ], [1])
        self.assertEqual(out[1]["sub_tema"], "Corto")


class TestCanonDeterministaEmpateLargo(unittest.TestCase):
    def test_canon_empate_es_mas_largo(self):
        g1 = _g(1, "Apertura del laboratorio", "x")
        g2 = _g(2, "Apertura del laboratorio", "x")
        grupos = [g1, g2]
        etiquetas = {1: {"sub_tema": "Apertura del laboratorio", "tono": "Neutro"},
                     2: {"sub_tema": "Apertura del laboratorio central", "tono": "Neutro"}}
        unificar_subtemas_noticias_similares(grupos, etiquetas)
        self.assertEqual(etiquetas[1]["sub_tema"], "Apertura del laboratorio central")
        self.assertEqual(etiquetas[2]["sub_tema"], "Apertura del laboratorio central")


class TestSubtemaDesdeTitulo(unittest.TestCase):
    def test_corto_quita_articulo(self):
        self.assertEqual(_subtema_desde_titulo("La Universidad de Atalaya"),
                         "Universidad de Atalaya")

    def test_mayusculas_conserva_sigla(self):
        self.assertEqual(_subtema_desde_titulo("LA IA Y LA RECONVERSIÓN LABORAL"),
                         "IA y la reconversión laboral")

    def test_dos_puntos_usa_la_segunda_parte(self):
        t = ("De las calles de un barrio pobre del suroccidente de Barranquilla "
             "a una curul en el Congreso: así fue el ascenso político de Estefanel Gutiérrez")
        self.assertEqual(_subtema_desde_titulo(t),
                         "Ascenso político de Estefanel Gutiérrez")

    def test_vacio(self):
        self.assertTrue(_subtema_desde_titulo(""))


class TestRepararSubtemasAjenos(unittest.TestCase):
    def _dossier(self):
        grupos = [
            _g(7, "La Universidad de Atalaya",
               "Crearán un campus en esa ciudadela, con participación de las "
               "universidades públicas y privadas como Unipamplona, Unisimón y otras."),
            _g(12, "Carnaval de Barranquilla 2027: Diana Acosta revela qué viene para la fiesta",
               "Es uno de los temas de investigación de mi tesis del doctorado que curso en la Universidad Simón Bolívar."),
            _g(13, "De las calles de un barrio pobre a una curul en el Congreso: así fue el ascenso político de Estefanel Gutiérrez",
               "El representante hizo de los barrios parte central de su carrera política."),
            _g(15, "Diana Acosta Siempre ha sabido lo que quiere",
               "Diana Acosta ha sido designada directora de Carnaval de Barranquilla."),
            _g(23, "LA IA Y LA RECONVERSIÓN LABORAL",
               "POR JOSE CONSUEGRA: la inteligencia artificial reta al empleo, los puestos se transformarán."),
            _g(24, "La medicina que viene será multimodal y personalizada, coinciden los expertos",
               "Conclusiones de Salud Consciencia 2026 en la Universidad Simón Bolívar."),
            _g(31, "Unisimón Cúcuta presenta el VIII Congreso Internacional de Innovación en Intervención Psicológica",
               "El programa de Psicología anunció el congreso de intervención psicológica."),
            _g(3, "Conferencia Internacional Caribe de Educación Médica y III Simposio de Simulación Clínica",
               "El programa de Medicina realizará la conferencia de educación médica."),
        ]
        etiquetas = {
            7: {"sub_tema": "Investigación sobre Carnaval 2027", "tono": "Neutro"},
            12: {"sub_tema": "Investigación sobre Carnaval 2027", "tono": "Neutro"},
            13: {"sub_tema": "Diana Acosta directora del Carnaval", "tono": "Neutro"},
            15: {"sub_tema": "Diana Acosta directora del Carnaval", "tono": "Neutro"},
            23: {"sub_tema": "Medicina multimodal y personalizada", "tono": "Neutro"},
            24: {"sub_tema": "Medicina multimodal y personalizada", "tono": "Neutro"},
            31: {"sub_tema": "Conferencia internacional de educación médica", "tono": "Positivo"},
            3: {"sub_tema": "Conferencia internacional de educación médica", "tono": "Positivo"},
        }
        return grupos, etiquetas

    def test_repara_los_cuatro_casos_reales(self):
        grupos, etiquetas = self._dossier()
        n = reparar_subtemas_ajenos(grupos, etiquetas)
        self.assertEqual(n, 4)
        self.assertEqual(etiquetas[7]["sub_tema"], "Universidad de Atalaya")
        self.assertIn("Estefanel", etiquetas[13]["sub_tema"])
        self.assertNotIn("Carnaval", etiquetas[13]["sub_tema"])
        self.assertIn("IA", etiquetas[23]["sub_tema"])
        self.assertNotIn("Medicina", etiquetas[23]["sub_tema"])
        self.assertIn("Psicol", etiquetas[31]["sub_tema"])
        # Los donantes legítimos no se tocan.
        self.assertEqual(etiquetas[12]["sub_tema"], "Investigación sobre Carnaval 2027")
        self.assertEqual(etiquetas[24]["sub_tema"], "Medicina multimodal y personalizada")
        self.assertEqual(etiquetas[3]["sub_tema"], "Conferencia internacional de educación médica")

    def test_no_toca_subtemas_legitimos(self):
        grupos = [
            _g(1, "La economía circular comienza en la cocina",
               "Estudio sobre recuperación de aceite de cocina usado en Barranquilla."),
            _g(2, "Obras de manejo ambiental no dan espera en ciénaga del Totumo",
               "El biólogo docente explicó la recuperación de la ciénaga."),
        ]
        etiquetas = {
            1: {"sub_tema": "Recuperación de aceite de cocina", "tono": "Positivo"},
            2: {"sub_tema": "Recuperación de ciénaga", "tono": "Neutro"},
        }
        self.assertEqual(reparar_subtemas_ajenos(grupos, etiquetas), 0)
        self.assertEqual(etiquetas[1]["sub_tema"], "Recuperación de aceite de cocina")

    def test_no_toca_si_no_hay_otro_grupo_que_lo_explique(self):
        grupos = [_g(1, "Foro de periodismo científico en Barranquilla",
                        "Tercer foro de periodismo científico organizado por la universidad.")]
        etiquetas = {1: {"sub_tema": "Foro de periodismo científico", "tono": "Neutro"}}
        self.assertEqual(reparar_subtemas_ajenos(grupos, etiquetas), 0)


class TestGuardaPositivaParticipio(unittest.TestCase):
    def _corre(self, titulo, contexto):
        grupos = [_g(1, titulo, contexto)]
        etiquetas = {1: {"sub_tema": "x", "tono": "Neutro"}}
        corr = aplicar_guarda_positiva(grupos, etiquetas, BRAND, ALIASES)
        return etiquetas[1]["tono"], corr

    def test_organizado_por_la_marca_sube(self):
        tono, _ = self._corre(
            "El poder de la información: Unisimón analiza el rol de los medios",
            "El encuentro, organizado por la Universidad Simón Bolívar, se realizará "
            "de 2:00 a 6:00 p. en la Casa de la Cultura de Unisimón, La Perla.")
        self.assertEqual(tono, "Positivo")

    def test_organizado_por_otro_no_sube(self):
        tono, _ = self._corre(
            "Feria del libro",
            "El encuentro, organizado por la Alcaldía, se realizará en la plaza.")
        self.assertEqual(tono, "Neutro")

    def test_organizado_sin_evento_no_sube(self):
        tono, _ = self._corre(
            "Informe anual",
            "El informe fue organizado por la Universidad Simón Bolívar en capítulos.")
        self.assertEqual(tono, "Neutro")

    def test_realizado_por_la_marca_con_evento_sube(self):
        tono, _ = self._corre(
            "Congreso de salud",
            "El congreso fue realizado por la Universidad Simón Bolívar con invitados internacionales.")
        self.assertEqual(tono, "Positivo")


class TestGuardaPositivaAlmaMater(unittest.TestCase):
    def _corre(self, titulo, contexto):
        grupos = [_g(1, titulo, contexto)]
        etiquetas = {1: {"sub_tema": "x", "tono": "Neutro"}}
        aplicar_guarda_positiva(grupos, etiquetas, BRAND, ALIASES)
        return etiquetas[1]["tono"]

    def test_participo_la_persona_no_sube(self):
        tono = self._corre(
            "Estefanel Gutiérrez impulsa proyecto de paz barrial",
            "El representante también recordó su formación en la Universidad Simón Bolívar, "
            "donde participó en diferentes espacios y grupos de investigación.")
        self.assertEqual(tono, "Neutro")

    def test_egresado_que_participa_no_sube(self):
        tono = self._corre(
            "Foro de empleo",
            "El egresado de la Universidad Simón Bolívar participó en el foro de empleo juvenil.")
        self.assertEqual(tono, "Neutro")

    def test_accion_propia_no_se_bloquea(self):
        tono = self._corre(
            "Estudio de la universidad",
            "La Universidad Simón Bolívar estudió el fenómeno y presentó los resultados en un foro.")
        self.assertEqual(tono, "Positivo")

    def test_participacion_real_de_la_marca_sube(self):
        tono = self._corre(
            "Mesa de trabajo",
            "La Universidad Simón Bolívar participó en la mesa de trabajo con empresarios.")
        self.assertEqual(tono, "Positivo")


if __name__ == "__main__":
    unittest.main()
