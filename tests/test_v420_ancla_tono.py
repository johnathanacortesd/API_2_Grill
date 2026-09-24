"""Pruebas v4.20: mismo hecho por ancla de persona + evento, y tono consistente.

- `unificar_hecho_por_ancla`: paráfrasis del mismo episodio asistencial
  («Estado de salud de Yamid Amat» = «Hospitalización de Yamid Amat» =
  «Yamid Amat en UCI») se unifican; etapas distintas («Ingreso de Lina
  Tejeiro a clínica» vs «Nacimiento de Gael en Santa Fe»), pacientes
  distintos y pronunciamientos no se tocan. El canon conserva las
  mayúsculas originales (nunca se escribe la forma normalizada).
- `_anclas_en`: nombres de organizaciones con vocabulario de evento
  («Así Vamos en Salud», «Sistema de Salud Colombiano») no generan anclas
  de persona; las personas sí.
- Guarda positiva: episodio de atención al paciente en la marca
  (ancla + evento asistencial + marca como lugar) sube Neutro a Positivo;
  alianza/convenio con la marca también; marca como sede en construcción
  de sujeto («Serena del Mar vivió una gran fiesta») también.
- `aplicar_regla_positivo_incidental`: la mención verdaderamente incidental
  baja a Neutro, pero el episodio asistencial, la alianza propia y el
  evento en sede propia se conservan en Positivo.
- `_marca_protagonista`: el alias corto ambiguo («Santa Fe» equipo de
  fútbol) no cuenta como protagonismo de la Fundación en contexto
  deportivo; la forma larga sí.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_openai_stub = types.ModuleType('openai')
_openai_stub.OpenAI = object
sys.modules.setdefault('openai', _openai_stub)

import analyzer_tono_tema as az

BRAND = 'Fundación Santa Fe'
ALIASES = ('Santa Fe',)
VOCEROS = ('Henry Gallardo',)


def _grupos_etiquetas(pares):
    """pares: [(titulo, subtema, tono)]."""
    grupos, etiquetas = [], {}
    for i, (tit, sub, tono) in enumerate(pares):
        gid = i + 1
        grupos.append({'grupo': gid, 'titulo': tit, 'texto': tit,
                       'contexto': tit})
        etiquetas[gid] = {'tono': tono, 'sub_tema': sub}
    return grupos, etiquetas


class TestUnificarHechoPorAncla(unittest.TestCase):
    def test_parafrasis_mismo_paciente_se_unen(self):
        grupos, et = _grupos_etiquetas([
            ('Yamid Amat sigue hospitalizado', 'Estado de salud de Yamid Amat', 'Neutro'),
            ('Nuevo parte médico de Yamid Amat', 'Estado de salud de Yamid Amat', 'Neutro'),
            ('Yamid Amat fue hospitalizado', 'Hospitalización de Yamid Amat', 'Neutro'),
            ('Yamid Amat, en cuidados intensivos', 'Yamid Amat en UCI', 'Neutro'),
            ('Ingresan a Yamid a la UCI', 'Ingreso a UCI de Yamid', 'Neutro'),
        ])
        n = az.unificar_hecho_por_ancla(grupos, et, BRAND, ALIASES, VOCEROS)
        self.assertGreater(n, 0)
        for gid in et:
            self.assertEqual(et[gid]['sub_tema'], 'Estado de salud de Yamid Amat')

    def test_canon_conserva_mayusculas(self):
        grupos, et = _grupos_etiquetas([
            ('Yamid Amat sigue hospitalizado', 'Estado de salud de Yamid Amat', 'Neutro'),
            ('Yamid Amat fue hospitalizado', 'Hospitalización de Yamid Amat', 'Neutro'),
        ])
        az.unificar_hecho_por_ancla(grupos, et, BRAND, ALIASES, VOCEROS)
        for gid in et:
            sub = et[gid]['sub_tema']
            self.assertTrue(sub[0].isupper(), sub)

    def test_etapas_distintas_no_se_unen(self):
        grupos, et = _grupos_etiquetas([
            ('Ingresan a Lina Tejeiro a la clínica', 'Ingreso de Lina Tejeiro a clínica', 'Neutro'),
            ('Lina Tejeiro confirma el nacimiento de su primogénito Gael',
             'Nacimiento de Gael en Santa Fe', 'Neutro'),
        ])
        n = az.unificar_hecho_por_ancla(grupos, et, BRAND, ALIASES, VOCEROS)
        self.assertEqual(n, 0)
        self.assertEqual(et[1]['sub_tema'], 'Ingreso de Lina Tejeiro a clínica')
        self.assertEqual(et[2]['sub_tema'], 'Nacimiento de Gael en Santa Fe')

    def test_pacientes_distintos_no_se_unen(self):
        grupos, et = _grupos_etiquetas([
            ('Yamid Amat sigue hospitalizado', 'Estado de salud de Yamid Amat', 'Neutro'),
            ('Lina Tejeiro evoluciona bien', 'Estado de salud de Lina Tejeiro', 'Neutro'),
        ])
        n = az.unificar_hecho_por_ancla(grupos, et, BRAND, ALIASES, VOCEROS)
        self.assertEqual(n, 0)

    def test_pronunciamiento_no_es_hecho_clinico(self):
        grupos, et = _grupos_etiquetas([
            ('Yamid Amat sigue hospitalizado', 'Estado de salud de Yamid Amat', 'Neutro'),
            ('Vargas habla sobre Yamid Amat', 'Vargas se pronuncia sobre Yamid', 'Neutro'),
        ])
        n = az.unificar_hecho_por_ancla(grupos, et, BRAND, ALIASES, VOCEROS)
        self.assertEqual(n, 0)

    def test_sin_ancla_no_une(self):
        grupos, et = _grupos_etiquetas([
            ('Balance del trimestre del hospital', 'Resultados financieros', 'Neutro'),
            ('El hospital amplía su planta', 'Infraestructura hospitalaria', 'Neutro'),
        ])
        n = az.unificar_hecho_por_ancla(grupos, et, BRAND, ALIASES, VOCEROS)
        self.assertEqual(n, 0)


class TestAnclasPersona(unittest.TestCase):
    def _tm(self):
        return az._tokens_marca(BRAND, ALIASES, VOCEROS)

    def test_persona_si_es_ancla(self):
        a = az._anclas_en('Yamid Amat está en la Fundación', self._tm())
        self.assertIn(('yamid', 'amat'), a)

    def test_organizacion_con_vocabulario_evento_no_es_ancla(self):
        self.assertEqual(az._anclas_en('Así Vamos en Salud organizó el foro', self._tm()), set())
        self.assertEqual(az._anclas_en('el Sistema de Salud Colombiano', self._tm()), set())

    def test_sustantivo_comun_no_es_paciente(self):
        self.assertEqual(az._anclas_en('Conversatorio de salud mental', self._tm()), set())

    def test_marca_no_es_ancla(self):
        a = az._anclas_en('La Fundación Santa Fe informa', self._tm())
        for toks in a:
            self.assertNotIn('fundacion', toks)


class TestGuardaAtencionPaciente(unittest.TestCase):
    def _tono(self, titulo, ctx, brand=BRAND, aliases=ALIASES, tono_ini='Neutro'):
        g = [{'grupo': 1, 'titulo': titulo, 'texto': ctx, 'contexto': ctx}]
        et = {1: {'tono': tono_ini, 'sub_tema': 'x'}}
        az.aplicar_guarda_positiva(g, et, brand, aliases, VOCEROS)
        return et[1]['tono']

    def test_atencion_en_la_marca_sube(self):
        self.assertEqual(self._tono(
            'Yamid Amat completa una semana en la UCI',
            'Yamid Amat completa una semana en la UCI de la Fundación Santa Fe, '
            'con pronóstico reservado, informó el centro médico.'), 'Positivo')

    def test_nacimiento_en_la_marca_sube(self):
        self.assertEqual(self._tono(
            'Nació Gael en la Santa Fe',
            'Lina Tejeiro dio a luz a su hijo Gael en la Clínica Santa Fe; '
            'madre e hijo están bien.'), 'Positivo')

    def test_atencion_en_otra_institucion_no_sube(self):
        self.assertEqual(self._tono(
            'Paciente en la UCI',
            'Yamid Amat está en la UCI del Hospital San Ignacio, con pronóstico '
            'reservado.'), 'Neutro')

    def test_tragedia_con_atencion_no_sube(self):
        # Paciente fallece en la marca con experto citado: tragedia sin
        # acción -> la guarda no lo sube a Positivo.
        self.assertEqual(self._tono(
            'Fallece paciente en la Fundación',
            'El paciente falleció ayer en la Fundación Santa Fe. El doctor '
            'Pérez, de la Fundación Santa Fe, explicó las causas del deceso.'), 'Neutro')

    def test_critica_dirigida_no_sube(self):
        self.assertEqual(self._tono(
            'Denuncian negligencia en la Fundación',
            'Familiares denuncian presunta negligencia en la atención de Yamid '
            'Amat en la Fundación Santa Fe.'), 'Neutro')

    def test_alianza_con_la_marca_sube(self):
        self.assertEqual(self._tono(
            'Morphy lleva bilingüismo a la primera infancia',
            'La alianza entre Morphy y la Fundación Serena del Mar lleva '
            'bilingüismo de alto impacto a la primera infancia.',
            brand='Serena del Mar', aliases=('Serena',)), 'Positivo')

    def test_de_acuerdo_con_no_es_alianza(self):
        self.assertEqual(self._tono(
            'Cifras de salud mejoran',
            'De acuerdo con la Fundación Santa Fe, las cifras mejoraron este '
            'trimestre.'), 'Neutro')

    def test_sede_en_construccion_de_sujeto_sube(self):
        self.assertEqual(self._tono(
            'Gran fiesta deportiva en Serena',
            'Serena del Mar vivió una gran fiesta deportiva con más de 2.000 '
            'asistentes este fin de semana.',
            brand='Serena del Mar', aliases=('Serena',)), 'Positivo')


class TestPositivoIncidental(unittest.TestCase):
    def _aplica(self, titulo, ctx, tono_ini, brand=BRAND, aliases=ALIASES):
        g = [{'grupo': 1, 'titulo': titulo, 'texto': ctx, 'contexto': ctx}]
        et = {1: {'tono': tono_ini, 'sub_tema': 'x'}}
        az.aplicar_guarda_positiva(g, et, brand, aliases, VOCEROS)
        bajados = az.aplicar_regla_positivo_incidental(g, et, brand, aliases, VOCEROS)
        return et[1]['tono'], bajados

    def test_mencion_incidental_baja(self):
        tono, _ = self._aplica(
            "'Inimaginable': Mhoni Vidente impresionó con predicción",
            'Mhoni Vidente predijo el futuro del país. En otras noticias, el '
            'Hospital Serena del Mar suspendió servicios.', 'Positivo')
        self.assertEqual(tono, 'Neutro')

    def test_episodio_asistencial_no_baja(self):
        tono, _ = self._aplica(
            'Yamid Amat sigue en la UCI',
            'Yamid Amat completa una semana en la UCI de la Fundación Santa Fe, '
            'con pronóstico reservado, informó el centro médico.', 'Positivo')
        self.assertEqual(tono, 'Positivo')

    def test_ranking_otra_institucion_baja(self):
        tono, _ = self._aplica(
            'Fundación Valle del Lili, el único hospital colombiano en el ranking',
            'La Fundación Valle del Lili es el único hospital colombiano en el '
            'ranking mundial de hospitales.', 'Positivo')
        self.assertEqual(tono, 'Neutro')

    def test_alianza_propia_no_baja(self):
        tono, _ = self._aplica(
            'Morphy lleva bilingüismo de alto impacto',
            'La alianza entre Morphy y la Fundación Serena del Mar lleva '
            'bilingüismo a la primera infancia.', 'Positivo',
            brand='Serena del Mar', aliases=('Serena',))
        self.assertEqual(tono, 'Positivo')

    def test_evento_en_sede_propia_no_baja(self):
        tono, _ = self._aplica(
            'Buen balance de la Carrera Píntate',
            'La Carrera Píntate se realizó en Serena del Mar con más de 2.000 '
            'corredores.', 'Positivo',
            brand='Serena del Mar', aliases=('Serena',))
        self.assertEqual(tono, 'Positivo')

    def test_negativo_no_lo_toca(self):
        tono, bajados = self._aplica(
            'Denuncian demoras en la EPS', 'Usuarios denuncian demoras. Se '
            'mencionó a la Fundación Santa Fe entre las clínicas.', 'Negativo')
        self.assertEqual(tono, 'Negativo')
        self.assertEqual(bajados, [])


class TestAliasAmbiguo(unittest.TestCase):
    def _actores(self):
        return [az.nz(x) for x in [BRAND] + list(ALIASES) if x and len(az.nz(x)) >= 4]

    def test_equipo_de_futbol_no_es_protagonista(self):
        # Caso real 61120473/61105640: el «Santa Fe» del titular es el equipo.
        self.assertFalse(az._marca_protagonista(
            'Santa Fe empató en El Campín',
            'Santa Fe empató 1-1 en El Campín. El equipo cardenal no pudo '
            'ganar de local.', self._actores()))

    def test_fundacion_en_titular_si_es_protagonista(self):
        self.assertTrue(az._marca_protagonista(
            'La Fundación Santa Fe abre nueva sede',
            'La Fundación Santa Fe abrió una nueva sede en Bogotá.',
            self._actores()))

    def test_alias_corto_fuera_de_contexto_deportivo_cuenta(self):
        self.assertTrue(az._marca_protagonista(
            'Santa Fe recibe acreditación internacional',
            'La clínica Santa Fe recibió una acreditación internacional por '
            'su calidad. Santa Fe atiende miles de pacientes.',
            self._actores()))


if __name__ == '__main__':
    unittest.main()
