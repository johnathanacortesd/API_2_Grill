"""v4.72: una noticia = un subtema, un tono y un tema.

- Pase final por noticia: la misma noticia (mismo grupo, titular comparable,
  CuerpoEs, pasajes literales o texto muy similar) lleva un solo subtema,
  elegido contra la noticia completa; el rótulo del modelo gana a los
  recortes del titular; un rótulo ajeno o solo de marca no gana.
- Agrupación: la guarda de lugar/persona se aplica entre BLOQUES (una nota
  puente sin lugar en el titular ya no encadena «…en Chía» con «…en
  Usaquén»).
- Rótulos de emergencia: sin colas de servicio («requisitos»), sin
  atribución («…, advierte X») y sin entradilla de agencia.
- Modo solo PKL: mismo agrupamiento por evidencia.
- Banco de pruebas: 0 historias partidas y 0 fusiones.
"""
import os
import random
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az  # noqa: E402

KM = {'titulo': 'Título'}
MARCA = 'Fundación Santa Fe de Bogotá'
ALIAS = ['FSFB', 'Fundación Santa Fe']


def fila(titulo, cuerpo, sub='', tono='Neutro', tema='Salud', medio='El Tiempo'):
    return {'Título': titulo, 'CuerpoEs': cuerpo, 'Subtema_IA': sub, 'Tono_IA': tono,
            'Tema_IA': tema, 'Medio': medio, 'Contexto analizado': cuerpo[:300]}


DONACION = ('La donación de órganos en Colombia cayó 15 % en el primer semestre, según el '
            'Instituto Nacional de Salud. La Fundación Santa Fe de Bogotá expresó preocupación '
            'por la cifra. El INS atribuye la caída a la negativa de las familias.')


class TestTituloComparable(unittest.TestCase):
    def test_prefijo_sufijo_cola_comillas(self):
        base = az.titulo_comparable('Fundación Santa Fe abre becas en Cartagena')
        for t in ('VIDEO | Fundación Santa Fe abre becas en Cartagena',
                  '«Fundación Santa Fe abre becas en Cartagena»',
                  'FUNDACIÓN SANTA FE ABRE BECAS EN CARTAGENA',
                  'Fundación Santa Fe abre becas en Cartagena: esto se sabe'):
            self.assertEqual(az.titulo_comparable(t), base, t)
        self.assertEqual(az.titulo_comparable('Fundación Santa Fe abre becas en Cartagena - El Tiempo',
                                              ['El Tiempo']), base)

    def test_entradilla(self):
        self.assertEqual(az.quitar_entradilla('BOGOTÁ (Colprensa). La FSFB abrió.'), 'La FSFB abrió.')
        self.assertEqual(az.quitar_entradilla('MEDELLÍN, 3 nov. (EFE) – El hospital'), 'El hospital')
        self.assertEqual(az.quitar_entradilla('La Fundación abrió'), 'La Fundación abrió')


class TestCtrlEquivalente(unittest.TestCase):
    def test_mismo_filtro(self):
        viejo = lambda s: ''.join(ch for ch in str(s or '') if (ch >= ' ' or ch in '\n\t')  # noqa: E731
                                  and ch not in '￾￿')
        rnd = random.Random(7)
        for _ in range(3000):
            t = ''.join(chr(rnd.choice([rnd.randint(0, 40), rnd.randint(0, 0x3000), 0xfffe, 0xffff]))
                        for _ in range(25))
            self.assertEqual(az.ctrl(t), viejo(t))


class TestRotuloEmergencia(unittest.TestCase):
    def test_cola_de_servicio(self):
        r = az._subtema_desde_titulo(
            'Campaña de prevención del cáncer de mama de la Fundación Santa Fe: requisitos')
        self.assertNotEqual(az.nz(r), 'requisitos')
        self.assertIn('cancer de mama', az.nz(r))

    def test_atribucion(self):
        r = az._subtema_desde_titulo('Aumentan consultas por ansiedad en jóvenes, advierte la Fundación Santa Fe')
        self.assertNotIn('fundacion', az.nz(r))
        self.assertIn('ansiedad', az.nz(r))

    def test_segunda_oracion_coordinada(self):
        r = az._subtema_desde_titulo('Donación de órganos baja 15 % y crece la lista de espera')
        self.assertIn('donacion de organos', az.nz(r))

    def test_cola_asi_fue_se_conserva(self):
        t = ('De las calles de un barrio pobre del suroccidente de Barranquilla '
             'a una curul en el Congreso: así fue el ascenso político de Estefanel Gutiérrez')
        self.assertEqual(az._subtema_desde_titulo(t), 'Ascenso político de Estefanel Gutiérrez')


class TestAgrupacionBloque(unittest.TestCase):
    def test_nota_puente_no_encadena_otra_ciudad(self):
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
        # Nombres distintos en titulares que no se parecen: no hay conflicto.
        self.assertFalse(az.conflicto_entre_bloques(
            ['Alertan por intimidaciones a mujeres cerca de Unisimón'], 'x',
            ['Policía y Gaula blindan dos universidades por acoso a mujeres'], 'y'))
        self.assertTrue(az.conflicto_entre_bloques(
            ['Fundación abre becas de enfermería en Cartagena'], 'convocatoria en Cartagena',
            ['Fundación abre becas de enfermería en Medellín'], 'convocatoria en Medellín'))


class TestUnificarPorNoticia(unittest.TestCase):
    def setUp(self):
        az._ROTULOS_LLM.clear()

    def test_misma_noticia_un_subtema_el_del_modelo(self):
        # El modelo dio una paráfrasis fiel; los pases por fila dejaron
        # recortes del titular en algunas filas.
        az._ROTULOS_LLM.add(az.nz('Disminución de donantes de órganos'))
        rows = [
            fila('Donación de órganos baja 15 % y crece la lista de espera', DONACION,
                 'Lista de espera', 'Neutro'),
            fila('Cae la donación de órganos en Colombia: preocupación en hospitales', DONACION,
                 'Preocupación en hospitales', 'Negativo'),
            fila('Donación de órganos baja 15 % y crece la lista de espera - El Tiempo', DONACION + ' Fin.',
                 'Disminución de donantes de órganos', 'Neutro'),
        ]
        mapa = {0: 1, 1: 1, 2: 2}
        et = {1: {'sub_tema': 'Disminución de donantes de órganos'},
              2: {'sub_tema': 'Disminución de donantes de órganos'}}
        st = az.unificar_por_noticia(rows, mapa, et, KM, originales={1: 'Disminución de donantes de órganos'},
                                     brand=MARCA, aliases=ALIAS)
        self.assertEqual({r['Subtema_IA'] for r in rows}, {'Disminución de donantes de órganos'})
        self.assertEqual(len({r['Tono_IA'] for r in rows}), 1)
        self.assertEqual(st['noticias'], 1)

    def test_rotulo_ajeno_no_gana(self):
        az._ROTULOS_LLM.update({az.nz('Premio internacional a investigadora'),
                                az.nz('Premio por estudio sobre sepsis')})
        acc = ('Un choque múltiple en la autopista Norte con calle 170 dejó tres personas heridas. '
               'Los lesionados fueron trasladados a la Fundación Santa Fe de Bogotá. Las autoridades investigan.')
        premio = ('La investigadora Laura Pinzón, de la Fundación Santa Fe de Bogotá, recibió el premio '
                  'internacional Lancet de investigación clínica por su estudio sobre sepsis.')
        rows = [fila('Choque múltiple en la autopista Norte de Bogotá', acc, 'Premio internacional a investigadora'),
                fila('Accidente en la autopista Norte deja tres heridos', acc + ' Fin.',
                     'Accidente en la autopista Norte'),
                fila('Investigadora de la Fundación Santa Fe recibe premio internacional', premio,
                     'Premio por estudio sobre sepsis')]
        mapa = {0: 1, 1: 1, 2: 2}
        et = {1: {'sub_tema': 'Premio internacional a investigadora'},
              2: {'sub_tema': 'Premio por estudio sobre sepsis'}}
        az.unificar_por_noticia(rows, mapa, et, KM, originales={1: 'Premio internacional a investigadora'},
                                brand=MARCA, aliases=ALIAS)
        self.assertEqual(rows[0]['Subtema_IA'], 'Accidente en la autopista Norte')
        self.assertEqual(rows[1]['Subtema_IA'], 'Accidente en la autopista Norte')

    def test_solo_marca_no_es_subtema(self):
        rows = [fila('Ciberataque afecta sistemas de la Fundación Santa Fe',
                     'La Fundación Santa Fe de Bogotá confirmó un ciberataque a sus sistemas de citas.',
                     'Fundación Santa Fe de Bogotá'),
                fila('Ciberataque afecta sistemas de la Fundación Santa Fe',
                     'La Fundación Santa Fe de Bogotá confirmó un ciberataque a sus sistemas de citas. Fin.',
                     'Fundación Santa Fe de Bogotá'),
                fila('Hackers atacan la red de la Fundación Santa Fe de Bogotá',
                     'La Fundación Santa Fe de Bogotá confirmó un ciberataque a sus sistemas de citas.',
                     'Ciberataque a sistemas de citas')]
        mapa = {0: 1, 1: 1, 2: 2}
        az.unificar_por_noticia(rows, mapa, {}, KM, brand=MARCA, aliases=ALIAS)
        for r in rows:
            self.assertNotEqual(az.nz(r['Subtema_IA']), az.nz(MARCA))
            self.assertIn('ciberataque', az.nz(r['Subtema_IA']))
        self.assertEqual(rows[0]['Subtema_IA'], rows[1]['Subtema_IA'])

    def test_molde_otra_ciudad_se_especifica(self):
        c1 = ('La Fundación Santa Fe de Bogotá inauguró una sede de urgencias en el municipio de Chía. '
              'Atenderá a Sabana Centro.')
        c2 = ('La Fundación Santa Fe de Bogotá inauguró una sede de urgencias en la localidad de Usaquén. '
              'Tiene un helipuerto.')
        az._ROTULOS_LLM.add(az.nz('Apertura de sede de urgencias'))
        rows = [fila('Fundación Santa Fe inaugura sede de urgencias en Chía', c1, 'Apertura de sede de urgencias'),
                fila('Fundación Santa Fe inaugura sede de urgencias en Usaquén', c2, 'Apertura de sede de urgencias')]
        mapa = {0: 1, 1: 2}
        et = {1: {'sub_tema': 'Apertura de sede de urgencias'}, 2: {'sub_tema': 'Apertura de sede de urgencias'}}
        az.unificar_por_noticia(rows, mapa, et, KM, brand=MARCA, aliases=ALIAS)
        self.assertNotEqual(az.nz(rows[0]['Subtema_IA']), az.nz(rows[1]['Subtema_IA']))
        self.assertTrue(any('chia' in az.nz(r['Subtema_IA']) or 'usaquen' in az.nz(r['Subtema_IA'])
                            for r in rows))

    def test_subtema_fijo_gana(self):
        rows = [fila('Alerta temprana por lluvias en Cauca', 'La Defensoría emitió una alerta temprana en Cauca.',
                     'Alertas Defensoría del Pueblo'),
                fila('Alerta temprana por lluvias en Cauca', 'La Defensoría emitió una alerta temprana en Cauca. Fin.',
                     'Riesgo por lluvias en Cauca')]
        az.unificar_por_noticia(rows, {0: 1, 1: 1}, {}, KM, brand='Defensoría del Pueblo',
                                rotulos_compartidos=['Alertas Defensoría del Pueblo'])
        self.assertEqual({r['Subtema_IA'] for r in rows}, {'Alertas Defensoría del Pueblo'})

    def test_tema_unico_por_subtema(self):
        rows = [fila('Ciberataque afecta sistemas de la Fundación Santa Fe', 'Ciberataque a sistemas de citas.',
                     'Ciberataque a sistemas de citas', tema='Seguridad informática'),
                fila('Ciberataque afecta sistemas de la Fundación Santa Fe', 'Ciberataque a sistemas de citas. Fin',
                     'Ciberataque a sistemas de citas', tema='Seguridad informática'),
                fila('Ciberataque afecta sistemas de la Fundación Santa Fe', 'Ciberataque a sistemas de citas!',
                     'Ciberataque a sistemas de citas', tema='Tecnología')]
        az.unificar_por_noticia(rows, {0: 1, 1: 1, 2: 2}, {}, KM, brand=MARCA, aliases=ALIAS)
        self.assertEqual({r['Tema_IA'] for r in rows}, {'Seguridad informática'})


class TestBancoDePruebas(unittest.TestCase):
    def test_banco_sin_partidas_ni_fusiones(self):
        import banco_agrupacion as banco
        for semilla in (1, 2):
            r = banco.correr(semilla)
            self.assertEqual(r['sub']['partidas'], 0, r)
            self.assertEqual(r['sub']['fusiones'], 0, r)
            self.assertEqual(r['tono']['partidas'], 0, r)

    def test_agrupar_noticias_filas_modo_pkl(self):
        import banco_agrupacion as banco
        rows, verdad = banco.generar_dossier(3)
        cm = az.agrupar_noticias_filas(rows, KM, brand=banco.MARCA, aliases=banco.ALIAS)
        from collections import defaultdict
        h2c, c2h = defaultdict(set), defaultdict(set)
        for i, c in cm.items():
            h2c[verdad[i]].add(c)
            c2h[c].add(verdad[i])
        self.assertTrue(all(len(s) == 1 for s in h2c.values()))
        self.assertTrue(all(len(s) == 1 for s in c2h.values()))


if __name__ == '__main__':
    unittest.main()
