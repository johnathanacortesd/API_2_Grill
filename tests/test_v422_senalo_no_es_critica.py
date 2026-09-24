"""v4.22: «señaló» (verbo de habla) no es crítica dirigida.

Caso real (dossier Fundación Santa Fe, 2026-09-24): 28 de 32 activaciones de
`_critica_dirigida` venían de `se[nñ]al` calzando «señaló/señala» («La
Fundación Santa Fe señaló que los próximos comunicados…»). Ese falso
positivo vetaba la rama asistencial de la guarda positiva y dejaba 5
episodios de atención al paciente (Yamid Amat en UCI) desprotegidos: la
regla de positivo incidental los bajaba de Positivo a Neutro.

El sustantivo «señalamiento(s)» sí conserva sentido acusatorio y se mantiene.
"""
import unittest

import analyzer_tono_tema as A

BRAND = 'Fundación Santa Fe'
ALIASES = ['Santa Fe', 'Fundación Santa Fe de Bogotá', 'Serena del Mar']

# Texto real del dossier (id 61123213, recortado): parte médico sobre Yamid.
TITULO_YAMID = 'Yamid Amat continúa en estado crítico en una UCI y con pronóstico reservado'
CTX_YAMID = (
    'uno de los periodistas más reconocidos de Colombia, permanece en la Unidad '
    'de Cuidados Intensivos (UCI) de la Fundación Santa Fe de Bogotá, donde se '
    'encuentra en estado crítico y bajo pronóstico reservado, según el más '
    'reciente comunicado emitido por la institución médica a solicitud de su '
    'familia. La Fundación Santa Fe señaló que los próximos comunicados sobre '
    'la condición del comunicador serán emitidos de acuerdo con la evolución '
    'de su estado de salud y únicamente con autorización expresa de sus familiares.'
)


def _grupo(titulo, ctx, tono='Neutro', gid=0):
    g = {'grupo': gid, 'gid': gid, 'idxs': [gid], 'titulo': titulo,
         'texto': ctx, 'contexto': ctx, 'contexto_marca': ctx, 'titulos_alt': []}
    e = {'sub_tema': 'Estado de salud de Yamid Amat', 'tono': tono, 'flags': {}}
    return g, e


class TestSenaloNoEsCritica(unittest.TestCase):
    def test_senalo_habla_no_es_critica(self):
        texto = '%s. %s' % (TITULO_YAMID, CTX_YAMID)
        self.assertFalse(A._critica_dirigida(texto, BRAND, ALIASES))

    def test_senalamientos_sustantivo_si_es_critica(self):
        texto = ('Los señalamientos contra la Fundación Santa Fe por presuntos '
                 'sobrecostos en la obra fueron rechazados por la institución.')
        self.assertTrue(A._critica_dirigida(texto, BRAND, ALIASES))

    def test_critica_genuina_sigue_detectada(self):
        texto = 'Denuncian a la Fundación Santa Fe por mala atención en urgencias.'
        self.assertTrue(A._critica_dirigida(texto, BRAND, ALIASES))

    def test_sin_critica_no_dispara(self):
        texto = ('La Fundación Santa Fe inauguró una nueva torre de consultorios '
                 'con inversión de 40 mil millones.')
        self.assertFalse(A._critica_dirigida(texto, BRAND, ALIASES))

    def test_guarda_positiva_sube_episodio_asistencial_con_senalo(self):
        g, e = _grupo(TITULO_YAMID, CTX_YAMID, tono='Neutro')
        subidos = A.aplicar_guarda_positiva([g], {0: e}, BRAND, ALIASES, [])
        self.assertEqual(subidos, [0])
        self.assertEqual(e['tono'], 'Positivo')

    def test_incidental_no_baja_episodio_asistencial_con_senalo(self):
        g, e = _grupo(TITULO_YAMID, CTX_YAMID, tono='Positivo')
        bajados = A.aplicar_regla_positivo_incidental([g], {0: e}, BRAND, ALIASES, [])
        self.assertEqual(bajados, [])
        self.assertEqual(e['tono'], 'Positivo')

    def test_critica_con_respuesta_genuina_sigue_bajando(self):
        g, e = _grupo(
            'Denuncian sobrecostos en la Fundación Santa Fe',
            'Denuncian a la Fundación Santa Fe por sobrecostos en la obra. '
            'La Fundación Santa Fe respondió con un comunicado negando los cargos.',
            tono='Negativo')
        bajados = A.aplicar_regla_critica_con_respuesta([g], {0: e}, BRAND, ALIASES, [])
        self.assertEqual(bajados, [0])
        self.assertEqual(e['tono'], 'Neutro')

    def test_blanco_de_critica_direccional_intacto(self):
        self.assertTrue(A._marca_blanco_de_critica(
            'Cuestionaron a Fundación Santa Fe por las demoras.', BRAND, ALIASES))
        self.assertFalse(A._marca_blanco_de_critica(
            'La Fundación Santa Fe señaló que habrá nuevos comunicados.', BRAND, ALIASES))


if __name__ == '__main__':
    unittest.main()
