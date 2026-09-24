"""v4.23: columna Prominencia — métrica determinista de presencia de marca.

Reglas del usuario (2026-09-24), solo tres categorías:
- Exclusiva:  4+ menciones en total, o marca en el Título con 2+ en el cuerpo.
- Compartida: 2-3 menciones en total, o marca en el Título con 0-1 en el cuerpo.
- Referencial: 0-1 menciones y sin presencia en el título.

Búsqueda por palabras y similitudes (sin LLM): insensible a mayúsculas y
tildes; "santa fe" = "santa-fe" = "santafe". Los términos vienen de
"Marca o Cliente Principal" y "Alias o términos relacionados".
"""
import unittest

import analyzer_tono_tema as A

BRAND = 'Fundación Santa Fe'
ALIASES = ['Santa Fe', 'Fundación Santa Fe de Bogotá', 'Serena del Mar']


class TestClasificarProminencia(unittest.TestCase):
    def test_cuatro_o_mas_es_exclusiva(self):
        self.assertEqual(A.clasificar_prominencia(0, 4), 'Exclusiva')
        self.assertEqual(A.clasificar_prominencia(0, 7), 'Exclusiva')

    def test_dos_o_tres_es_compartida(self):
        self.assertEqual(A.clasificar_prominencia(0, 2), 'Compartida')
        self.assertEqual(A.clasificar_prominencia(0, 3), 'Compartida')

    def test_cero_o_una_es_referencial(self):
        self.assertEqual(A.clasificar_prominencia(0, 0), 'Referencial')
        self.assertEqual(A.clasificar_prominencia(0, 1), 'Referencial')

    def test_titulo_mas_dos_en_cuerpo_es_exclusiva(self):
        # "2 en CuerpoEs y 1 en Título la haría Exclusiva"
        self.assertEqual(A.clasificar_prominencia(1, 2), 'Exclusiva')
        self.assertEqual(A.clasificar_prominencia(2, 5), 'Exclusiva')

    def test_titulo_con_cero_o_una_en_cuerpo_es_compartida(self):
        # "Si está la marca en el Título también será exclusiva, a menos que
        # en CuerpoEs no sea mencionada o solo una vez" -> Compartida
        self.assertEqual(A.clasificar_prominencia(1, 0), 'Compartida')
        self.assertEqual(A.clasificar_prominencia(1, 1), 'Compartida')


class TestConteoMenciones(unittest.TestCase):
    def test_insensible_a_mayusculas_y_tildes(self):
        n = A.contar_menciones_prominencia(
            'La FUNDACIÓN SANTA FE informó. fundacion santa fe añadió.',
            A._patron_prominencia(A._terminos_prominencia(BRAND, [])))
        self.assertEqual(n, 2)

    def test_variantes_de_separacion(self):
        pat = A._patron_prominencia(A._terminos_prominencia('Santa Fe', []))
        for variante in ('Santa Fe', 'Santa-Fe', 'Santafe', 'SANTAFE'):
            self.assertEqual(
                A.contar_menciones_prominencia('Clínica %s reportó' % variante, pat),
                1, variante)

    def test_termino_largo_no_cuenta_doble(self):
        # "Fundación Santa Fe de Bogotá" = 1 mención, no 2 (con "Santa Fe")
        pat = A._patron_prominencia(A._terminos_prominencia(BRAND, ALIASES))
        n = A.contar_menciones_prominencia(
            'La Fundación Santa Fe de Bogotá emitió un comunicado.', pat)
        self.assertEqual(n, 1)

    def test_alias_separados_por_coma_y_punto_y_coma(self):
        pat = A._patron_prominencia(
            A._terminos_prominencia(BRAND, 'Santa Fe,Serena del Mar;Otra'))
        n = A.contar_menciones_prominencia(
            'Santa Fe y Serena del Mar firmaron. Santa Fe asistió.', pat)
        self.assertEqual(n, 3)

    def test_sin_limites_de_palabra_no_cuenta(self):
        pat = A._patron_prominencia(A._terminos_prominencia('Santa Fe', []))
        # "santafereños" no es mención de la marca
        self.assertEqual(
            A.contar_menciones_prominencia('Los santafereños celebraron.', pat), 0)


class TestCalcularProminencia(unittest.TestCase):
    def test_exclusiva_por_conteo(self):
        cuerpo = ('La Fundación Santa Fe informó. La Fundación Santa Fe añadió. '
                  'Santa Fe confirmó. Serena del Mar apoyó.')
        self.assertEqual(A.calcular_prominencia('Titular neutro', cuerpo, BRAND, ALIASES),
                         'Exclusiva')

    def test_compartida_por_conteo(self):
        cuerpo = 'La Fundación Santa Fe informó. Santa Fe añadió datos.'
        self.assertEqual(A.calcular_prominencia('Titular neutro', cuerpo, BRAND, ALIASES),
                         'Compartida')

    def test_referencial(self):
        self.assertEqual(
            A.calcular_prominencia('Titular neutro', 'Sin la marca aquí.', BRAND, ALIASES),
            'Referencial')
        self.assertEqual(
            A.calcular_prominencia('Titular neutro',
                                   'Solo una vez la Fundación Santa Fe.', BRAND, ALIASES),
            'Referencial')

    def test_titulo_manda_con_respaldo_en_cuerpo(self):
        titulo = 'La Fundación Santa Fe abre nueva sede'
        cuerpo = 'La Fundación Santa Fe invirtió. Santa Fe contrató personal.'
        self.assertEqual(A.calcular_prominencia(titulo, cuerpo, BRAND, ALIASES),
                         'Exclusiva')

    def test_titulo_solo_no_alcanza(self):
        titulo = 'La Fundación Santa Fe abre nueva sede'
        self.assertEqual(A.calcular_prominencia(titulo, 'Cuerpo sin menciones.',
                                               BRAND, ALIASES), 'Compartida')


class TestAplicarProminencia(unittest.TestCase):
    def test_agrega_columna_a_filas(self):
        rows = [
            {'Título': 'La Fundación Santa Fe abre sede',
             'Resumen - Aclaracion': 'La Fundación Santa Fe invirtió. Santa Fe contrató.'},
            {'Título': 'Titular cualquiera',
             'Resumen - Aclaracion': 'Texto sin la marca.'},
        ]
        km = {'titulo': 'Título', 'resumen': 'Resumen - Aclaracion'}
        out = A.aplicar_prominencia(rows, km, BRAND, ALIASES)
        self.assertEqual(out[0]['Prominencia'], 'Exclusiva')
        self.assertEqual(out[1]['Prominencia'], 'Referencial')


if __name__ == '__main__':
    unittest.main()


class TestComunicadoMarca(unittest.TestCase):
    """v4.24: "comunicado de la marca" + 2 o más menciones = Exclusiva."""

    def test_comunicado_con_dos_menciones_es_exclusiva(self):
        cuerpo = ('Según un comunicado de la Fundación Santa Fe, la entidad '
                  'informó. La Fundación Santa Fe añadió detalles.')
        # 2 menciones: sin la regla del comunicado sería Compartida
        self.assertEqual(
            A.calcular_prominencia('Titular neutro', cuerpo, BRAND, ALIASES),
            'Exclusiva')

    def test_comunicado_con_tres_menciones_es_exclusiva(self):
        cuerpo = ('En comunicado de la Santa Fe se anunció. Santa Fe detalló. '
                  'La Santa Fe concluyó.')
        self.assertEqual(
            A.calcular_prominencia('Titular neutro', cuerpo, BRAND, ALIASES),
            'Exclusiva')

    def test_comunicado_con_una_mencion_sigue_referencial(self):
        cuerpo = 'En un comunicado de la Santa Fe se informó del evento.'
        self.assertEqual(
            A.calcular_prominencia('Titular neutro', cuerpo, BRAND, ALIASES),
            'Referencial')

    def test_comunicado_ajeno_no_aplica(self):
        cuerpo = ('La empresa emitió un comunicado de prensa. '
                  'La Fundación Santa Fe asistió. Santa Fe apoyó.')
        # 2 menciones pero el comunicado no es DE la marca -> Compartida
        self.assertEqual(
            A.calcular_prominencia('Titular neutro', cuerpo, BRAND, ALIASES),
            'Compartida')

    def test_comunicado_de_alias_tambien_cuenta(self):
        cuerpo = ('Mediante comunicado de Serena del Mar se informó. '
                  'Serena del Mar amplió la información.')
        self.assertEqual(
            A.calcular_prominencia('Titular neutro', cuerpo, BRAND, ALIASES),
            'Exclusiva')

    def test_clasificar_con_flag(self):
        self.assertEqual(
            A.clasificar_prominencia(0, 2, comunicado_marca=True), 'Exclusiva')
        self.assertEqual(
            A.clasificar_prominencia(0, 3, comunicado_marca=True), 'Exclusiva')
        self.assertEqual(
            A.clasificar_prominencia(0, 1, comunicado_marca=True), 'Referencial')
        self.assertEqual(
            A.clasificar_prominencia(1, 1, comunicado_marca=True), 'Exclusiva')
        # sin flag, el comportamiento v4.23 no cambia
        self.assertEqual(A.clasificar_prominencia(0, 2), 'Compartida')
        self.assertEqual(A.clasificar_prominencia(0, 1), 'Referencial')


class TestTituloAuditor(unittest.TestCase):
    """v4.25: en radio/TV el título lo pone el auditor: no cuenta para
    prominencia (solo el contenido). Solo aplica a prominencia."""

    def test_radio_ignora_titulo(self):
        titulo = 'La Fundación Santa Fe abre nueva sede'      # 1 mención
        cuerpo = 'La Fundación Santa Fe invirtió. Santa Fe contrató.'  # 2
        # sin tipo de medio: título(1) + cuerpo(2) -> Exclusiva
        self.assertEqual(A.calcular_prominencia(titulo, cuerpo, BRAND, ALIASES),
                         'Exclusiva')
        # en radio/TV: solo cuerpo(2) -> Compartida
        for tipo in ('Radio', 'Televisión', 'AM', 'FM', 'Aire', 'Cable',
                     'radio', 'TELEVISIÓN'):
            self.assertEqual(
                A.calcular_prominencia(titulo, cuerpo, BRAND, ALIASES, tipo),
                'Compartida', tipo)

    def test_prensa_e_internet_si_cuentan_titulo(self):
        titulo = 'La Fundación Santa Fe abre nueva sede'
        cuerpo = 'La Fundación Santa Fe invirtió. Santa Fe contrató.'
        for tipo in ('Prensa', 'Internet', 'Revistas', '', None):
            self.assertEqual(
                A.calcular_prominencia(titulo, cuerpo, BRAND, ALIASES, tipo),
                'Exclusiva', tipo)

    def test_comunicado_en_titulo_ignorado_en_radio(self):
        titulo = 'Comunicado de la Fundación Santa Fe'
        cuerpo = 'La Fundación Santa Fe informó. Santa Fe añadió.'
        self.assertEqual(A.calcular_prominencia(titulo, cuerpo, BRAND, ALIASES),
                         'Exclusiva')
        # en radio el título (auditor) no cuenta: cuerpo 2, sin comunicado
        self.assertEqual(
            A.calcular_prominencia(titulo, cuerpo, BRAND, ALIASES, 'Radio'),
            'Compartida')

    def test_aplicar_lee_tipo_de_medio(self):
        rows = [
            {'Título': 'La Fundación Santa Fe abre sede',
             'Resumen - Aclaracion': 'La Fundación Santa Fe invirtió. Santa Fe contrató.',
             'Tipo de Medio': 'Radio'},
            {'Título': 'La Fundación Santa Fe abre sede',
             'Resumen - Aclaracion': 'La Fundación Santa Fe invirtió. Santa Fe contrató.',
             'Tipo de Medio': 'Prensa'},
        ]
        km = {'titulo': 'Título', 'resumen': 'Resumen - Aclaracion',
              'tipodemedio': 'Tipo de Medio'}
        out = A.aplicar_prominencia(rows, km, BRAND, ALIASES)
        self.assertEqual(out[0]['Prominencia'], 'Compartida')
        self.assertEqual(out[1]['Prominencia'], 'Exclusiva')
