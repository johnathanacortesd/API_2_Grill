#!/usr/bin/env python3
"""Banco de pruebas de agrupación y subtema (v4.72).

Genera un dossier sintético con VERDAD DE BASE (qué filas son la misma
noticia) y corre el motor completo (`enrich_rows_with_ai`) con un modelo
SIMULADO que se comporta como un LLM real en lo que importa aquí: etiqueta
cada grupo por separado y, para la misma noticia, a veces devuelve otra
redacción del subtema (o un tono distinto). Así se mide lo que el código
determinista tiene que garantizar:

* historias partidas: la misma noticia con más de un Subtema_IA (o Tono_IA,
  o Tema_IA) entre sus filas;
* fusiones indebidas: dos noticias distintas con el mismo Subtema_IA
  (incluye las trampas «mismo molde en otra ciudad» y «mismo asunto, otro
  hecho»);
* tiempo de CPU del código (el modelo simulado no tiene latencia).

Las variaciones por medio imitan dossiers reales de Grill: titular con
prefijo/sufijo del medio, mayúsculas, cola de llamado («: esto se sabe»),
paráfrasis del titular, cuerpo reescrito (oraciones en otro orden,
sinónimos, entradilla de agencia, cierre del medio), resumen de radio/TV de
dos oraciones, mismo cuerpo con otro titular, notas truncadas.

Uso:
    python3 tools/banco_agrupacion.py            # 3 semillas, tabla resumen
    python3 tools/banco_agrupacion.py --semillas 5 --copias 1.5 --detalle

No llama a ninguna API.
"""
import argparse
import json
import os
import random
import re
import sys
import time
import types
import zlib
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if 'openai' not in sys.modules:
    _stub = types.ModuleType('openai')
    _stub.OpenAI = object
    sys.modules['openai'] = _stub

import analyzer_tono_tema as az  # noqa: E402

MARCA = 'Fundación Santa Fe de Bogotá'
ALIAS = ['FSFB', 'Fundación Santa Fe']
VOCEROS = ['Henry Gallardo Lozano']

# ---------------------------------------------------------------------------
# Historias. Cada una: titulares (paráfrasis que usan distintos medios),
# oraciones del hecho (la primera es la entrada), sinónimos para reescribir,
# subtemas que un LLM daría (redacciones distintas del MISMO hecho) y tono.
# Las claves 'trampa' marcan historias que se parecen a otra pero NO son la
# misma noticia.
# ---------------------------------------------------------------------------
HISTORIAS = [
    dict(clave='becas_cartagena',
         titulos=['Fundación Santa Fe abre convocatoria de becas de enfermería en Cartagena',
                  'FSFB lanza becas para estudiar enfermería en Cartagena',
                  'Convocatoria de becas de enfermería de la Fundación Santa Fe llega a Cartagena'],
         frases=['La Fundación Santa Fe de Bogotá abrió una convocatoria de 40 becas para estudiantes de enfermería en Cartagena.',
                 'Las inscripciones estarán abiertas hasta el 30 de noviembre en la página web de la institución.',
                 'Henry Gallardo Lozano, director de la FSFB, explicó que las becas cubren el 100 % de la matrícula.',
                 'Los aspirantes deben acreditar residencia en Bolívar y un promedio académico superior a 4.0.',
                 'La iniciativa se hace en alianza con la Universidad de Cartagena y la Gobernación de Bolívar.',
                 '"Queremos cerrar la brecha de personal de enfermería en la región Caribe", afirmó Gallardo.'],
         subs=['Becas de enfermería en Cartagena', 'Convocatoria de becas de enfermería en Cartagena',
               'Becas para estudiantes de enfermería'],
         tono='Positivo'),
    dict(clave='becas_medellin', trampa='becas_cartagena',
         titulos=['Fundación Santa Fe abre convocatoria de becas de enfermería en Medellín',
                  'FSFB lanza becas para estudiar enfermería en Medellín'],
         frases=['La Fundación Santa Fe de Bogotá abrió una convocatoria de 25 becas para estudiantes de enfermería en Medellín.',
                 'El proceso de selección incluye entrevista y prueba de conocimientos en enero.',
                 'La vocera Paola Cárdenas indicó que la convocatoria se hace con la Universidad de Antioquia.',
                 'Los cupos están dirigidos a jóvenes de estratos 1, 2 y 3 del Valle de Aburrá.'],
         subs=['Becas de enfermería en Medellín', 'Convocatoria de becas de enfermería en Medellín'],
         tono='Positivo'),
    dict(clave='dengue_alerta',
         titulos=['Fundación Santa Fe advierte aumento de casos de dengue grave en el país',
                  'Alerta por dengue grave: FSFB reporta incremento de hospitalizaciones',
                  'Expertos de la Fundación Santa Fe alertan por el alza del dengue grave'],
         frases=['La Fundación Santa Fe de Bogotá advirtió un aumento de 445 casos de dengue grave en lo corrido del año.',
                 'Según la institución, las hospitalizaciones por dengue crecieron 38 % frente a 2025.',
                 'El infectólogo Carlos Álvarez recomendó eliminar los criaderos de mosquitos en tanques y llantas.',
                 'Los departamentos más afectados son Tolima, Huila, Meta y Valle del Cauca.',
                 'La FSFB pidió consultar de inmediato ante fiebre alta, dolor abdominal o sangrado.'],
         subs=['Aumento de casos de dengue grave', 'Alerta por dengue grave', 'Incremento de hospitalizaciones por dengue'],
         tono='Neutro'),
    dict(clave='dengue_vacuna', trampa='dengue_alerta',
         titulos=['Fundación Santa Fe inicia estudio clínico de vacuna contra el dengue',
                  'FSFB participará en ensayo de una nueva vacuna contra el dengue'],
         frases=['La Fundación Santa Fe de Bogotá inició un estudio clínico de fase 3 de una vacuna tetravalente contra el dengue.',
                 'El ensayo reclutará 1.200 voluntarios entre 4 y 60 años en Bogotá, Neiva e Ibagué.',
                 'El investigador principal, Jorge Ruiz, explicó que los resultados se esperan en 2028.',
                 'La vacuna fue desarrollada por un laboratorio brasileño y ya fue aprobada en Brasil.'],
         subs=['Estudio clínico de vacuna contra el dengue', 'Ensayo de vacuna contra el dengue'],
         tono='Positivo'),
    dict(clave='trasplante_record',
         titulos=['Fundación Santa Fe realiza récord de trasplantes de hígado en 2026',
                  'Récord de trasplantes hepáticos en la Fundación Santa Fe',
                  'La FSFB superó los 120 trasplantes de hígado este año'],
         frases=['La Fundación Santa Fe de Bogotá completó 124 trasplantes de hígado en 2026, la cifra más alta de su historia.',
                 'El programa de trasplantes hepáticos cumple 30 años y atiende pacientes de todo el país.',
                 'El cirujano Gilberto Mejía destacó que el 40 % de los trasplantes fueron en niños.',
                 'La institución hizo un llamado a aumentar la donación de órganos en Colombia.',
                 'Actualmente hay 3.200 personas en lista de espera de un órgano en el país.'],
         subs=['Récord de trasplantes de hígado', 'Trasplantes hepáticos en la Fundación', 'Cifra récord de trasplantes hepáticos'],
         tono='Positivo'),
    dict(clave='donacion_organos', trampa='trasplante_record',
         titulos=['Cae la donación de órganos en Colombia: preocupación en hospitales',
                  'Donación de órganos baja 15 % y crece la lista de espera'],
         frases=['La donación de órganos en Colombia cayó 15 % en el primer semestre, según el Instituto Nacional de Salud.',
                 'La Fundación Santa Fe de Bogotá fue uno de los hospitales que expresó preocupación por la cifra.',
                 'El INS atribuye la caída a la negativa de las familias en el momento del fallecimiento.',
                 'Expertos piden campañas de pedagogía sobre la presunción legal de donación.'],
         subs=['Caída de la donación de órganos', 'Disminución de donantes de órganos'],
         tono='Neutro'),
    dict(clave='sede_usaquen',
         titulos=['Fundación Santa Fe inaugura nueva sede de urgencias en Usaquén',
                  'Abre la nueva sede de urgencias de la FSFB en el norte de Bogotá',
                  'Nueva sede de urgencias de la Fundación Santa Fe en Usaquén ya atiende pacientes'],
         frases=['La Fundación Santa Fe de Bogotá inauguró una nueva sede de urgencias en la localidad de Usaquén.',
                 'La sede tiene 60 camas de observación, 8 salas de reanimación y un helipuerto.',
                 'La inversión fue de 85.000 millones de pesos y generará 300 empleos directos.',
                 'Henry Gallardo Lozano señaló que la sede reducirá los tiempos de espera en el norte de la ciudad.',
                 'La alcaldesa local de Usaquén asistió a la apertura junto a directivos del hospital.'],
         subs=['Inauguración de sede de urgencias en Usaquén', 'Nueva sede de urgencias en Usaquén', 'Apertura de sede de urgencias'],
         tono='Positivo'),
    dict(clave='sede_chia', trampa='sede_usaquen',
         titulos=['Fundación Santa Fe inaugura nueva sede de urgencias en Chía',
                  'Abre la nueva sede de urgencias de la FSFB en Chía'],
         frases=['La Fundación Santa Fe de Bogotá inauguró una nueva sede de urgencias en el municipio de Chía.',
                 'La sede atenderá a pacientes de Sabana Centro, con 30 camas y servicio pediátrico.',
                 'El alcalde de Chía, Leonardo Donoso, celebró la llegada del hospital al municipio.',
                 'La obra tardó dos años y contó con licencia de la Secretaría de Salud de Cundinamarca.'],
         subs=['Inauguración de sede de urgencias en Chía', 'Nueva sede de urgencias en Chía'],
         tono='Positivo'),
    dict(clave='demanda_paciente',
         titulos=['Familia demanda a la Fundación Santa Fe por presunta negligencia médica',
                  'Denuncian a la FSFB por la muerte de un paciente tras una cirugía',
                  'Polémica por demanda contra la Fundación Santa Fe por caso de negligencia'],
         frases=['La familia de un paciente de 54 años presentó una demanda contra la Fundación Santa Fe de Bogotá por presunta negligencia médica.',
                 'Según los familiares, el hombre murió por una infección adquirida después de una cirugía de rodilla.',
                 'El abogado de la familia, Mauricio Pava, pidió a la Superintendencia de Salud abrir una investigación.',
                 'La FSFB respondió en un comunicado que el caso está siendo revisado por su comité de ética.',
                 'La institución aseguró que cumplió todos los protocolos de atención.'],
         subs=['Demanda por presunta negligencia médica', 'Denuncia por muerte de paciente', 'Demanda contra la Fundación por negligencia'],
         tono='Negativo'),
    dict(clave='ranking_hospitales',
         titulos=['Fundación Santa Fe, entre los mejores hospitales de América Latina',
                  'Ranking de hospitales: la FSFB ocupa el puesto 3 en la región',
                  'Dos hospitales colombianos en el top 5 de América Latina'],
         frases=['La Fundación Santa Fe de Bogotá ocupó el tercer lugar en el ranking de los mejores hospitales de América Latina de la revista América Economía.',
                 'El escalafón evaluó 58 instituciones de 12 países en seguridad del paciente, talento humano y eficiencia.',
                 'La Fundación Valle del Lili ocupó el segundo lugar y el Hospital Israelita Albert Einstein el primero.',
                 'Henry Gallardo Lozano dijo que el reconocimiento es fruto de 50 años de trabajo.'],
         subs=['Ranking de mejores hospitales de América Latina', 'Escalafón de hospitales de la región', 'Reconocimiento como mejor hospital'],
         tono='Positivo'),
    dict(clave='paro_enfermeras',
         titulos=['Enfermeras de la Fundación Santa Fe protestan por retrasos en pagos',
                  'Plantón de trabajadores de la salud frente a la Fundación Santa Fe'],
         frases=['Un grupo de enfermeras de la Fundación Santa Fe de Bogotá realizó un plantón para exigir el pago de horas extra.',
                 'Las trabajadoras denunciaron retrasos de hasta tres meses en los recargos nocturnos.',
                 'El sindicato Anec aseguró que la protesta continuará si no hay respuesta.',
                 'La dirección de talento humano de la FSFB dijo que los pagos se normalizarán este mes.'],
         subs=['Protesta de enfermeras por pagos', 'Plantón por retraso en pagos', 'Reclamo de enfermeras por recargos'],
         tono='Negativo'),
    dict(clave='cancer_mama',
         titulos=['Fundación Santa Fe lanza campaña gratuita de detección de cáncer de mama',
                  'Mamografías gratis en octubre: así funciona la campaña de la FSFB',
                  'Campaña de prevención del cáncer de mama de la Fundación Santa Fe: requisitos'],
         frases=['La Fundación Santa Fe de Bogotá lanzó una campaña gratuita de detección temprana de cáncer de mama durante octubre.',
                 'Las mujeres entre 40 y 69 años pueden agendar una mamografía sin costo en la sede principal.',
                 'La oncóloga Sandra Díaz recordó que la detección temprana eleva la supervivencia al 90 %.',
                 'Se esperan 2.000 mamografías durante el mes de la sensibilización.'],
         subs=['Campaña de detección de cáncer de mama', 'Mamografías gratuitas', 'Prevención del cáncer de mama'],
         tono='Positivo'),
    dict(clave='ciberataque',
         titulos=['Ciberataque afecta sistemas de la Fundación Santa Fe',
                  'Hackers atacan la red informática de la Fundación Santa Fe de Bogotá',
                  'Fundación Santa Fe confirma incidente de ciberseguridad'],
         frases=['La Fundación Santa Fe de Bogotá confirmó un ciberataque que afectó sus sistemas de agendamiento de citas.',
                 'La institución activó sus protocolos de contingencia y atiende a los pacientes con historias clínicas en papel.',
                 'La Policía Nacional y el Colcert investigan el origen del ataque de tipo ransomware.',
                 'La FSFB aseguró que no hay evidencia de filtración de datos de pacientes.'],
         subs=['Ciberataque a sistemas de la Fundación', 'Incidente de ciberseguridad', 'Ataque informático de tipo ransomware'],
         tono='Negativo'),
    dict(clave='reforma_salud',
         titulos=['Reforma a la salud: hospitales privados piden cambios al proyecto',
                  'Gremio de hospitales critica la reforma a la salud en el Senado'],
         frases=['Los hospitales privados del país pidieron cambios a la reforma a la salud que se debate en el Senado.',
                 'La Asociación Colombiana de Hospitales advirtió que el proyecto pone en riesgo la sostenibilidad del sistema.',
                 'Entre las instituciones firmantes está la Fundación Santa Fe de Bogotá.',
                 'El ministro de Salud respondió que la reforma garantiza el flujo de recursos.'],
         subs=['Reforma a la salud en el Senado', 'Críticas de hospitales a la reforma', 'Debate de la reforma a la salud'],
         tono='Neutro'),
    dict(clave='nobel_medicina', trampa=None,
         titulos=['Investigadora de la Fundación Santa Fe recibe premio internacional de medicina',
                  'Premio a investigadora colombiana por estudio sobre sepsis'],
         frases=['La investigadora Laura Pinzón, de la Fundación Santa Fe de Bogotá, recibió el premio Lancet de investigación clínica.',
                 'El galardón reconoce su estudio sobre la reducción de la mortalidad por sepsis en unidades de cuidados intensivos.',
                 'El estudio siguió a 4.000 pacientes de cinco países durante seis años.',
                 'Pinzón dedicó el premio al equipo de la unidad de cuidado intensivo.'],
         subs=['Premio internacional a investigadora', 'Premio por estudio sobre sepsis'],
         tono='Positivo'),
    dict(clave='alianza_universidad',
         titulos=['Fundación Santa Fe y Universidad de los Andes firman alianza para formar médicos',
                  'Uniandes y FSFB amplían convenio de formación médica'],
         frases=['La Fundación Santa Fe de Bogotá y la Universidad de los Andes firmaron una alianza para ampliar la formación de especialistas médicos.',
                 'El convenio abre 30 nuevos cupos de residencia en pediatría, cirugía y medicina interna.',
                 'El rector de Uniandes destacó que la alianza ya suma 25 años.',
                 'Los residentes harán sus rotaciones en la sede principal y en la nueva sede de Usaquén.'],
         subs=['Alianza para formación de médicos', 'Convenio de formación médica con Uniandes'],
         tono='Positivo'),
    dict(clave='consulta_ia',
         titulos=['Fundación Santa Fe usa inteligencia artificial para leer radiografías',
                  'La FSFB incorpora inteligencia artificial en diagnóstico por imágenes'],
         frases=['La Fundación Santa Fe de Bogotá implementó un sistema de inteligencia artificial para apoyar la lectura de radiografías de tórax.',
                 'La herramienta prioriza los estudios con hallazgos urgentes y reduce el tiempo de reporte a 15 minutos.',
                 'El radiólogo Andrés Rojas explicó que la decisión final siempre es del especialista.',
                 'El sistema fue entrenado con 200.000 imágenes anonimizadas.'],
         subs=['Inteligencia artificial en lectura de radiografías', 'Uso de inteligencia artificial en diagnóstico'],
         tono='Positivo'),
    dict(clave='mencion_incidental',
         titulos=['Accidente en la autopista Norte deja tres heridos',
                  'Choque múltiple en la autopista Norte de Bogotá'],
         frases=['Un choque múltiple en la autopista Norte con calle 170 dejó tres personas heridas en la mañana de este martes.',
                 'Los lesionados fueron trasladados a la Fundación Santa Fe de Bogotá y a la Clínica del Country.',
                 'La Secretaría de Movilidad reportó trancones de más de una hora en el sentido sur-norte.',
                 'Las autoridades investigan si uno de los conductores iba en estado de embriaguez.'],
         subs=['Accidente en la autopista Norte', 'Choque múltiple en la autopista Norte'],
         tono='Neutro'),
    dict(clave='salud_mental',
         titulos=['Aumentan consultas por ansiedad en jóvenes, advierte la Fundación Santa Fe',
                  'Salud mental: crecen las consultas por ansiedad entre jóvenes'],
         frases=['La Fundación Santa Fe de Bogotá reportó un aumento de 27 % en consultas por ansiedad en jóvenes de 15 a 24 años.',
                 'La psiquiatra Natalia Gómez atribuyó el aumento al uso excesivo de redes sociales y a la presión académica.',
                 'La institución abrió una línea de orientación gratuita para familias.',
                 'Los expertos recomiendan pedir ayuda ante cambios bruscos de sueño o apetito.'],
         subs=['Aumento de consultas por ansiedad en jóvenes', 'Salud mental de jóvenes'],
         tono='Neutro'),
    dict(clave='voluntariado',
         titulos=['Fundación Santa Fe lleva brigada de salud a La Guajira',
                  'Brigada médica de la FSFB atiende a comunidades wayuu'],
         frases=['Un equipo de 40 médicos de la Fundación Santa Fe de Bogotá realizó una brigada de salud en Uribia, La Guajira.',
                 'Durante cuatro días se atendieron 1.500 consultas de pediatría, odontología y nutrición.',
                 'La brigada se hizo con apoyo de la Armada Nacional y autoridades indígenas wayuu.',
                 'Se detectaron 120 casos de desnutrición infantil que fueron remitidos al ICBF.'],
         subs=['Brigada de salud en La Guajira', 'Jornada médica con comunidades wayuu'],
         tono='Positivo'),
]

SINONIMOS = {
    'abrió': 'lanzó', 'advirtió': 'alertó sobre', 'aumento': 'incremento', 'explicó': 'aseguró',
    'inauguró': 'abrió', 'señaló': 'indicó', 'destacó': 'resaltó', 'afirmó': 'sostuvo',
    'pidió': 'solicitó', 'reportó': 'informó', 'recomendó': 'aconsejó', 'denunciaron': 'aseguraron',
    'presentó': 'interpuso', 'confirmó': 'reconoció', 'implementó': 'puso en marcha',
    'realizó': 'llevó a cabo', 'firmaron': 'suscribieron', 'recibió': 'obtuvo',
}
MEDIOS = ['El Tiempo', 'El Espectador', 'Semana', 'Caracol Radio', 'Blu Radio', 'RCN Radio',
          'El Heraldo', 'El Colombiano', 'Infobae', 'Pulzo', 'Noticias Caracol', 'La FM',
          'El Universal', 'Portafolio', 'Vanguardia', 'El País Cali']
CIERRES = ['Síguenos en Google News para más noticias.', 'Lea también: las claves de la semana.',
           'Con información de Colprensa.', 'Este contenido fue publicado originalmente en nuestra web.']
COLAS = [': esto se sabe', ': lo que debe saber', ': detalles', '']


def _reescribir(frase, rnd, p=0.5):
    for a, b in SINONIMOS.items():
        if a in frase and rnd.random() < p:
            frase = frase.replace(a, b, 1)
    return frase


def _titulo_variante(h, rnd, medio):
    t = rnd.choice(h['titulos'])
    r = rnd.random()
    if r < 0.12:
        t = t.upper()
    elif r < 0.24:
        t = t + ' - ' + medio
    elif r < 0.32:
        t = 'VIDEO | ' + t
    elif r < 0.42:
        t = t + rnd.choice(COLAS)
    elif r < 0.50:
        t = '«' + t + '»'
    return t


def _cuerpo_variante(h, rnd, modo):
    fr = list(h['frases'])
    if modo == 'radio':
        cuerpo = [_reescribir(fr[0], rnd, 0.7), _reescribir(rnd.choice(fr[1:]), rnd, 0.7)]
    elif modo == 'cable':
        cuerpo = fr[:]
    else:
        resto = fr[1:]
        rnd.shuffle(resto)
        k = rnd.randint(max(2, len(resto) - 2), len(resto))
        cuerpo = [_reescribir(fr[0], rnd)] + [_reescribir(x, rnd) for x in resto[:k]]
    txt = ' '.join(cuerpo)
    if modo != 'radio' and rnd.random() < 0.3:
        txt = 'BOGOTÁ (Colprensa). ' + txt
    if modo != 'radio' and rnd.random() < 0.5:
        txt += ' ' + rnd.choice(CIERRES)
    return txt


def generar_dossier(semilla=1, factor_copias=1.0):
    """Devuelve (rows, verdad) con verdad[i] = clave de la historia."""
    rnd = random.Random(semilla)
    rows, verdad = [], []
    cable_por_historia = {}
    nid = 1000
    for h in HISTORIAS:
        n = max(1, int(round(rnd.choice([1, 2, 3, 4, 5, 6, 8, 10]) * factor_copias)))
        for c in range(n):
            medio = rnd.choice(MEDIOS)
            r = rnd.random()
            modo = 'radio' if r < 0.18 else ('cable' if r < 0.40 else 'reescrita')
            titulo = _titulo_variante(h, rnd, medio)
            if modo == 'cable' and h['clave'] in cable_por_historia and rnd.random() < 0.6:
                cuerpo = cable_por_historia[h['clave']]  # mismo cuerpo, otro titular
            else:
                cuerpo = _cuerpo_variante(h, rnd, modo)
                if modo == 'cable':
                    cable_por_historia.setdefault(h['clave'], cuerpo)
            nid += 1
            rows.append({
                'ID Noticia': nid, 'Título': titulo, 'CuerpoEs': cuerpo,
                'Resumen - Aclaracion': cuerpo[:300], 'Medio': medio,
                'Tipo de Medio': 'Radio' if modo == 'radio' else 'Internet',
            })
            verdad.append(h['clave'])
    orden = list(range(len(rows)))
    rnd.shuffle(orden)
    return [rows[i] for i in orden], [verdad[i] for i in orden]


# ---------------------------------------------------------------------------
# Modelo simulado.
# ---------------------------------------------------------------------------
class ModeloSimulado:
    """Imita al LLM: reconoce la historia de cada grupo por su titular y le
    da UNA de las redacciones posibles del subtema (elegida por el texto del
    grupo, así dos grupos de la misma noticia pueden recibir redacciones
    distintas). El tono es el de la historia con un 15 % de ruido.
    `consolidar` decide cómo responde la consolidación: 'oraculo' agrupa
    bien, 'solos' devuelve cada id solo (el peor caso: todo depende del
    código determinista)."""

    def __init__(self, semilla=1, consolidar='solos', ruido_tono=0.15, ruido_sub=0.0):
        self.semilla = semilla
        self.consolidar = consolidar
        self.ruido_tono = ruido_tono
        # ruido_sub: probabilidad de que el etiquetado devuelva un rótulo
        # malo (recorte del titular o el rótulo de OTRA noticia), como hace
        # un LLM real de vez en cuando. La reparación sí reformula bien.
        self.ruido_sub = ruido_sub
        self.llamadas = Counter()
        self._por_titulo = {}
        for h in HISTORIAS:
            for t in h['titulos']:
                self._por_titulo[az.nz(t)] = h

    def historia(self, texto):
        n = az.nz(texto)
        mejor, largo = None, 0
        for k, h in self._por_titulo.items():
            if k in n and len(k) > largo:
                mejor, largo = h, len(k)
        return mejor

    def _sub(self, h, semilla_txt):
        k = zlib.crc32(('%s|%s' % (self.semilla, semilla_txt)).encode('utf-8'))
        return h['subs'][k % len(h['subs'])]

    def _tono(self, h, semilla_txt):
        k = zlib.crc32(('t%s|%s' % (self.semilla, semilla_txt)).encode('utf-8'))
        if (k % 1000) / 1000.0 < self.ruido_tono:
            return 'Neutro' if h['tono'] != 'Neutro' else 'Positivo'
        return h['tono']

    def __call__(self, cfg, mensajes, json_mode=True, max_tokens=4000, temperatura=0.0,
                 intentos=3, uso=None):
        user = mensajes[-1]['content']
        if uso is not None:
            az._sumar_uso(uso, len(user) // 4, 200)
        if '"hechos"' in user:
            self.llamadas['consolidacion'] += 1
            return self._consolidacion(user)
        if 'Corrige SOLO estos sub-temas' in user:
            self.llamadas['reparacion'] += 1
            return self._etiquetas(user, r'GRUPO id=(\d+)\nTITULAR: ([^\n]*)', reparar=True)
        if 'GRUPO id=' in user and 'CUERPO DE LA NOTICIA' in user:
            self.llamadas['etiquetado'] += 1
            return self._etiquetas(user, r'GRUPO id=(\d+) \(\d+ menciones\)\nTITULAR: ([^\n]*)')
        self.llamadas['otros'] += 1
        return '{}'

    def _etiquetas(self, user, patron, reparar=False):
        res = []
        for gid, tit in re.findall(patron, user):
            h = self.historia(tit)
            if h is None:
                res.append({'id': int(gid), 'sub_tema': 'Hecho de la marca', 'tono': 'Neutro'})
                continue
            sub = self._sub(h, tit + gid)
            if reparar:
                # Una reformulación que no copie el titular, si existe.
                buenos = [x for x in h['subs']
                          if 'copia_titular' not in ' '.join(az.validar(x, 'Neutro', [tit]))]
                sub = buenos[0] if buenos else sub
            elif self.ruido_sub:
                k = zlib.crc32(('r%s|%s|%s' % (self.semilla, tit, gid)).encode('utf-8'))
                r = (k % 1000) / 1000.0
                if r < self.ruido_sub / 2:
                    sub = ' '.join(tit.split()[-5:])  # recorte del titular
                elif r < self.ruido_sub:
                    otra = HISTORIAS[k % len(HISTORIAS)]
                    sub = otra['subs'][0]  # rótulo de otra noticia
            res.append({'id': int(gid), 'sub_tema': sub, 'tono': self._tono(h, tit)})
        return json.dumps({'resultados': res}, ensure_ascii=False)

    def _consolidacion(self, user):
        filas = re.findall(r'id=(\d+) \| SUBTEMA PROPUESTO: «([^»]*)» \| TITULAR: (.*?) \| CONTEXTO', user)
        if self.consolidar == 'oraculo':
            por_h = defaultdict(list)
            for gid, sub, tit in filas:
                h = self.historia(tit)
                por_h[h['clave'] if h else 'x' + gid].append((int(gid), sub))
            hechos = [{'ids': [g for g, _ in v], 'sub_tema': v[0][1]} for v in por_h.values()]
        else:
            hechos = [{'ids': [int(gid)], 'sub_tema': sub} for gid, sub, _t in filas]
        return json.dumps({'hechos': hechos}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Métricas.
# ---------------------------------------------------------------------------
def evaluar(rows, verdad, col='Subtema_IA'):
    por_h = defaultdict(list)
    for r, v in zip(rows, verdad):
        if r.get('is_duplicate'):
            continue
        por_h[v].append(az.nz(r.get(col) or ''))
    partidas = {h: Counter(v) for h, v in por_h.items() if len(set(v)) > 1}
    exceso = sum(len(c) - 1 for c in partidas.values())
    # Fusiones: un mismo rótulo en dos historias distintas.
    por_rot = defaultdict(set)
    for h, v in por_h.items():
        for s in v:
            por_rot[s].add(h)
    fusiones = {s: hs for s, hs in por_rot.items() if s and len(hs) > 1}
    # Calidad: filas cuyo subtema final es uno de los rótulos correctos de
    # su noticia (los que el modelo da cuando acierta).
    gold = {h['clave']: {az.nz(x) for x in h['subs']} for h in HISTORIAS}
    n_ok = sum(1 for h, v in por_h.items() for s in v if s in gold.get(h, ()))
    n_tot = sum(len(v) for v in por_h.values())
    return {'historias': len(por_h), 'multi': sum(1 for v in por_h.values() if len(v) > 1),
            'partidas': len(partidas), 'exceso': exceso, 'fusiones': len(fusiones),
            'distintos': len({s for v in por_h.values() for s in v}),
            'calidad': round(100.0 * n_ok / max(1, n_tot), 1),
            '_partidas': partidas, '_fusiones': fusiones}


def correr(semilla=1, factor=1.0, consolidar='solos', detalle=False, ruido_sub=0.0):
    rows, verdad = generar_dossier(semilla, factor)
    modelo = ModeloSimulado(semilla, consolidar=consolidar, ruido_sub=ruido_sub)
    original = az.llamar_llm
    az.llamar_llm = modelo
    try:
        t0 = time.process_time()
        w0 = time.time()
        az.enrich_rows_with_ai(rows, {'titulo': 'Título'}, MARCA, ALIAS, api_key='simulada',
                               extra={'voceros': VOCEROS, 'workers': 1})
        cpu = time.process_time() - t0
        pared = time.time() - w0
    finally:
        az.llamar_llm = original
    out = {'semilla': semilla, 'filas': len(rows), 'cpu_s': round(cpu, 2), 'pared_s': round(pared, 2),
           'llamadas': dict(modelo.llamadas)}
    for col, nom in (('Subtema_IA', 'sub'), ('Tono_IA', 'tono'), ('Tema_IA', 'tema')):
        m = evaluar(rows, verdad, col)
        out[nom] = {k: v for k, v in m.items() if not k.startswith('_')}
        if detalle and nom == 'sub':
            for h, c in m['_partidas'].items():
                print('  PARTIDA %-20s %s' % (h, dict(c)))
            for s, hs in m['_fusiones'].items():
                print('  FUSION  %-40s %s' % (s, sorted(hs)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--semillas', type=int, default=3)
    ap.add_argument('--copias', type=float, default=1.0)
    ap.add_argument('--consolidar', default='solos', choices=['solos', 'oraculo'])
    ap.add_argument('--ruido', type=float, default=0.0,
                    help='probabilidad de rótulo malo del modelo simulado (0-1)')
    ap.add_argument('--detalle', action='store_true')
    a = ap.parse_args()
    tot = Counter()
    for s in range(1, a.semillas + 1):
        r = correr(s, a.copias, a.consolidar, a.detalle, a.ruido)
        print('semilla %d: %d filas, CPU %.2fs | subtema: %d/%d historias partidas (exceso %d), '
              '%d fusiones, %d rótulos, calidad %.1f%% | tono partido %d | tema partido %d | llamadas %s'
              % (s, r['filas'], r['cpu_s'], r['sub']['partidas'], r['sub']['multi'],
                 r['sub']['exceso'], r['sub']['fusiones'], r['sub']['distintos'],
                 r['sub']['calidad'], r['tono']['partidas'], r['tema']['partidas'], r['llamadas']))
        tot['calidad'] += r['sub']['calidad']
        tot['n'] += 1
        for k in ('partidas', 'exceso', 'fusiones', 'multi'):
            tot['sub_' + k] += r['sub'][k]
        tot['tono_partidas'] += r['tono']['partidas']
        tot['tema_partidas'] += r['tema']['partidas']
        tot['cpu'] += r['cpu_s']
    print('TOTAL: subtema partidas %d/%d (exceso %d), fusiones %d, calidad media %.1f%% | '
          'tono partidas %d | tema partidas %d | CPU %.1fs'
          % (tot['sub_partidas'], tot['sub_multi'], tot['sub_exceso'], tot['sub_fusiones'],
             tot['calidad'] / max(1, tot['n']), tot['tono_partidas'], tot['tema_partidas'],
             tot['cpu']))


if __name__ == '__main__':
    main()
