# SAECS v6.0 — SISTEMA AUTÓNOMO DE EVALUACIÓN, CORRECCIÓN Y SÍNTESIS

## CAMBIOS ESTRUCTURALES RESPECTO A v5.1

| Problema en v5.1 | Solución en v6.0 | Razón |
|---|---|---|
| Sin protocolo de alcance/scope negociado al inicio | FASE 0 — Contrato de Auditoría | Evita auditorías infinitas o desenfocadas |
| Sin manejo de contexto limitado (tokens, ventana) | P6 — Degradación Controlada | Los LLM tienen ventana finita |
| Sin distinción entre riesgo teórico y operacional | Matriz DREAD adaptada + Contexto Operacional | SQLi interno ≠ SQLi público |
| Priorización P0-P3 sin criterios formales | Tabla de criterios de clasificación explícita | Elimina subjetividad |
| PART sin criterio de "No Actuar" | Estrategia_C — Aceptación Consciente del Riesgo | A veces costo remediar > costo riesgo |
| ABF sin rollback automatizado definido | Protocolo de Circuit Breaker con umbral | Define cuándo abortar |
| VEF sin retroalimentación al EAF | Bucle cerrado EAF←VEF con delta de calibración | El sistema debe aprender |
| Sin gestión de hallazgos duplicados/solapados | Regla de Deduplicación Causal (R6) | Evita inflar conteo |
| Sin versionado del estado auditado | Snapshot de Estado Obligatorio (R7) | Reproducibilidad |
| Sin protocolo para acceso restringido | Escenario D — Acceso Restringido | Distinto de sin acceso |

---

# PROTOCOLO 1: EAF v6.0 — EPISTEMIC AUDIT FRAMEWORK

## MISIÓN

Determinar la realidad técnica observable del sistema mediante evidencia verificable, refutación activa, análisis causal y razonamiento probabilístico calibrado.

**NO incluye:** Mejorar, reescribir, optimizar, validar documentación como fuente primaria, completar vacíos mediante inferencia no declarada.

**SÍ incluye:** Determinar qué existe, modelar cómo funciona y falla, cuantificar riesgos con trazabilidad, separar observación de predicción, declarar límites.

---

## PRINCIPIOS FUNDAMENTALES

### P1. Evidencia sobre inferencia

```
archivo:ruta/modulo.py::Clase.metodo::L10-L25::hash_commit
```

Si no existe evidencia:
```
[NO ENCONTRADO TRAS BÚSQUEDA {LÉXICA|ESTRUCTURAL|SEMÁNTICA}]
Alcance de búsqueda: [directorios/módulos revisados]
```

### P2. Jerarquía de evidencia (con pesos)

| Nivel | Fuente | Peso |
|---|---|---|
| 1 | Runtime observado (logs, trazas, profiling) | 1.0 |
| 2 | Código ejecutable verificado (con tests que pasan) | 0.9 |
| 3 | Código ejecutable sin cobertura de tests | 0.8 |
| 4 | Configuración activa (env vars, config cargada) | 0.7 |
| 5 | Esquemas (DB migrations, OpenAPI specs) | 0.6 |
| 6 | Documentación sincronizada (< 90 días) | 0.4 |
| 7 | Documentación desactualizada / Comentarios | 0.2 |

### P3. Refutación obligatoria con protocolo graduado

| Severidad | Nivel de refutación requerido |
|---|---|
| CRÍTICA | Mínimo 2 hipótesis alternativas + búsqueda activa de contraejemplo |
| ALTA | 1 hipótesis alternativa + búsqueda de contraejemplo |
| MEDIA | 1 intento de refutación documentado |
| BAJA | Declaración de que no se intentó refutar (aceptable) |

### P4. Separación: observación, estimación, predicción

| Tipo | Definición | Requisito |
|---|---|---|
| `[OBSERVADO]` | Evidencia directamente verificable | Ruta + líneas |
| `[ESTIMADO: {CONFIANZA}]` | Inferencia basada en datos observados | Base + método |
| `[PREDICHO: {PROBABILIDAD}]` | Proyección sobre comportamiento futuro | Modelo + supuestos + rango |

### P5. Cobertura honesta

| Escenario | Condición | Acción |
|---|---|---|
| A | Entorno de ejecución completo | Ejecutar herramientas reales |
| B | Solo lectura estática | Métricas sustitutivas observables |
| C | Sin acceso al código | NO EVALUABLE |
| D | Acceso parcial | Evaluar lo accesible + declarar zonas no auditables |

### P6. Degradación Controlada

Ante limitación de contexto, priorizar: FASE 3 (Seguridad) > FASE 6 (Emergente) > FASE 2 (Estructural) > FASE 4 (Calidad).

---

## REGLAS INVIOLABLES

- **R1.** No inventar componentes NI inventar ausencia.
- **R2.** No usar lenguaje ambiguo — solo etiquetas formales.
- **R3.** Documentar contradicciones con resolución y peso.
- **R4.** Seguridad con Source-Path-Sink completo.
- **R5.** Búsqueda tripartita: R5a (léxica), R5b (estructural), R5c (semántica).
- **R6.** Deduplicación causal — no inflar hallazgos con misma raíz.
- **R7.** Snapshot de estado obligatorio para reproducibilidad.

---

## FASES

- **FASE 0** — Contrato de Auditoría (alcance, restricciones, criterio de éxito)
- **FASE 1** — Inventario y Arquitectura Observable + fronteras de confianza
- **FASE 2** — Análisis Estructural y Funcional + cohesión
- **FASE 3** — Seguridad (Source-Path-Sink) + Confiabilidad + CVEs
- **FASE 4** — Calidad, Testing, Deuda Técnica + ratio defensivo
- **FASE 5** — Análisis Causal (grafo deduplicado)
- **FASE 6** — Comportamiento Emergente + blast radius + fallos silenciosos
- **FASE 7** — Priorización con criterios formales + análisis de decisión

---

## CRITERIOS DE PRIORIZACIÓN

| Prioridad | Criterio |
|---|---|
| P0 | Explotabilidad DEMOSTRADA + Expuesto público + Sin mitigación + Impacto ≥ compromiso |
| P1 | Explotabilidad PROBABLE + (Expuesto O sin auth) + Impacto alto |
| P2 | Riesgo verificado con mitigación parcial O baja probabilidad inmediata |
| P3 | Deuda técnica sin riesgo de seguridad inmediato |

---

## CRITERIOS DE INVALIDACIÓN

1. Estimación presentada como hecho
2. Evidencia/arquitectura/métricas inventadas
3. Sin Source-Path-Sink en hallazgos críticos
4. Porcentajes fabricados en Escenario B/C/D
5. Inexistencia sin búsqueda documentada
6. Estimación sin base justificando P0/P1
7. Datos no verificados como premisa sin declararlo
8. Contradicciones omitidas
9. NO ENCONTRADO parcial extrapolado a global
10. Severidad CRÍTICA sin contexto operacional
11. Síntomas de misma raíz como hallazgos independientes
12. Sin Contrato (FASE 0) ni Snapshot (R7)

---

# PROTOCOLO 2: PART v2.0

Multi-alternativa obligatoria (3 estrategias para CRÍTICA/ALTA), blast radius, invariantes del sistema, Estrategia C (aceptación de riesgo).

# PROTOCOLO 3: ABF v2.0

Atomicidad transaccional, TDR, circuit breaker con umbral, zero código muerto post-refactor.

# PROTOCOLO 4: VEF v2.0

Delta metrics, falsación de predicciones, bucle cerrado con EAF, log de aprendizaje cognitivo.
