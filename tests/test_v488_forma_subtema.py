"""v4.88: reglas de forma del subtema restauradas (se perdieron en v4.74).

- La cola de llamado («…: lo que dijo X», «…: requisitos») no es el hecho:
  el subtema nunca es la persona citada ni la cola de servicio.
- Un subtema no empieza con pretérito con tilde («Informó…», «Inició…»).
- Sin hecho: solo una persona («Ricardo Salazar») o solo tiempo («Fin de
  semana»).
- Titular «sujeto + verbo + resto» se nominaliza («Aprobación de…»).
- Titular largo: sin la atribución («…, advierte X») ni la segunda oración
  coordinada («…baja 15 % y crece la lista de espera»).
- Mismo rótulo con otras mayúsculas o tildes = un solo subtema.
- Un recorte literal del titular tras quitar el verbo sigue siendo copia
  (regla v4.67): se deja a la reformulación del modelo.
- Regla del usuario: ningún subtema lleva comas (salvo la coma decimal de
  una cifra) ni empieza con verbo, número o guion; lo rechazan los dos
  motores y una garantía final lo cumple sobre lo que se exporta.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az  # noqa: E402


class TestColaLlamado(unittest.TestCase):
    def test_subtema_no_es_la_persona_citada(self):
        t = ('La Fundación Santa Fe advierte aumento de casos de dengue en Cartagena: '
             'lo que dijo Ricardo Salazar')
        self.assertEqual(az._subtema_desde_titulo(t), 'Aumento de casos de dengue en Cartagena')

    def test_cola_de_servicio(self):
        r = az._subtema_desde_titulo(
            'Campaña de prevención del cáncer de mama de la Fundación Santa Fe: requisitos')
        self.assertNotEqual(az.nz(r), 'requisitos')
        self.assertIn('cancer de mama', az.nz(r))

    def test_cola_asi_fue_se_conserva(self):
        t = ('De las calles de un barrio pobre del suroccidente de Barranquilla '
             'a una curul en el Congreso: así fue el ascenso político de Estefanel Gutiérrez')
        self.assertEqual(az._subtema_desde_titulo(t), 'Ascenso político de Estefanel Gutiérrez')


class TestFormaSubtema(unittest.TestCase):
    def test_preterito_con_tilde_es_verbo_inicial(self):
        for s in ('Informó este miércoles', 'Conoció el pasado jueves'):
            self.assertTrue(any(p.startswith('verbo_inicial')
                                for p in az.validar(s, 'Neutro', [])), s)
        self.assertFalse(any(p.startswith('verbo_inicial')
                             for p in az.validar('Dominó de la feria en Cali', 'Neutro', [])))

    def test_sin_hecho(self):
        for s in ('Ricardo Salazar', 'Fin de semana', 'Años en Bucaramanga'):
            self.assertTrue(az._es_subtema_sin_hecho(s), s)
        for s in ('Metro de Bogotá', 'Línea 137', 'Estado de salud de Yamid Amat',
                  'Universidad de Atalaya', 'Inauguración de sede en Cali'):
            self.assertFalse(az._es_subtema_sin_hecho(s), s)

    def test_respaldo_no_devuelve_cola_de_cuerpo(self):
        tit = 'La Fundación Santa Fe advierte aumento de casos de dengue en Cartagena: esto se sabe'
        cuerpo = ('La FSFB advirtió un aumento de 445 casos de dengue grave y recomendó '
                  'eliminar criaderos en Cartagena, según informó este miércoles.')
        r = az._subtema_especifico(tit, [cuerpo])
        self.assertTrue(r)
        self.assertFalse(r.lower().startswith(('informó', 'conoció', 'según')))

    def test_nominaliza_el_verbo_del_titular(self):
        r = az._subtema_especifico(
            'El Congreso aprobó la reforma pensional en segundo debate',
            ['El Congreso aprobó la reforma pensional en segundo debate.'])
        self.assertEqual(r, 'Aprobación de reforma pensional en segundo debate')

    def test_solo_preguntas_no_empieza_con_preterito(self):
        r = az._subtema_especifico(
            '¿Cuántos años tiene? ¿Cómo inició?',
            ['Inició en la radio hace veinte años. Hoy dirige el programa matutino de la emisora.'])
        self.assertTrue(r)
        self.assertNotEqual(r, 'Hecho informativo')
        self.assertFalse(r.startswith('Inició'))


class TestTitularLargo(unittest.TestCase):
    def test_atribucion(self):
        r = az._subtema_desde_titulo(
            'Aumentan consultas por ansiedad en jóvenes, advierte la Fundación Santa Fe')
        self.assertNotIn('fundacion', az.nz(r))
        self.assertIn('ansiedad', az.nz(r))

    def test_segunda_oracion_coordinada(self):
        r = az._subtema_desde_titulo('Donación de órganos baja 15 % y crece la lista de espera')
        self.assertIn('donacion de organos', az.nz(r))


class TestRecorteTrasVerbo(unittest.TestCase):
    def test_recorte_literal_tras_preterito_va_al_modelo(self):
        s = '¿Cuántos años tiene Yamid Amat?'
        t = '¿Cuántos años tiene Yamid Amat? Inició en la radio a los 20'
        self.assertTrue(az._recorte_tras_verbo('Radio a los 20', t))
        self.assertEqual(az.reparar_subtema_determinista(s, t), '')

    def test_nominalizacion_no_es_recorte(self):
        t = 'El Congreso aprobó la reforma pensional en segundo debate'
        self.assertFalse(az._recorte_tras_verbo(
            'Aprobación de reforma pensional en segundo debate', t))


class TestUnificarForma(unittest.TestCase):
    def test_mayusculas_y_tildes_un_solo_subtema(self):
        rows = [{'Subtema_IA': 'Declaratoria alerta ambiental Fase 1 suroccidente'},
                {'Subtema_IA': 'Declaratoria alerta ambiental Fase 1 suroccidente'},
                {'Subtema_IA': 'Declaratoria alerta ambiental fase 1 suroccidente'},
                {'Subtema_IA': 'Bar Week 2026 en Bogotá'},
                {'Subtema_IA': '-', 'is_duplicate': True}]
        self.assertEqual(az.unificar_forma_subtemas(rows), 1)
        self.assertEqual(rows[2]['Subtema_IA'], 'Declaratoria alerta ambiental Fase 1 suroccidente')
        self.assertEqual(rows[3]['Subtema_IA'], 'Bar Week 2026 en Bogotá')
        self.assertEqual(rows[4]['Subtema_IA'], '-')

    def test_forma_manual_gana(self):
        rows = [{'Subtema_IA': 'Alerta fase 1'}, {'Subtema_IA': 'Alerta fase 1'},
                {'Subtema_IA': 'Alerta Fase 1', '_correccion_manual': True}]
        az.unificar_forma_subtemas(rows)
        self.assertEqual({r['Subtema_IA'] for r in rows}, {'Alerta Fase 1'})

    def test_rotulos_distintos_no_se_tocan(self):
        rows = [{'Subtema_IA': 'Obras en la calle 13'}, {'Subtema_IA': 'Obras en la calle 26'}]
        self.assertEqual(az.unificar_forma_subtemas(rows), 0)


class TestSoloMarcaNoEsSubtema(unittest.TestCase):
    def test_solo_marca_no_es_subtema(self):
        marca, alias = 'Fundación Santa Fe de Bogotá', ['FSFB', 'Fundación Santa Fe']

        def fila(titulo, cuerpo, sub):
            return {'Título': titulo, 'CuerpoEs': cuerpo, 'Subtema_IA': sub, 'Tono_IA': 'Neutro',
                    'Tema_IA': 'Salud', 'Medio': 'El Tiempo', 'Contexto analizado': cuerpo[:300]}
        c = 'La Fundación Santa Fe de Bogotá confirmó un ciberataque a sus sistemas de citas.'
        rows = [fila('Ciberataque afecta sistemas de la Fundación Santa Fe', c, marca),
                fila('Ciberataque afecta sistemas de la Fundación Santa Fe', c + ' Fin.', marca),
                fila('Hackers atacan la red de la Fundación Santa Fe de Bogotá', c,
                     'Ciberataque a sistemas de citas')]
        az._ROTULOS_LLM.clear()
        az.unificar_por_noticia(rows, {0: 1, 1: 1, 2: 2}, {}, {'titulo': 'Título'},
                                brand=marca, aliases=alias)
        for r in rows:
            self.assertNotEqual(az.nz(r['Subtema_IA']), az.nz(marca))
            self.assertIn('ciberataque', az.nz(r['Subtema_IA']))
        self.assertEqual(rows[0]['Subtema_IA'], rows[1]['Subtema_IA'])


class TestSinComasNumerosNiGuiones(unittest.TestCase):
    MALOS = ('Entrega de becas, en Cali', '15 becas para enfermería en Cali',
             '2026 Feria del libro de Bogotá', '- Feria del libro de Bogotá',
             '– Feria del libro de Bogotá', '• Feria del libro de Bogotá',
             'Bogotá, mi Ciudad, mi Casa', 'Entregó becas en Cali')

    def test_validar_los_rechaza(self):
        duros = {'puntuacion', 'inicio_numero', 'inicio_guion', 'caracter_marcador'}
        for s in self.MALOS:
            pr = {p.split('(')[0] for p in az.validar(s, 'Neutro', [])}
            self.assertTrue(pr & (duros | {'verbo_inicial'}), s)

    def test_haiku_los_rechaza_con_motivo(self):
        import motor_haiku as mh
        for s in self.MALOS:
            self.assertTrue(mh.problemas_subtema(s), s)
        self.assertEqual(mh.problemas_subtema('Feria del libro de Bogotá'), [])
        self.assertFalse([p for p in mh.problemas_subtema('Presupuesto de $42,7 billones para 2027')
                          if 'coma' in p])

    def test_coma_decimal_y_cifra_interna_valen(self):
        for s in ('Presupuesto de $42,7 billones', 'Línea 137 del SITP en Kennedy',
                  'Entrega de 300 viviendas en Bosa'):
            self.assertFalse(az._viola_forma_absoluta(s), s)
            self.assertNotIn('puntuacion', az._problemas_forma_subtema(s), s)

    def test_garantia_final(self):
        rows = [{'Título': 'x', 'Subtema_IA': s} for s in self.MALOS]
        rows += [{'Título': 'x', 'Subtema_IA': 'Entrega de becas, en Cali'},
                 {'Título': 'x', 'Subtema_IA': '-', 'is_duplicate': True},
                 {'Título': 'x', 'Subtema_IA': 'Presupuesto de $42,7 billones'}]
        az.garantizar_forma_subtemas(rows, {'titulo': 'Título'})
        for r in rows:
            s = r['Subtema_IA']
            if s == '-':
                continue
            self.assertFalse(az._viola_forma_absoluta(s), s)
        self.assertEqual(rows[0]['Subtema_IA'], 'Entrega de becas en Cali')
        self.assertEqual(rows[1]['Subtema_IA'], 'Becas para enfermería en Cali')
        self.assertEqual(rows[3]['Subtema_IA'], 'Feria del libro de Bogotá')
        # Mismo rótulo, mismo arreglo (no se parte la noticia).
        self.assertEqual(rows[0]['Subtema_IA'], rows[len(self.MALOS)]['Subtema_IA'])
        self.assertEqual(rows[-2]['Subtema_IA'], '-')
        self.assertEqual(rows[-1]['Subtema_IA'], 'Presupuesto de $42,7 billones')

    def test_subtema_fijo_del_perfil_no_se_toca(self):
        rows = [{'Título': 'x', 'Subtema_IA': 'Plan, Bogotá'}]
        self.assertEqual(az.garantizar_forma_subtemas(rows, {}, ['Plan, Bogotá']), 0)

    def test_la_corrida_completa_la_aplica(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        'tools'))
        import banco_agrupacion as banco
        original = az.garantizar_forma_subtemas
        llamadas = []

        def espia(rows, km, fijos=()):
            for r in rows:
                if not r.get('is_duplicate'):
                    r['Subtema_IA'] = '- 15 ' + str(r.get('Subtema_IA') or '') + ', hoy'
                    break
            llamadas.append(1)
            return original(rows, km, fijos)
        az.garantizar_forma_subtemas = espia
        try:
            rows, _v = banco.generar_dossier(1)
            modelo = banco.ModeloSimulado(1)
            ll = az.llamar_llm
            az.llamar_llm = modelo
            try:
                az.enrich_rows_with_ai(rows, {'titulo': 'Título'}, banco.MARCA, banco.ALIAS,
                                       api_key='simulada', extra={'workers': 1})
            finally:
                az.llamar_llm = ll
        finally:
            az.garantizar_forma_subtemas = original
        self.assertEqual(llamadas, [1])
        for r in rows:
            s = str(r.get('Subtema_IA') or '')
            if s and s != '-':
                self.assertFalse(az._viola_forma_absoluta(s), s)


if __name__ == '__main__':
    unittest.main()
